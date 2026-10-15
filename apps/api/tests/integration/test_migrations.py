"""Integration tests for the SQLite migration foundation."""

import hashlib
import sqlite3
from pathlib import Path
from typing import cast

import pytest

from lifecase.providers import (
    ANTHROPIC_MESSAGES_MODEL_ID,
    GEMINI_GENERATE_MODEL_ID,
    GROQ_CHAT_MODEL_ID,
    MISTRAL_CHAT_MODEL_ID,
)
from lifecase.storage import (
    MIGRATIONS,
    ForeignKeysDisabledError,
    Fts5UnavailableError,
    Migration,
    MigrationApplyError,
    apply_migrations,
    connect_database,
    require_fts5,
)

EXPECTED_TABLES = {
    "action",
    "action_completion_evidence",
    "action_deadline",
    "action_dependency",
    "action_event",
    "action_progress",
    "action_requirement_claim",
    "agent_run",
    "agent_stage_run",
    "approval_event",
    "approval_request",
    "artifact",
    "case_record",
    "case_transition",
    "claim",
    "claim_confirmation",
    "claim_event",
    "claim_source",
    "deadline",
    "deadline_basis_claim",
    "deadline_event",
    "schema_migration",
    "source",
    "source_extraction",
    "source_segment",
    "source_segment_fts",
    "warranty_preparation_intent",
}

EXPECTED_COLUMNS = {
    "action_progress": [
        "id",
        "action_id",
        "case_id",
        "position",
        "action_version",
        "kind",
        "text",
        "actor",
        "reason",
        "created_at",
    ],
    "approval_event": [
        "id",
        "approval_id",
        "action_id",
        "case_id",
        "event_type",
        "prior_status",
        "new_status",
        "prior_version",
        "new_version",
        "actor",
        "reason",
        "occurred_at",
    ],
    "approval_request": [
        "id",
        "action_id",
        "case_id",
        "action_version",
        "payload_sha256",
        "summary_text",
        "summary_sha256",
        "status",
        "version",
        "requested_by",
        "requested_reason",
        "created_at",
        "updated_at",
    ],
    "action": [
        "id",
        "case_id",
        "kind",
        "status",
        "risk_level",
        "payload_json",
        "payload_sha256",
        "blockers_json",
        "blockers_sha256",
        "version",
        "created_at",
        "updated_at",
    ],
    "action_completion_evidence": [
        "action_id",
        "case_id",
        "position",
        "authority_type",
        "authority_id",
        "authority_version",
        "authority_sha256",
    ],
    "action_deadline": [
        "action_id",
        "case_id",
        "deadline_id",
        "deadline_version",
        "definition_sha256",
    ],
    "action_dependency": [
        "action_id",
        "case_id",
        "position",
        "depends_on_action_id",
        "depends_on_action_version",
    ],
    "action_event": [
        "id",
        "action_id",
        "case_id",
        "event_type",
        "prior_status",
        "new_status",
        "prior_version",
        "new_version",
        "payload_sha256",
        "blockers_sha256",
        "actor",
        "reason",
        "occurred_at",
    ],
    "action_requirement_claim": [
        "action_id",
        "case_id",
        "position",
        "claim_id",
        "claim_version",
        "value_sha256",
        "field",
    ],
    "agent_run": [
        "id",
        "case_id",
        "status",
        "version",
        "current_stage",
        "last_completed_stage",
        "max_calls",
        "max_input_tokens",
        "max_output_tokens",
        "max_total_tokens",
        "max_cost_minor",
        "cost_currency",
        "used_calls",
        "used_input_tokens",
        "used_output_tokens",
        "used_total_tokens",
        "used_cost_minor",
        "reserved_input_tokens",
        "reserved_output_tokens",
        "reserved_total_tokens",
        "reserved_cost_minor",
        "cancellation_requested",
        "created_at",
        "updated_at",
        "completed_at",
        "input_binding_json",
        "input_binding_sha256",
    ],
    "agent_stage_run": [
        "id",
        "run_id",
        "case_id",
        "stage",
        "stage_version",
        "input_schema_version",
        "output_schema_version",
        "checkpoint_schema_version",
        "input_scope_sha256",
        "status",
        "version",
        "max_calls",
        "max_input_tokens",
        "max_output_tokens",
        "max_total_tokens",
        "max_cost_minor",
        "cost_currency",
        "used_calls",
        "used_input_tokens",
        "used_output_tokens",
        "used_total_tokens",
        "used_cost_minor",
        "reserved_input_tokens",
        "reserved_output_tokens",
        "reserved_total_tokens",
        "reserved_cost_minor",
        "repair_count",
        "provider",
        "model_id",
        "latency_ms",
        "checkpoint_json",
        "checkpoint_sha256",
        "error_code",
        "retryable",
        "started_at",
        "updated_at",
        "completed_at",
    ],
    "artifact": [
        "id",
        "case_id",
        "source_id",
        "type",
        "relative_path",
        "sha256",
        "size_bytes",
        "created_by_run",
        "created_at",
    ],
    "case_record": [
        "id",
        "type",
        "template_version",
        "title",
        "goal",
        "status",
        "priority",
        "version",
        "created_at",
        "updated_at",
        "closed_at",
        "template_id",
    ],
    "case_transition": [
        "id",
        "case_id",
        "prior_status",
        "new_status",
        "prior_version",
        "new_version",
        "actor",
        "reason",
        "transitioned_at",
    ],
    "claim": [
        "id",
        "case_id",
        "field",
        "value_json",
        "value_sha256",
        "comparison_sha256",
        "status",
        "confidence",
        "created_by",
        "contradiction",
        "version",
        "created_at",
        "updated_at",
    ],
    "claim_confirmation": [
        "id",
        "claim_id",
        "event_id",
        "claim_version",
        "basis",
        "value_sha256",
        "actor",
        "reason",
        "confirmed_at",
    ],
    "claim_event": [
        "id",
        "claim_id",
        "event_type",
        "prior_status",
        "new_status",
        "prior_value_json",
        "prior_value_sha256",
        "prior_comparison_sha256",
        "new_value_json",
        "new_value_sha256",
        "new_comparison_sha256",
        "prior_version",
        "new_version",
        "actor",
        "reason",
        "occurred_at",
    ],
    "claim_source": [
        "claim_id",
        "source_id",
        "segment_id",
        "segment_text_sha256",
        "quote_start",
        "quote_end",
        "quote_hash",
        "relation",
        "attached_by",
        "attached_at",
    ],
    "schema_migration": ["version", "name", "checksum", "applied_at"],
    "source": [
        "id",
        "case_id",
        "original_name",
        "media_type",
        "sha256",
        "size_bytes",
        "object_path",
        "trust_label",
        "imported_at",
    ],
    "source_segment": [
        "id",
        "source_id",
        "sequence",
        "page",
        "bbox_json",
        "text",
        "extraction_method",
        "confidence",
        "text_sha256",
        "extractor_version",
    ],
    "source_extraction": [
        "source_id",
        "extractor_version",
        "status",
        "segment_count",
        "notices_json",
        "error_code",
        "updated_at",
    ],
    "source_segment_fts": ["source_id", "segment_id", "text", "text_sha256"],
    "warranty_preparation_intent": ["case_id", "intent", "version", "updated_at"],
}


