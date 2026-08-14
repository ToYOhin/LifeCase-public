"""Deterministic Case-scoped Evidence Context construction."""

import hashlib
import sqlite3
from dataclasses import dataclass

from lifecase.evidence.context.errors import (
    EvidenceContextCaseNotFoundError,
    EvidenceContextConfigurationError,
    EvidenceContextError,
    EvidenceContextIntegrityError,
    EvidenceContextPersistenceError,
    EvidenceContextRequestError,
)
from lifecase.evidence.context.models import (
    EvidenceCitation,
    EvidenceContext,
    EvidenceContextItem,
    EvidenceContextMatch,
    EvidenceContextModelError,
    EvidenceContextOrigin,
    EvidenceContextRequest,
    EvidenceTrustLabel,
)
from lifecase.retrieval import (
    RetrievalIndexError,
    RetrievalIntegrityError,
    RetrievalRequestError,
    RetrievalResult,
    RetrievalService,
)


@dataclass(frozen=True, slots=True)
class _AuthoritativeSegment:
    case_id: str
    source_id: str
    segment_id: str
    sequence: int
    page: int | None
    text_sha256: str
    text: str
    trust_label: EvidenceTrustLabel

    @property
    def citation(self) -> EvidenceCitation:
        return EvidenceCitation(
            case_id=self.case_id,
            source_id=self.source_id,
            segment_id=self.segment_id,
            sequence=self.sequence,
            page=self.page,
            text_sha256=self.text_sha256,
        )


@dataclass(frozen=True, slots=True)
class _RankedSeed:
    rank: int
    score: float
    segment: _AuthoritativeSegment
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class _MergedWindow:
    source_id: str
    start: int
    end: int
    first_rank: int


