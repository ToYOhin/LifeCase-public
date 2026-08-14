"""M4-02 Case-scoped Evidence Review projection coverage."""

import hashlib
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import lifecase.api.reviews as reviews_api
from lifecase.cases import CaseService
from lifecase.claims import (
    ClaimConfirmationBasis,
    ClaimCreatedBy,
    ClaimService,
    ClaimSourceInput,
    ClaimSourceRelation,
    ClaimStatus,
    ClaimValue,
    ConfirmClaimCommand,
    CreateClaimCommand,
    EvidenceReviewCaseNotFoundError,
    EvidenceReviewIntegrityError,
    EvidenceReviewPersistenceError,
    EvidenceReviewService,
)
from lifecase.config import ApplicationConfig
from lifecase.main import create_app
from lifecase.storage import apply_migrations, connect_database

_NOW = "2026-08-02T00:00:00.000000Z"


def test_review_queue_is_case_scoped_authoritative_and_deterministic(tmp_path: Path) -> None:
    connection = _database(tmp_path)
    service = ClaimService(connection)
    try:
        text = "Synthetic receipt identifies Store A and order SYN-001."
        source = _insert_source(connection, "case_one", "source_one", "segment_one", text)
        settled = service.create_claim(
            _claim_command("case_one", "merchant_or_manufacturer", "Reviewed Store")
        )
        settled = service.confirm_claim(
            ConfirmClaimCommand(
                claim_id=settled.id,
                expected_version=settled.version,
                basis=ClaimConfirmationBasis.USER,
                actor="reviewer_one",
                reason="Synthetic explicit user confirmation",
            )
        )
        first = service.create_claim(
            _claim_command(
                "case_one",
                "merchant_or_manufacturer",
                "Store A",
                source=_excerpt(source, text, "Store A"),
            )
        )
        second = service.create_claim(
            _claim_command("case_one", "merchant_or_manufacturer", "Store B")
        )
        inferred = service.create_claim(
            _claim_command(
                "case_one",
                "product_name",
                "Inferred headphones",
                status=ClaimStatus.INFERRED,
            )
        )
        other_case = service.create_claim(
            _claim_command("case_two", "merchant_or_manufacturer", "Other Case Store")
        )
        before = _snapshot(connection)

        result = EvidenceReviewService(connection).list_case_review("case_one")

        merchant_claims = sorted((first, second), key=lambda claim: (claim.created_at, claim.id))
        assert result.case_id == "case_one"
        assert result.next_claim_id == merchant_claims[0].id
        assert [item.claim.id for item in result.items] == [
            merchant_claims[0].id,
            merchant_claims[1].id,
            inferred.id,
        ]
        assert [item.is_next for item in result.items] == [True, False, False]
        assert other_case.id not in {item.claim.id for item in result.items}
        assert result.items[0].claim.contradiction is True
        assert result.items[1].claim.contradiction is True
        assert result.items[0].settled_claims == (settled,)
        cited_item = next(item for item in result.items if item.claim.id == first.id)
        citation = cited_item.citations[0]
        assert (
            citation.case_id,
            citation.source_id,
            citation.source_name,
            citation.segment_id,
            citation.sequence,
            citation.page,
            citation.text_sha256,
            citation.text,
            citation.quote_text,
            citation.relation,
        ) == (
            "case_one",
            "source_one",
            "synthetic-receipt.txt",
            "segment_one",
            0,
            1,
            source.segment_text_sha256,
            text,
            "Store A",
            ClaimSourceRelation.SUPPORTS,
        )
        assert _snapshot(connection) == before
        assert connection.in_transaction is False
    finally:
        connection.close()


def test_review_allows_source_free_user_candidate_and_empty_case(tmp_path: Path) -> None:
    connection = _database(tmp_path)
    try:
        claim = ClaimService(connection).create_claim(
            _claim_command("case_one", "order_number", "SYN-NO-SOURCE")
        )

        review = EvidenceReviewService(connection).list_case_review("case_one")
        empty = EvidenceReviewService(connection).list_case_review("case_two")

        assert review.next_claim_id == claim.id
        assert review.items[0].citations == ()
        assert review.items[0].settled_claims == ()
        assert empty.items == ()
        assert empty.next_claim_id is None
    finally:
        connection.close()