def table_names(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute(
        """
        SELECT name FROM sqlite_master
        WHERE type = 'table'
          AND name NOT LIKE 'sqlite_%'
          AND name NOT LIKE 'source_segment_fts_%'
        """
    ).fetchall()
    return {str(row[0]) for row in rows}


def test_migrates_empty_database_with_required_safety_settings(tmp_path: Path) -> None:
    connection = connect_database(tmp_path / "lifecase.sqlite3")

    try:
        apply_migrations(connection)

        assert table_names(connection) == EXPECTED_TABLES
        for table, expected_columns in EXPECTED_COLUMNS.items():
            columns = connection.execute(f"PRAGMA table_info({table})").fetchall()
            assert [str(column[1]) for column in columns] == expected_columns
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower() == "wal"
        connection.execute("INSERT INTO source_segment_fts(text) VALUES ('synthetic capability')")
        assert (
            connection.execute(
                """
            SELECT bm25(source_segment_fts)
            FROM source_segment_fts
            WHERE source_segment_fts MATCH 'capability'
            """
            ).fetchone()
            is not None
        )

        migrations = connection.execute(
            "SELECT version, name, checksum, applied_at FROM schema_migration"
        ).fetchall()
        assert [tuple(row[:3]) for row in migrations] == [
            (migration.version, migration.name, migration.checksum) for migration in MIGRATIONS
        ]
        assert all(str(row[3]).endswith("Z") for row in migrations)
    finally:
        connection.close()


def test_fts5_capability_failure_is_typed_and_safe(tmp_path: Path) -> None:
    class Fts5UnavailableConnection:
        def execute(self, _statement: str) -> None:
            raise sqlite3.OperationalError("synthetic missing FTS5 detail")

    with pytest.raises(Fts5UnavailableError) as captured:
        require_fts5(cast(sqlite3.Connection, Fts5UnavailableConnection()))

    assert str(captured.value) == "SQLite FTS5 with the trigram tokenizer is required"
    assert str(tmp_path) not in str(captured.value)
    assert "synthetic missing FTS5 detail" not in str(captured.value)


def test_rerunning_migrations_is_idempotent(tmp_path: Path) -> None:
    connection = connect_database(tmp_path / "lifecase.sqlite3")

    try:
        apply_migrations(connection)
        first_records = connection.execute(
            "SELECT version, name, checksum, applied_at FROM schema_migration"
        ).fetchall()

        apply_migrations(connection)

        records = connection.execute(
            "SELECT version, name, checksum, applied_at FROM schema_migration"
        ).fetchall()
        assert len(records) == len(MIGRATIONS)
        assert [tuple(row) for row in records] == [tuple(row) for row in first_records]
    finally:
        connection.close()


def test_agent_run_versioning_migration_preserves_history_and_allows_new_run(
    tmp_path: Path,
) -> None:
    connection = connect_database(tmp_path / "lifecase.sqlite3")
    timestamp = "2026-08-13T00:00:00.000000Z"
    try:
        apply_migrations(connection, MIGRATIONS[:15])
        connection.execute(
            """
            INSERT INTO case_record (
                id, type, template_id, template_version, title, goal, status,
                priority, version, created_at, updated_at
            ) VALUES ('case_versioned', 'warranty', 'warranty_claim', 1,
                      'Synthetic versioned run', 'Test preserved history',
                      'intake', 0, 1, ?, ?)
            """,
            (timestamp, timestamp),
        )
        connection.execute(
            """
            INSERT INTO agent_run (
                id, case_id, status, version, max_calls, max_input_tokens,
                max_output_tokens, max_total_tokens, max_cost_minor, cost_currency,
                created_at, updated_at, completed_at
            ) VALUES ('run_version_1', 'case_versioned', 'completed', 1, 14, 28000,
                      28000, 56000, 100, 'CNY', ?, ?, ?)
            """,
            (timestamp, timestamp, timestamp),
        )

        apply_migrations(connection)
        connection.execute(
            """
            INSERT INTO agent_run (
                id, case_id, status, version, max_calls, max_input_tokens,
                max_output_tokens, max_total_tokens, max_cost_minor, cost_currency,
                created_at, updated_at
            ) VALUES ('run_version_2', 'case_versioned', 'queued', 1, 14, 28000,
                      28000, 56000, 100, 'CNY', ?, ?)
            """,
            (timestamp, timestamp),
        )

        rows = connection.execute(
            "SELECT id, status FROM agent_run WHERE case_id = 'case_versioned' ORDER BY id"
        ).fetchall()
        assert [tuple(row) for row in rows] == [
            ("run_version_1", "completed"),
            ("run_version_2", "queued"),
        ]
    finally:
        connection.close()


def test_retrieval_migration_backfills_existing_segments(tmp_path: Path) -> None:
    connection = connect_database(tmp_path / "lifecase.sqlite3")

    try:
        apply_migrations(connection, MIGRATIONS[:2])
        connection.execute(
            """
            INSERT INTO case_record (
                id, type, template_version, title, goal, status,
                priority, version, created_at, updated_at
            ) VALUES ('case_upgrade', 'warranty', 1, 'Synthetic upgrade',
                      'Test migration', 'intake', 0, 1, ?, ?)
            """,
            ("2026-08-01T00:00:00.000000Z", "2026-08-01T00:00:00.000000Z"),
        )
        connection.execute(
            """
            INSERT INTO source (
                id, case_id, original_name, media_type, sha256,
                size_bytes, object_path, imported_at
            ) VALUES ('source_upgrade', 'case_upgrade', 'synthetic.txt',
                      'text/plain', ?, 0, ?, ?)
            """,
            (
                "0" * 64,
                f"objects/00/00/{'0' * 64}",
                "2026-08-01T00:00:00.000000Z",
            ),
        )
        text = "synthetic migration backfill marker"
        connection.execute(
            """
            INSERT INTO source_segment (
                id, source_id, sequence, page, bbox_json, text,
                extraction_method, confidence, text_sha256, extractor_version
            ) VALUES ('segment_upgrade', 'source_upgrade', 0, NULL, NULL, ?,
                      'direct', 1.0, ?, 'synthetic.v1')
            """,
            (text, hashlib.sha256(text.encode()).hexdigest()),
        )
        connection.execute(
            """
            INSERT INTO case_record (
                id, type, template_version, title, goal, status,
                priority, version, created_at, updated_at, closed_at
            ) VALUES ('case_legacy_closed', 'warranty', 1, 'Synthetic legacy closed',
                      'Test closure backfill', 'resolved', 0, 2, ?, ?, NULL)
            """,
            ("2026-08-01T00:00:00.000000Z", "2026-08-01T00:02:00.000000Z"),
        )

        apply_migrations(connection)

        row = connection.execute(
            """
            SELECT segment_id FROM source_segment_fts
            WHERE source_segment_fts MATCH 'backfill marker'
            """
        ).fetchone()
        assert row is not None and str(row[0]) == "segment_upgrade"
        closed = connection.execute(
            "SELECT closed_at FROM case_record WHERE id = 'case_legacy_closed'"
        ).fetchone()
        assert closed is not None and str(closed[0]) == "2026-08-01T00:02:00.000000Z"
    finally:
        connection.close()


def test_claim_migration_upgrades_m2_01_data_without_disrupting_retrieval(
    tmp_path: Path,
) -> None:
    connection = connect_database(tmp_path / "lifecase.sqlite3")
    text = "synthetic retained retrieval marker"
    text_sha256 = hashlib.sha256(text.encode()).hexdigest()
    try:
        apply_migrations(connection, MIGRATIONS[:4])
        connection.execute(
            """
            INSERT INTO case_record (
                id, type, template_version, title, goal, status,
                priority, version, created_at, updated_at
            ) VALUES ('case_m2_01', 'warranty', 1, 'Synthetic M2-01 data',
                      'Test additive Claim migration', 'intake', 0, 1, ?, ?)
            """,
            ("2026-08-01T00:00:00.000000Z", "2026-08-01T00:00:00.000000Z"),
        )
        connection.execute(
            """
            INSERT INTO source (
                id, case_id, original_name, media_type, sha256,
                size_bytes, object_path, imported_at
            ) VALUES ('source_m2_01', 'case_m2_01', 'synthetic.txt',
                      'text/plain', ?, 0, ?, ?)
            """,
            (
                "0" * 64,
                f"objects/00/00/{'0' * 64}",
                "2026-08-01T00:00:00.000000Z",
            ),
        )
        connection.execute(
            """
            INSERT INTO source_segment (
                id, source_id, sequence, page, bbox_json, text,
                extraction_method, confidence, text_sha256, extractor_version
            ) VALUES ('segment_m2_01', 'source_m2_01', 0, NULL, NULL, ?,
                      'direct', 1.0, ?, 'synthetic.v1')
            """,
            (text, text_sha256),
        )
        connection.execute(
            """
            INSERT INTO source_segment_fts(rowid, source_id, segment_id, text, text_sha256)
            SELECT rowid, source_id, id, text, text_sha256
            FROM source_segment WHERE id = 'segment_m2_01'
            """
        )

        apply_migrations(connection)

        row = connection.execute(
            """
            SELECT segment_id FROM source_segment_fts
            WHERE source_segment_fts MATCH 'retrieval marker'
            """
        ).fetchone()
        assert row is not None and str(row[0]) == "segment_m2_01"
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM case_record WHERE id = 'case_m2_01'"
            ).fetchone()[0]
            == 1
        )
        assert "claim" in table_names(connection)
    finally:
        connection.close()