class EvidenceContextService:
    """Build a validated Context without model calls, writes, or external effects."""

    def __init__(
        self,
        connection: sqlite3.Connection,
    ) -> None:
        self._connection = connection
        self._retrieval = RetrievalService(connection)

    def build(self, request: EvidenceContextRequest) -> EvidenceContext:
        """Build one stable, budgeted Context from the formal retrieval service."""

        if not isinstance(request, EvidenceContextRequest):
            raise EvidenceContextRequestError("Evidence Context request is invalid")
        try:
            if self._connection.in_transaction:
                raise EvidenceContextPersistenceError(
                    "Evidence Context construction requires an idle connection"
                )
            self._connection.execute("BEGIN")
            self._require_case(request.case_id)
            hits = self._retrieval.search(
                case_id=request.case_id,
                query=request.query,
                limit=request.retrieval_limit,
            )
            authoritative = self._revalidate_hits(request.case_id, hits)
            seeds = self._allowed_seeds(request, hits, authoritative)
            matches = tuple(
                EvidenceContextMatch(
                    rank=seed.rank,
                    score=seed.score,
                    citation=seed.segment.citation,
                )
                for seed in seeds
            )
            candidates = self._expand_and_merge(request.case_id, seeds)
            items, used_bytes, truncated = _apply_budget(request, candidates)
            result = EvidenceContext(
                case_id=request.case_id,
                query=request.query,
                matches=matches,
                items=items,
                budget=request.budget,
                used_text_bytes=used_bytes,
                truncated=truncated,
            )
            self._connection.execute("COMMIT")
            return result
        except EvidenceContextError:
            self._rollback()
            raise
        except RetrievalIntegrityError as error:
            self._rollback()
            raise EvidenceContextIntegrityError(
                "Evidence retrieval integrity validation failed"
            ) from error
        except RetrievalRequestError as error:
            self._rollback()
            raise EvidenceContextRequestError("Evidence Context request is invalid") from error
        except RetrievalIndexError as error:
            self._rollback()
            raise EvidenceContextConfigurationError(
                "Local evidence retrieval is unavailable"
            ) from error
        except EvidenceContextModelError as error:
            self._rollback()
            raise EvidenceContextIntegrityError("Evidence Context validation failed") from error
        except (sqlite3.Error, ValueError, KeyError) as error:
            self._rollback()
            raise EvidenceContextPersistenceError(
                "Evidence Context could not read authoritative state"
            ) from error

    def _require_case(self, case_id: str) -> None:
        row = self._connection.execute(
            "SELECT 1 FROM case_record WHERE id = ?", (case_id,)
        ).fetchone()
        if row is None:
            raise EvidenceContextCaseNotFoundError

    def _revalidate_hits(
        self,
        case_id: str,
        hits: tuple[RetrievalResult, ...],
    ) -> dict[str, _AuthoritativeSegment]:
        segment_ids = tuple(hit.segment_id for hit in hits)
        if len(segment_ids) != len(set(segment_ids)):
            raise EvidenceContextIntegrityError("Evidence retrieval returned duplicate citations")
        if not segment_ids:
            return {}
        placeholders = ", ".join("?" for _segment_id in segment_ids)
        rows = self._connection.execute(
            f"""
            SELECT source.case_id, source.id AS source_id, source.trust_label,
                   segment.id AS segment_id, segment.sequence, segment.page,
                   segment.text, segment.text_sha256
            FROM source
            JOIN source_segment AS segment ON segment.source_id = source.id
            WHERE source.case_id = ?
              AND segment.id IN ({placeholders})
            ORDER BY segment.id
            """,
            (case_id, *segment_ids),
        ).fetchall()
        if len(rows) != len(segment_ids):
            raise EvidenceContextIntegrityError("Evidence citation is stale or out of scope")
        authoritative = {
            str(row["segment_id"]): _validated_segment(row, expected_case_id=case_id)
            for row in rows
        }
        for hit in hits:
            segment = authoritative.get(hit.segment_id)
            if segment is None or (
                hit.case_id != segment.case_id
                or hit.source_id != segment.source_id
                or hit.sequence != segment.sequence
                or hit.page != segment.page
                or hit.text_sha256 != segment.text_sha256
                or hit.text != segment.text
            ):
                raise EvidenceContextIntegrityError("Evidence citation validation failed")
        return authoritative

    def _allowed_seeds(
        self,
        request: EvidenceContextRequest,
        hits: tuple[RetrievalResult, ...],
        authoritative: dict[str, _AuthoritativeSegment],
    ) -> tuple[_RankedSeed, ...]:
        seeds: list[_RankedSeed] = []
        for rank, hit in enumerate(hits, start=1):
            segment = authoritative[hit.segment_id]
            if segment.trust_label not in request.policy.allowed_trust_labels:
                continue
            seeds.append(
                _RankedSeed(
                    rank=rank,
                    score=hit.score,
                    segment=segment,
                    start=max(0, segment.sequence - request.adjacent_before),
                    end=segment.sequence + request.adjacent_after,
                )
            )
        return tuple(seeds)

    def _expand_and_merge(
        self,
        case_id: str,
        seeds: tuple[_RankedSeed, ...],
    ) -> tuple[tuple[_AuthoritativeSegment, tuple[int, ...], EvidenceContextOrigin], ...]:
        if not seeds:
            return ()
        by_source: dict[str, list[_RankedSeed]] = {}
        for seed in seeds:
            by_source.setdefault(seed.segment.source_id, []).append(seed)

        maximum_sequences = self._load_source_maximum_sequences(case_id, tuple(sorted(by_source)))
        windows: list[_MergedWindow] = []
        for source_id, source_seeds in by_source.items():
            windows.extend(_merge_windows(source_id, source_seeds, maximum_sequences[source_id]))

        windows.sort(key=lambda window: (window.first_rank, window.source_id, window.start))
        window_segments = self._load_window_segments(case_id, tuple(windows))
        direct_ids = {seed.segment.segment_id for seed in seeds}
        emitted: set[str] = set()
        candidates: list[tuple[_AuthoritativeSegment, tuple[int, ...], EvidenceContextOrigin]] = []
        for window in windows:
            relevant_seeds = by_source[window.source_id]
            expected_trust_label = relevant_seeds[0].segment.trust_label
            for sequence in range(window.start, window.end + 1):
                segment = window_segments.get((window.source_id, sequence))
                if segment is None:
                    raise EvidenceContextIntegrityError(
                        "Evidence source sequence is not continuous"
                    )
                if segment.trust_label is not expected_trust_label:
                    raise EvidenceContextIntegrityError("Evidence policy scope changed")
                if segment.segment_id in emitted:
                    continue
                match_ranks = tuple(
                    seed.rank
                    for seed in relevant_seeds
                    if seed.start <= segment.sequence <= seed.end
                )
                if not match_ranks:
                    raise EvidenceContextIntegrityError("Evidence window attribution failed")
                origin = (
                    EvidenceContextOrigin.MATCH
                    if segment.segment_id in direct_ids
                    else EvidenceContextOrigin.ADJACENT
                )
                candidates.append((segment, match_ranks, origin))
                emitted.add(segment.segment_id)
        return tuple(candidates)

    def _load_source_maximum_sequences(
        self,
        case_id: str,
        source_ids: tuple[str, ...],
    ) -> dict[str, int]:
        placeholders = ", ".join("?" for _source_id in source_ids)
        rows = self._connection.execute(
            f"""
            SELECT source.id AS source_id,
                   MIN(segment.sequence) AS minimum_sequence,
                   MAX(segment.sequence) AS maximum_sequence,
                   COUNT(*) AS segment_count
            FROM source
            JOIN source_segment AS segment ON segment.source_id = source.id
            WHERE source.case_id = ? AND source.id IN ({placeholders})
            GROUP BY source.id
            ORDER BY source.id
            """,
            (case_id, *source_ids),
        ).fetchall()
        if len(rows) != len(source_ids):
            raise EvidenceContextIntegrityError("Evidence source is missing or out of scope")
        maximum_sequences: dict[str, int] = {}
        for row in rows:
            minimum_sequence = int(row["minimum_sequence"])
            maximum_sequence = int(row["maximum_sequence"])
            segment_count = int(row["segment_count"])
            if minimum_sequence != 0 or segment_count != maximum_sequence + 1:
                raise EvidenceContextIntegrityError("Evidence source sequence is not continuous")
            maximum_sequences[str(row["source_id"])] = maximum_sequence
        return maximum_sequences

    def _load_window_segments(
        self,
        case_id: str,
        windows: tuple[_MergedWindow, ...],
    ) -> dict[tuple[str, int], _AuthoritativeSegment]:
        predicates = " OR ".join(
            "(source.id = ? AND segment.sequence BETWEEN ? AND ?)" for _window in windows
        )
        parameters: list[object] = [case_id]
        for window in windows:
            parameters.extend((window.source_id, window.start, window.end))
        rows = self._connection.execute(
            f"""
            SELECT source.case_id, source.id AS source_id, source.trust_label,
                   segment.id AS segment_id, segment.sequence, segment.page,
                   segment.text, segment.text_sha256
            FROM source
            JOIN source_segment AS segment ON segment.source_id = source.id
            WHERE source.case_id = ? AND ({predicates})
            ORDER BY source.id, segment.sequence, segment.id
            """,
            parameters,
        ).fetchall()
        segments = tuple(_validated_segment(row, expected_case_id=case_id) for row in rows)
        indexed = {(segment.source_id, segment.sequence): segment for segment in segments}
        if len(indexed) != len(segments):
            raise EvidenceContextIntegrityError("Evidence window contains duplicate sequences")
        return indexed

    def _rollback(self) -> None:
        try:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
        except sqlite3.Error:
            return