def test_review_survives_restart_with_identical_order_and_content(tmp_path: Path) -> None:
    database_path = tmp_path / "lifecase.sqlite3"
    connection = _database(tmp_path)
    ClaimService(connection).create_claim(_claim_command("case_one", "purchase_date", "2026-07-01"))
    before = EvidenceReviewService(connection).list_case_review("case_one")
    connection.close()

    restarted = connect_database(database_path)
    try:
        assert EvidenceReviewService(restarted).list_case_review("case_one") == before
    finally:
        restarted.close()


def test_stale_or_cross_case_citation_fails_closed(tmp_path: Path) -> None:
    connection = _database(tmp_path)
    text = "Synthetic authority Store A"
    source = _insert_source(connection, "case_one", "source_one", "segment_one", text)
    other = _insert_source(connection, "case_two", "source_two", "segment_two", text)
    claim = ClaimService(connection).create_claim(
        _claim_command(
            "case_one",
            "merchant_or_manufacturer",
            "Store A",
            source=_excerpt(source, text, "Store A"),
        )
    )
    try:
        connection.execute(
            "UPDATE claim_source SET source_id = ?, segment_id = ? WHERE claim_id = ?",
            (other.source_id, other.segment_id, claim.id),
        )
        with pytest.raises(EvidenceReviewIntegrityError):
            EvidenceReviewService(connection).list_case_review("case_one")

        connection.execute(
            "UPDATE claim_source SET source_id = ?, segment_id = ? WHERE claim_id = ?",
            (source.source_id, source.segment_id, claim.id),
        )
        connection.execute(
            "UPDATE source_segment SET text = 'tampered' WHERE id = ?",
            (source.segment_id,),
        )
        with pytest.raises(EvidenceReviewIntegrityError):
            EvidenceReviewService(connection).list_case_review("case_one")
    finally:
        connection.close()


def test_review_missing_case_and_closed_database_fail_safely(tmp_path: Path) -> None:
    connection = _database(tmp_path)
    service = EvidenceReviewService(connection)
    with pytest.raises(EvidenceReviewCaseNotFoundError):
        service.list_case_review("case_missing")
    connection.close()

    with pytest.raises(EvidenceReviewPersistenceError) as captured:
        service.list_case_review("case_one")

    assert "SELECT" not in str(captured.value)
    assert str(tmp_path) not in str(captured.value)


def test_review_api_reads_authority_then_reuses_claim_confirmation(tmp_path: Path) -> None:
    config, case_id, claim_id = _api_review(tmp_path)
    with TestClient(create_app(config)) as client:
        review = client.get(f"/api/v1/cases/{case_id}/evidence-review")
        confirmed = client.post(
            f"/api/v1/claims/{claim_id}/confirm",
            json={
                "expected_version": 1,
                "basis": "evidence",
                "actor": "reviewer_one",
                "reason": "Reviewed the synthetic receipt",
            },
        )
        refreshed = client.get(f"/api/v1/cases/{case_id}/evidence-review")

    assert review.status_code == 200
    assert review.json()["next_claim_id"] == claim_id
    item = review.json()["items"][0]
    assert item["claim"]["value"] == "Store A"
    assert item["citations"][0]["quote_text"] == "Store A"
    assert "object_path" not in review.text
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "confirmed"
    assert refreshed.status_code == 200
    assert refreshed.json() == {"case_id": case_id, "next_claim_id": None, "items": []}