def test_template_binding_migration_upgrades_m2_02_data_without_disruption(
    tmp_path: Path,
) -> None:
    connection = connect_database(tmp_path / "lifecase.sqlite3")
    text = "synthetic M2-02 retained template migration marker"
    text_sha256 = hashlib.sha256(text.encode()).hexdigest()
    try:
        apply_migrations(connection, MIGRATIONS[:5])
        connection.execute(
            """
            INSERT INTO case_record (
                id, type, template_version, title, goal, status,
                priority, version, created_at, updated_at
            ) VALUES ('case_m2_02', 'warranty', 1, 'Synthetic M2-02 data',
                      'Test template binding migration', 'intake', 0, 1, ?, ?)
            """,
            ("2026-08-01T00:00:00.000000Z", "2026-08-01T00:00:00.000000Z"),
        )
        connection.execute(
            """
            INSERT INTO claim (
                id, case_id, field, value_json, value_sha256, comparison_sha256,
                status, confidence, created_by, contradiction, version,
                created_at, updated_at
            ) VALUES ('claim_m2_02', 'case_m2_02', 'order_number', '"SYN-1"',
                      ?, ?, 'confirmed', 1.0, 'user', 0, 1, ?, ?)
            """,
            (
                hashlib.sha256(b'"SYN-1"').hexdigest(),
                hashlib.sha256(b'"syn-1"').hexdigest(),
                "2026-08-01T00:00:00.000000Z",
                "2026-08-01T00:00:00.000000Z",
            ),
        )
        connection.execute(
            """
            INSERT INTO source (
                id, case_id, original_name, media_type, sha256,
                size_bytes, object_path, imported_at
            ) VALUES ('source_m2_02', 'case_m2_02', 'synthetic.txt',
                      'text/plain', ?, 0, ?, ?)
            """,
            (
                "0" * 64,
                f"objects/00/00/{'0' * 64}",
                "2026-08-01T00:00:00.000000Z",
            ),
        )
        connection.execute(
            """
            INSERT INTO source_segment (
                id, source_id, sequence, page, bbox_json, text,
                extraction_method, confidence, text_sha256, extractor_version
            ) VALUES ('segment_m2_02', 'source_m2_02', 0, NULL, NULL, ?,
                      'direct', 1.0, ?, 'synthetic.v1')
            """,
            (text, text_sha256),
        )
        connection.execute(
            """
            INSERT INTO source_segment_fts(rowid, source_id, segment_id, text, text_sha256)
            SELECT rowid, source_id, id, text, text_sha256
            FROM source_segment WHERE id = 'segment_m2_02'
            """
        )

        apply_migrations(connection)

        case = connection.execute(
            "SELECT type, template_id, template_version FROM case_record WHERE id = 'case_m2_02'"
        ).fetchone()
        assert tuple(case) == ("warranty", "warranty_claim", 1)
        assert (
            connection.execute("SELECT COUNT(*) FROM claim WHERE id = 'claim_m2_02'").fetchone()[0]
            == 1
        )
        row = connection.execute(
            """
            SELECT segment_id FROM source_segment_fts
            WHERE source_segment_fts MATCH 'template migration marker'
            """
        ).fetchone()
        assert row is not None and str(row[0]) == "segment_m2_02"
    finally:
        connection.close()