def _validated_segment(
    row: sqlite3.Row,
    *,
    expected_case_id: str,
) -> _AuthoritativeSegment:
    case_id = str(row["case_id"])
    text = str(row["text"])
    text_sha256 = str(row["text_sha256"])
    if (
        case_id != expected_case_id
        or not text
        or hashlib.sha256(text.encode("utf-8")).hexdigest() != text_sha256
    ):
        raise EvidenceContextIntegrityError("Authoritative evidence validation failed")
    try:
        trust_label = EvidenceTrustLabel(str(row["trust_label"]))
    except ValueError as error:
        raise EvidenceContextIntegrityError("Evidence trust label is invalid") from error
    return _AuthoritativeSegment(
        case_id=case_id,
        source_id=str(row["source_id"]),
        segment_id=str(row["segment_id"]),
        sequence=int(row["sequence"]),
        page=int(row["page"]) if row["page"] is not None else None,
        text_sha256=text_sha256,
        text=text,
        trust_label=trust_label,
    )


def _merge_windows(
    source_id: str,
    seeds: list[_RankedSeed],
    maximum_sequence: int,
) -> tuple[_MergedWindow, ...]:
    ranges = sorted(
        (
            seed.start,
            min(seed.end, maximum_sequence),
            seed.rank,
        )
        for seed in seeds
    )
    merged: list[_MergedWindow] = []
    for start, end, rank in ranges:
        if end < start:
            raise EvidenceContextIntegrityError("Evidence window is invalid")
        if not merged or start > merged[-1].end + 1:
            merged.append(
                _MergedWindow(
                    source_id=source_id,
                    start=start,
                    end=end,
                    first_rank=rank,
                )
            )
            continue
        previous = merged[-1]
        merged[-1] = _MergedWindow(
            source_id=source_id,
            start=previous.start,
            end=max(previous.end, end),
            first_rank=min(previous.first_rank, rank),
        )
    return tuple(merged)


def _apply_budget(
    request: EvidenceContextRequest,
    candidates: tuple[tuple[_AuthoritativeSegment, tuple[int, ...], EvidenceContextOrigin], ...],
) -> tuple[tuple[EvidenceContextItem, ...], int, bool]:
    items: list[EvidenceContextItem] = []
    used_bytes = 0
    for segment, match_ranks, origin in candidates:
        text_bytes = len(segment.text.encode("utf-8"))
        if text_bytes > request.budget.max_item_text_bytes:
            continue
        if used_bytes + text_bytes > request.budget.max_text_bytes:
            continue
        items.append(
            EvidenceContextItem(
                citation=segment.citation,
                text=segment.text,
                origin=origin,
                match_ranks=match_ranks,
                text_bytes=text_bytes,
            )
        )
        used_bytes += text_bytes
    return tuple(items), used_bytes, len(items) != len(candidates)