def test_review_api_maps_missing_integrity_and_storage_errors_safely(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, case_id, _claim_id = _api_review(tmp_path)
    with TestClient(create_app(config)) as client:
        missing = client.get("/api/v1/cases/case_missing/evidence-review")

    def fail_review(*_args: object, **_kwargs: object) -> None:
        raise EvidenceReviewPersistenceError("SELECT private FROM secret_table")

    monkeypatch.setattr(reviews_api.EvidenceReviewService, "list_case_review", fail_review)
    with TestClient(create_app(config)) as client:
        failed = client.get(f"/api/v1/cases/{case_id}/evidence-review")

    assert (missing.status_code, missing.json()["error"]["code"]) == (404, "case_not_found")
    assert (failed.status_code, failed.json()["error"]["code"]) == (
        503,
        "evidence_review_unavailable",
    )
    assert failed.json()["error"]["retryable"] is True
    assert "SELECT private" not in failed.text
    assert "secret_table" not in failed.text


def _database(tmp_path: Path) -> sqlite3.Connection:
    connection = connect_database(tmp_path / "lifecase.sqlite3")
    apply_migrations(connection)
    for case_id in ("case_one", "case_two"):
        connection.execute(
            """
            INSERT INTO case_record (
                id, type, template_id, template_version, title, goal, status,
                priority, version, created_at, updated_at, closed_at
            ) VALUES (?, 'warranty', 'warranty_claim', 1, 'Synthetic review Case',
                      'Review synthetic evidence', 'needs_review', 0, 1, ?, ?, NULL)
            """,
            (case_id, _NOW, _NOW),
        )
    return connection


def _api_review(tmp_path: Path) -> tuple[ApplicationConfig, str, str]:
    config = ApplicationConfig(
        database_path=tmp_path / "runtime" / "lifecase.sqlite3",
        data_root=tmp_path / "runtime" / "data",
        upload_root=tmp_path / "runtime" / "uploads",
    )
    connection = connect_database(config.database_path)
    try:
        apply_migrations(connection)
        case = CaseService(connection).create_case(
            title="Synthetic review API Case",
            goal="Review one synthetic fact",
            template_id="warranty",
        )
        text = "Synthetic receipt names Store A."
        source = _insert_source(connection, case.id, "source_api", "segment_api", text)
        claim = ClaimService(connection).create_claim(
            _claim_command(
                case.id,
                "merchant_or_manufacturer",
                "Store A",
                source=_excerpt(source, text, "Store A"),
            )
        )
        return config, case.id, claim.id
    finally:
        connection.close()


def _insert_source(
    connection: sqlite3.Connection,
    case_id: str,
    source_id: str,
    segment_id: str,
    text: str,
) -> ClaimSourceInput:
    text_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
    connection.execute(
        """
        INSERT INTO source (
            id, case_id, original_name, media_type, sha256,
            size_bytes, object_path, imported_at
        ) VALUES (?, ?, 'synthetic-receipt.txt', 'text/plain', ?, 1, ?, ?)
        """,
        (source_id, case_id, "0" * 64, f"objects/00/00/{'0' * 64}", _NOW),
    )
    connection.execute(
        """
        INSERT INTO source_segment (
            id, source_id, sequence, page, bbox_json, text,
            extraction_method, confidence, text_sha256, extractor_version
        ) VALUES (?, ?, 0, 1, NULL, ?, 'direct', 1.0, ?, 'synthetic.v1')
        """,
        (segment_id, source_id, text, text_sha256),
    )
    return ClaimSourceInput(
        source_id=source_id,
        segment_id=segment_id,
        segment_text_sha256=text_sha256,
        quote_start=0,
        quote_end=1,
        quote_hash=hashlib.sha256(text[:1].encode("utf-8")).hexdigest(),
        relation=ClaimSourceRelation.SUPPORTS,
    )


def _excerpt(source: ClaimSourceInput, text: str, quote: str) -> ClaimSourceInput:
    start = text.index(quote)
    end = start + len(quote)
    return ClaimSourceInput(
        source_id=source.source_id,
        segment_id=source.segment_id,
        segment_text_sha256=source.segment_text_sha256,
        quote_start=start,
        quote_end=end,
        quote_hash=hashlib.sha256(quote.encode("utf-8")).hexdigest(),
        relation=ClaimSourceRelation.SUPPORTS,
    )


def _claim_command(
    case_id: str,
    field: str,
    value: object,
    *,
    status: ClaimStatus = ClaimStatus.CANDIDATE,
    source: ClaimSourceInput | None = None,
) -> CreateClaimCommand:
    return CreateClaimCommand(
        case_id=case_id,
        field=field,
        value=ClaimValue.from_input(value),
        status=status,
        confidence=0.7,
        created_by=ClaimCreatedBy.AGENT,
        actor="agent_run_synthetic",
        reason="Synthetic review candidate",
        sources=(source,) if source is not None else (),
    )


def _snapshot(connection: sqlite3.Connection) -> tuple[tuple[object, ...], ...]:
    tables = (
        "case_record",
        "source",
        "source_segment",
        "source_segment_fts",
        "claim",
        "claim_source",
        "claim_event",
        "claim_confirmation",
        "agent_run",
        "agent_stage_run",
    )
    return tuple(
        tuple(tuple(row) for row in connection.execute(f"SELECT * FROM {table}").fetchall())
        for table in tables
    )