def test_agent_run_migration_upgrades_m3_01_data_without_disruption(
    tmp_path: Path,
) -> None:
    connection = connect_database(tmp_path / "lifecase.sqlite3")
    text = "synthetic M3-01 retained agent migration marker"
    text_sha256 = hashlib.sha256(text.encode()).hexdigest()
    timestamp = "2026-08-02T00:00:00.000000Z"
    try:
        apply_migrations(connection, MIGRATIONS[:6])
        connection.execute(
            """
            INSERT INTO case_record (
                id, type, template_id, template_version, title, goal, status,
                priority, version, created_at, updated_at
            ) VALUES ('case_m3_01', 'warranty', 'warranty_claim', 1,
                      'Synthetic M3-01 data', 'Test agent migration',
                      'intake', 0, 1, ?, ?)
            """,
            (timestamp, timestamp),
        )
        connection.execute(
            """
            INSERT INTO claim (
                id, case_id, field, value_json, value_sha256, comparison_sha256,
                status, confidence, created_by, contradiction, version,
                created_at, updated_at
            ) VALUES ('claim_m3_01', 'case_m3_01', 'order_number', '"SYN-2"',
                      ?, ?, 'confirmed', 1.0, 'user', 0, 1, ?, ?)
            """,
            (
                hashlib.sha256(b'"SYN-2"').hexdigest(),
                hashlib.sha256(b'"syn-2"').hexdigest(),
                timestamp,
                timestamp,
            ),
        )
        connection.execute(
            """
            INSERT INTO source (
                id, case_id, original_name, media_type, sha256,
                size_bytes, object_path, imported_at
            ) VALUES ('source_m3_01', 'case_m3_01', 'synthetic.txt',
                      'text/plain', ?, 0, ?, ?)
            """,
            ("0" * 64, f"objects/00/00/{'0' * 64}", timestamp),
        )
        connection.execute(
            """
            INSERT INTO source_segment (
                id, source_id, sequence, page, bbox_json, text,
                extraction_method, confidence, text_sha256, extractor_version
            ) VALUES ('segment_m3_01', 'source_m3_01', 0, NULL, NULL, ?,
                      'direct', 1.0, ?, 'synthetic.v1')
            """,
            (text, text_sha256),
        )
        connection.execute(
            """
            INSERT INTO source_segment_fts(
                rowid, source_id, segment_id, text, text_sha256
            )
            SELECT rowid, source_id, id, text, text_sha256
            FROM source_segment WHERE id = 'segment_m3_01'
            """
        )

        apply_migrations(connection)

        assert (
            connection.execute(
                "SELECT COUNT(*) FROM case_record WHERE id = 'case_m3_01'"
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute("SELECT COUNT(*) FROM claim WHERE id = 'claim_m3_01'").fetchone()[0]
            == 1
        )
        row = connection.execute(
            """
            SELECT segment_id FROM source_segment_fts
            WHERE source_segment_fts MATCH 'agent migration marker'
            """
        ).fetchone()
        assert row is not None and str(row[0]) == "segment_m3_01"
        assert connection.execute("SELECT COUNT(*) FROM agent_run").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM agent_stage_run").fetchone()[0] == 0
        migration = connection.execute(
            "SELECT name FROM schema_migration WHERE version = 7"
        ).fetchone()
        assert migration is not None
        assert str(migration[0]) == "agent_run_stage_checkpoint_and_budget"
    finally:
        connection.close()


def test_ollama_provider_migration_preserves_stage_rows_and_closes_identity(
    tmp_path: Path,
) -> None:
    connection = connect_database(tmp_path / "lifecase.sqlite3")
    timestamp = "2026-08-10T00:00:00.000000Z"
    try:
        apply_migrations(connection, MIGRATIONS[:10])
        connection.execute(
            """
            INSERT INTO case_record (
                id, type, template_id, template_version, title, goal, status,
                priority, version, created_at, updated_at
            ) VALUES ('case_p1_02', 'warranty', 'warranty_claim', 1,
                      'Synthetic P1-02 data', 'Test Ollama identity migration',
                      'intake', 0, 1, ?, ?)
            """,
            (timestamp, timestamp),
        )
        connection.execute(
            """
            INSERT INTO agent_run (
                id, case_id, status, version, max_calls, max_input_tokens,
                max_output_tokens, max_total_tokens, max_cost_minor, cost_currency,
                created_at, updated_at
            ) VALUES ('run_p1_02', 'case_p1_02', 'checkpointed', 1, 14, 28000,
                      14000, 42000, 100, 'CNY', ?, ?)
            """,
            (timestamp, timestamp),
        )
        connection.execute(
            """
            INSERT INTO agent_stage_run (
                id, run_id, case_id, stage, stage_version, input_schema_version,
                output_schema_version, checkpoint_schema_version, input_scope_sha256,
                status, version, max_calls, max_input_tokens, max_output_tokens,
                max_total_tokens, max_cost_minor, cost_currency, provider, model_id,
                latency_ms, started_at, updated_at, completed_at
            ) VALUES (
                'stage_p1_02', 'run_p1_02', 'case_p1_02', 'extract',
                'extract.v1', 'extract.input.v1', 'extract.output.v1',
                'extract.checkpoint.v1', ?, 'checkpointed', 1, 2, 4000, 2000,
                6000, 10, 'CNY', 'fake', 'lifecase-fake-v1', 1, ?, ?, ?
            )
            """,
            ("0" * 64, timestamp, timestamp, timestamp),
        )

        apply_migrations(connection)

        retained = connection.execute(
            "SELECT provider, model_id FROM agent_stage_run WHERE id = 'stage_p1_02'"
        ).fetchone()
        assert retained is not None
        assert tuple(retained) == ("fake", "lifecase-fake-v1")
        connection.execute(
            "UPDATE agent_stage_run SET provider = 'ollama', model_id = 'qwen3:4b' "
            "WHERE id = 'stage_p1_02'"
        )
        updated = connection.execute(
            "SELECT provider, model_id FROM agent_stage_run WHERE id = 'stage_p1_02'"
        ).fetchone()
        assert updated is not None
        assert tuple(updated) == ("ollama", "qwen3:4b")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE agent_stage_run SET provider = 'unknown' WHERE id = 'stage_p1_02'"
            )
        migration = connection.execute(
            "SELECT name FROM schema_migration WHERE version = 11"
        ).fetchone()
        assert migration is not None
        assert str(migration[0]) == "ollama_provider_identity"
    finally:
        connection.close()


def test_chat_provider_migrations_preserve_stage_rows_and_close_identity(
    tmp_path: Path,
) -> None:
    connection = connect_database(tmp_path / "lifecase.sqlite3")
    timestamp = "2026-08-12T00:00:00.000000Z"
    try:
        apply_migrations(connection, MIGRATIONS[:11])
        connection.execute(
            """
            INSERT INTO case_record (
                id, type, template_id, template_version, title, goal, status,
                priority, version, created_at, updated_at
            ) VALUES ('case_p1_05', 'warranty', 'warranty_claim', 1,
                      'Synthetic P1-05 data', 'Test Groq identity migration',
                      'intake', 0, 1, ?, ?)
            """,
            (timestamp, timestamp),
        )
        connection.execute(
            """
            INSERT INTO agent_run (
                id, case_id, status, version, max_calls, max_input_tokens,
                max_output_tokens, max_total_tokens, max_cost_minor, cost_currency,
                created_at, updated_at
            ) VALUES ('run_p1_05', 'case_p1_05', 'checkpointed', 1, 14, 28000,
                      14000, 42000, 100, 'CNY', ?, ?)
            """,
            (timestamp, timestamp),
        )
        connection.execute(
            """
            INSERT INTO agent_stage_run (
                id, run_id, case_id, stage, stage_version, input_schema_version,
                output_schema_version, checkpoint_schema_version, input_scope_sha256,
                status, version, max_calls, max_input_tokens, max_output_tokens,
                max_total_tokens, max_cost_minor, cost_currency, provider, model_id,
                latency_ms, started_at, updated_at, completed_at
            ) VALUES (
                'stage_p1_05', 'run_p1_05', 'case_p1_05', 'extract',
                'extract.v1', 'extract.input.v1', 'extract.output.v1',
                'extract.checkpoint.v1', ?, 'checkpointed', 1, 2, 4000, 2000,
                6000, 10, 'CNY', 'openai', 'gpt-synthetic', 1, ?, ?, ?
            )
            """,
            ("0" * 64, timestamp, timestamp, timestamp),
        )

        apply_migrations(connection)

        retained = connection.execute(
            "SELECT provider, model_id FROM agent_stage_run WHERE id = 'stage_p1_05'"
        ).fetchone()
        assert retained is not None
        assert tuple(retained) == ("openai", "gpt-synthetic")
        connection.execute(
            "UPDATE agent_stage_run SET provider = 'groq', model_id = ? WHERE id = 'stage_p1_05'",
            (GROQ_CHAT_MODEL_ID,),
        )
        connection.execute(
            "UPDATE agent_stage_run SET provider = 'mistral', model_id = ? "
            "WHERE id = 'stage_p1_05'",
            (MISTRAL_CHAT_MODEL_ID,),
        )
        connection.execute(
            "UPDATE agent_stage_run SET provider = 'deepseek', model_id = ? "
            "WHERE id = 'stage_p1_05'",
            ("deepseek-v4-flash",),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE agent_stage_run SET provider = 'unknown' WHERE id = 'stage_p1_05'"
            )
        migration = connection.execute(
            "SELECT name FROM schema_migration WHERE version = 12"
        ).fetchone()
        assert migration is not None
        assert str(migration[0]) == "groq_provider_identity"
        migration = connection.execute(
            "SELECT name FROM schema_migration WHERE version = 13"
        ).fetchone()
        assert migration is not None
        assert str(migration[0]) == "mistral_provider_identity"
        migration = connection.execute(
            "SELECT name FROM schema_migration WHERE version = 14"
        ).fetchone()
        assert migration is not None
        assert str(migration[0]) == "deepseek_evaluation_provider_identity"
        connection.execute(
            "UPDATE agent_stage_run SET provider = 'gemini', model_id = ? WHERE id = 'stage_p1_05'",
            (GEMINI_GENERATE_MODEL_ID,),
        )
        connection.execute(
            "UPDATE agent_stage_run SET provider = 'anthropic', model_id = ? "
            "WHERE id = 'stage_p1_05'",
            (ANTHROPIC_MESSAGES_MODEL_ID,),
        )
        migration = connection.execute(
            "SELECT name FROM schema_migration WHERE version = 15"
        ).fetchone()
        assert migration is not None
        assert str(migration[0]) == "gemini_anthropic_provider_identity"
    finally:
        connection.close()


def test_action_migration_upgrades_m5_01_deadline_data_without_disruption(
    tmp_path: Path,
) -> None:
    connection = connect_database(tmp_path / "lifecase.sqlite3")
    timestamp = "2026-08-03T00:00:00.000000Z"
    definition_sha256 = "d" * 64
    try:
        apply_migrations(connection, MIGRATIONS[:8])
        connection.execute(
            """
            INSERT INTO case_record (
                id, type, template_id, template_version, title, goal, status,
                priority, version, created_at, updated_at
            ) VALUES ('case_m5_01', 'warranty', 'warranty_claim', 1,
                      'Synthetic M5-01 data', 'Test Action migration',
                      'intake', 0, 1, ?, ?)
            """,
            (timestamp, timestamp),
        )
        connection.execute(
            """
            INSERT INTO deadline (
                id, case_id, kind, date_kind, due_value, timezone, status,
                uncertainty, confidence, rule_id, rule_version,
                rule_parameters_json, rule_parameters_sha256, version,
                definition_sha256, created_at, updated_at
            ) VALUES ('deadline_m5_01', 'case_m5_01', 'suggested', 'local_date',
                      '2026-08-10', 'Asia/Shanghai', 'proposed',
                      'personal_follow_up_preference_not_external_obligation',
                      NULL, NULL, NULL, NULL, NULL, 1, ?, ?, ?)
            """,
            (definition_sha256, timestamp, timestamp),
        )
        connection.execute(
            """
            INSERT INTO deadline_event (
                id, deadline_id, case_id, event_type, prior_status, new_status,
                prior_version, new_version, definition_sha256, actor, reason, occurred_at
            ) VALUES ('deadline_event_m5_01', 'deadline_m5_01', 'case_m5_01',
                      'created', NULL, 'proposed', 0, 1, ?,
                      'local_user', 'Synthetic retained Deadline', ?)
            """,
            (definition_sha256, timestamp),
        )

        apply_migrations(connection)

        retained = connection.execute(
            "SELECT case_id, due_value, definition_sha256 FROM deadline WHERE id = ?",
            ("deadline_m5_01",),
        ).fetchone()
        assert retained is not None
        assert tuple(retained) == ("case_m5_01", "2026-08-10", definition_sha256)
        assert (
            connection.execute("SELECT name FROM schema_migration WHERE version = 9").fetchone()[0]
            == "action_authority_graph_and_history"
        )
        assert "action_event" in table_names(connection)
    finally:
        connection.close()


def test_approval_migration_preserves_m5_02_action_graph_and_history(tmp_path: Path) -> None:
    from lifecase.actions import (
        ActionKind,
        ActionPayload,
        ActionRiskLevel,
        ActionService,
        CreateActionCommand,
    )
    from lifecase.cases import CaseService

    connection = connect_database(tmp_path / "lifecase.sqlite3")
    try:
        apply_migrations(connection, MIGRATIONS[:9])
        case = CaseService(connection).create_case(
            title="Synthetic M5-02 Action",
            goal="Verify approval migration retention",
            template_id="warranty",
        )
        action = ActionService(connection).create_action(
            CreateActionCommand(
                case_id=case.id,
                kind=ActionKind.COLLECT_EVIDENCE,
                risk_level=ActionRiskLevel.B,
                payload=ActionPayload.from_input(
                    ActionKind.COLLECT_EVIDENCE,
                    {"requirement_key": "purchase_date", "requirement_type": "claim"},
                ),
                requirements=(),
                dependencies=(),
                deadline=None,
                actor="local_user",
                reason="Synthetic retained Action",
            )
        )

        apply_migrations(connection)

        retained = ActionService(connection).get_action(action.id)
        assert retained == action
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM action_event WHERE action_id = ?", (action.id,)
            ).fetchone()[0]
            == 1
        )
        action_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'action'"
        ).fetchone()[0]
        assert "approved" in str(action_sql)
        assert {"approval_request", "approval_event"} <= table_names(connection)
    finally:
        connection.close()


def test_rejects_connection_without_foreign_keys(tmp_path: Path) -> None:
    connection = sqlite3.connect(tmp_path / "unsafe.sqlite3", isolation_level=None)

    try:
        with pytest.raises(ForeignKeysDisabledError):
            apply_migrations(connection)

        assert table_names(connection) == set()
    finally:
        connection.close()


def test_foreign_key_constraints_are_enforced(tmp_path: Path) -> None:
    connection = connect_database(tmp_path / "lifecase.sqlite3")

    try:
        apply_migrations(connection)

        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO source (
                    id, case_id, original_name, media_type, sha256,
                    size_bytes, object_path, imported_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "src_missing_case",
                    "case_missing",
                    "example.txt",
                    "text/plain",
                    "0" * 64,
                    0,
                    "objects/00/00/hash",
                    "2026-08-01T00:00:00.000000Z",
                ),
            )
    finally:
        connection.close()


def test_interrupted_migration_rolls_back_and_can_be_repaired(tmp_path: Path) -> None:
    connection = connect_database(tmp_path / "lifecase.sqlite3")
    test_version = MIGRATIONS[-1].version + 1
    interrupted = Migration(
        version=test_version,
        name="interrupted_test_migration",
        statements=(
            "CREATE TABLE partial_write (id INTEGER PRIMARY KEY)",
            "INSERT INTO table_that_does_not_exist (id) VALUES (1)",
        ),
    )

    try:
        apply_migrations(connection)

        with pytest.raises(MigrationApplyError):
            apply_migrations(connection, (*MIGRATIONS, interrupted))

        assert "partial_write" not in table_names(connection)
        assert connection.in_transaction is False
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM schema_migration WHERE version = ?",
                (test_version,),
            ).fetchone()[0]
            == 0
        )

        repaired = Migration(
            version=test_version,
            name="interrupted_test_migration",
            statements=("CREATE TABLE recovered_write (id INTEGER PRIMARY KEY)",),
        )
        apply_migrations(connection, (*MIGRATIONS, repaired))

        assert "recovered_write" in table_names(connection)
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM schema_migration WHERE version = ?",
                (test_version,),
            ).fetchone()[0]
            == 1
        )
    finally:
        connection.close()
