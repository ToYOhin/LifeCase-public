"""Small, deterministic SQLite schema migration runner."""

import hashlib
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from lifecase.retrieval.schema import CREATE_FTS_TABLE, POPULATE_FTS_TABLE


class MigrationError(RuntimeError):
    """Base class for migration failures."""


class ForeignKeysDisabledError(MigrationError):
    """Raised when migrations are attempted on an unsafe connection."""


class MigrationCatalogError(MigrationError):
    """Raised when the code migration catalog is invalid."""


class MigrationDriftError(MigrationError):
    """Raised when applied migrations do not match the code catalog."""


class MigrationApplyError(MigrationError):
    """Raised after a migration fails and its transaction is rolled back."""


@dataclass(frozen=True, slots=True)
class Migration:
    """One immutable, ordered database migration."""

    version: int
    name: str
    statements: tuple[str, ...]

    @property
    def checksum(self) -> str:
        """Return a stable digest used to detect edited migrations."""

        payload = "\n-- statement --\n".join(
            (str(self.version), self.name, *self.statements)
        ).encode()
        return hashlib.sha256(payload).hexdigest()


INITIAL_SCHEMA = Migration(
    version=1,
    name="initial_case_and_evidence_schema",
    statements=(
        """
        CREATE TABLE case_record (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            template_version INTEGER NOT NULL CHECK (template_version > 0),
            title TEXT NOT NULL,
            goal TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN (
                'intake',
                'needs_review',
                'ready',
                'in_progress',
                'waiting_external',
                'blocked',
                'resolved',
                'abandoned',
                'archived'
            )),
            priority INTEGER NOT NULL DEFAULT 0,
            version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            closed_at TEXT
        )
        """,
        """
        CREATE TABLE source (
            id TEXT PRIMARY KEY,
            case_id TEXT NOT NULL REFERENCES case_record(id) ON DELETE CASCADE,
            original_name TEXT NOT NULL,
            media_type TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
            object_path TEXT NOT NULL,
            trust_label TEXT NOT NULL DEFAULT 'untrusted'
                CHECK (trust_label = 'untrusted'),
            imported_at TEXT NOT NULL
        )
        """,
        "CREATE INDEX source_case_id_idx ON source(case_id)",
        "CREATE INDEX source_sha256_idx ON source(sha256)",
        """
        CREATE TABLE source_segment (
            id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL REFERENCES source(id) ON DELETE CASCADE,
            sequence INTEGER NOT NULL CHECK (sequence >= 0),
            page INTEGER CHECK (page > 0),
            bbox_json TEXT,
            text TEXT NOT NULL,
            extraction_method TEXT NOT NULL,
            confidence REAL CHECK (confidence >= 0.0 AND confidence <= 1.0),
            text_sha256 TEXT NOT NULL,
            UNIQUE (source_id, sequence)
        )
        """,
        "CREATE INDEX source_segment_source_id_idx ON source_segment(source_id)",
        """
        CREATE TABLE artifact (
            id TEXT PRIMARY KEY,
            case_id TEXT NOT NULL REFERENCES case_record(id) ON DELETE CASCADE,
            source_id TEXT REFERENCES source(id) ON DELETE SET NULL,
            type TEXT NOT NULL,
            relative_path TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
            created_by_run TEXT,
            created_at TEXT NOT NULL
        )
        """,
        "CREATE INDEX artifact_case_id_idx ON artifact(case_id)",
        "CREATE INDEX artifact_source_id_idx ON artifact(source_id)",
        "CREATE INDEX artifact_sha256_idx ON artifact(sha256)",
    ),
)

EXTRACTION_SCHEMA = Migration(
    version=2,
    name="source_extraction_state_and_version",
    statements=(
        """
        ALTER TABLE source_segment
        ADD COLUMN extractor_version TEXT NOT NULL DEFAULT 'legacy'
        """,
        """
        CREATE TABLE source_extraction (
            source_id TEXT PRIMARY KEY REFERENCES source(id) ON DELETE CASCADE,
            extractor_version TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN (
                'completed',
                'completed_with_notices',
                'ocr_required',
                'failed'
            )),
            segment_count INTEGER NOT NULL CHECK (segment_count >= 0),
            notices_json TEXT NOT NULL,
            error_code TEXT,
            updated_at TEXT NOT NULL
        )
        """,
    ),
)

RETRIEVAL_SCHEMA = Migration(
    version=3,
    name="source_segment_fts5_projection",
    statements=(CREATE_FTS_TABLE, POPULATE_FTS_TABLE),
)

CASE_TRANSITION_SCHEMA = Migration(
    version=4,
    name="case_transition_history",
    statements=(
        """
        UPDATE case_record
        SET closed_at = updated_at
        WHERE status IN ('resolved', 'abandoned', 'archived')
          AND closed_at IS NULL
        """,
        """
        CREATE TABLE case_transition (
            id TEXT PRIMARY KEY,
            case_id TEXT NOT NULL REFERENCES case_record(id) ON DELETE CASCADE,
            prior_status TEXT NOT NULL CHECK (prior_status IN (
                'intake', 'needs_review', 'ready', 'in_progress',
                'waiting_external', 'blocked', 'resolved', 'abandoned', 'archived'
            )),
            new_status TEXT NOT NULL CHECK (new_status IN (
                'intake', 'needs_review', 'ready', 'in_progress',
                'waiting_external', 'blocked', 'resolved', 'abandoned', 'archived'
            )),
            prior_version INTEGER NOT NULL CHECK (prior_version > 0),
            new_version INTEGER NOT NULL CHECK (new_version = prior_version + 1),
            actor TEXT NOT NULL CHECK (length(actor) BETWEEN 1 AND 120),
            reason TEXT NOT NULL CHECK (length(reason) BETWEEN 1 AND 1000),
            transitioned_at TEXT NOT NULL,
            UNIQUE (case_id, new_version)
        )
        """,
        """
        CREATE INDEX case_transition_case_id_idx
        ON case_transition(case_id, new_version, id)
        """,
    ),
)

CLAIM_PROVENANCE_SCHEMA = Migration(
    version=5,
    name="claim_provenance",
    statements=(
        """
        CREATE TABLE claim (
            id TEXT PRIMARY KEY,
            case_id TEXT NOT NULL REFERENCES case_record(id) ON DELETE CASCADE,
            field TEXT NOT NULL CHECK (length(field) BETWEEN 1 AND 64),
            value_json TEXT NOT NULL CHECK (length(value_json) BETWEEN 1 AND 4096),
            value_sha256 TEXT NOT NULL CHECK (length(value_sha256) = 64),
            comparison_sha256 TEXT NOT NULL CHECK (length(comparison_sha256) = 64),
            status TEXT NOT NULL CHECK (status IN (
                'candidate', 'inferred', 'confirmed', 'corrected', 'rejected'
            )),
            confidence REAL CHECK (confidence >= 0.0 AND confidence <= 1.0),
            created_by TEXT NOT NULL CHECK (created_by IN ('user', 'rule', 'agent')),
            contradiction INTEGER NOT NULL DEFAULT 0 CHECK (contradiction IN (0, 1)),
            version INTEGER NOT NULL CHECK (version > 0),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE INDEX claim_case_field_idx
        ON claim(case_id, field, status, id)
        """,
        """
        CREATE TABLE claim_source (
            claim_id TEXT NOT NULL REFERENCES claim(id) ON DELETE CASCADE,
            source_id TEXT NOT NULL,
            segment_id TEXT NOT NULL,
            segment_text_sha256 TEXT NOT NULL CHECK (length(segment_text_sha256) = 64),
            quote_start INTEGER NOT NULL CHECK (quote_start >= 0),
            quote_end INTEGER NOT NULL CHECK (quote_end > quote_start),
            quote_hash TEXT NOT NULL CHECK (length(quote_hash) = 64),
            relation TEXT NOT NULL CHECK (relation IN (
                'supports', 'contradicts', 'context'
            )),
            attached_by TEXT NOT NULL CHECK (length(attached_by) BETWEEN 1 AND 120),
            attached_at TEXT NOT NULL,
            PRIMARY KEY (
                claim_id, segment_id, quote_start, quote_end, relation
            )
        )
        """,
        """
        CREATE INDEX claim_source_claim_id_idx
        ON claim_source(claim_id, source_id, segment_id, quote_start, quote_end, relation)
        """,
        """
        CREATE TABLE claim_event (
            id TEXT PRIMARY KEY,
            claim_id TEXT NOT NULL REFERENCES claim(id) ON DELETE CASCADE,
            event_type TEXT NOT NULL CHECK (event_type IN (
                'created', 'source_attached', 'confirmed', 'corrected', 'rejected'
            )),
            prior_status TEXT CHECK (prior_status IN (
                'candidate', 'inferred', 'confirmed', 'corrected', 'rejected'
            )),
            new_status TEXT NOT NULL CHECK (new_status IN (
                'candidate', 'inferred', 'confirmed', 'corrected', 'rejected'
            )),
            prior_value_json TEXT,
            prior_value_sha256 TEXT,
            prior_comparison_sha256 TEXT,
            new_value_json TEXT NOT NULL,
            new_value_sha256 TEXT NOT NULL CHECK (length(new_value_sha256) = 64),
            new_comparison_sha256 TEXT NOT NULL
                CHECK (length(new_comparison_sha256) = 64),
            prior_version INTEGER NOT NULL CHECK (prior_version >= 0),
            new_version INTEGER NOT NULL CHECK (new_version = prior_version + 1),
            actor TEXT NOT NULL CHECK (length(actor) BETWEEN 1 AND 120),
            reason TEXT NOT NULL CHECK (length(reason) BETWEEN 1 AND 1000),
            occurred_at TEXT NOT NULL,
            UNIQUE (claim_id, new_version),
            CHECK (
                (prior_version = 0 AND prior_status IS NULL
                    AND prior_value_json IS NULL AND prior_value_sha256 IS NULL
                    AND prior_comparison_sha256 IS NULL)
                OR
                (prior_version > 0 AND prior_status IS NOT NULL
                    AND prior_value_json IS NOT NULL
                    AND length(prior_value_sha256) = 64
                    AND length(prior_comparison_sha256) = 64)
            )
        )
        """,
        """
        CREATE INDEX claim_event_claim_id_idx
        ON claim_event(claim_id, new_version, id)
        """,
        """
        CREATE TABLE claim_confirmation (
            id TEXT PRIMARY KEY,
            claim_id TEXT NOT NULL REFERENCES claim(id) ON DELETE CASCADE,
            event_id TEXT NOT NULL UNIQUE REFERENCES claim_event(id) ON DELETE CASCADE,
            claim_version INTEGER NOT NULL CHECK (claim_version > 0),
            basis TEXT NOT NULL CHECK (basis IN ('evidence', 'user')),
            value_sha256 TEXT NOT NULL CHECK (length(value_sha256) = 64),
            actor TEXT NOT NULL CHECK (length(actor) BETWEEN 1 AND 120),
            reason TEXT NOT NULL CHECK (length(reason) BETWEEN 1 AND 1000),
            confirmed_at TEXT NOT NULL,
            UNIQUE (claim_id, claim_version)
        )
        """,
        """
        CREATE INDEX claim_confirmation_claim_id_idx
        ON claim_confirmation(claim_id, claim_version, id)
        """,
    ),
)

CASE_TEMPLATE_BINDING_SCHEMA = Migration(
    version=6,
    name="case_template_identity_binding",
    statements=(
        """
        ALTER TABLE case_record
        ADD COLUMN template_id TEXT NOT NULL DEFAULT 'warranty_claim'
            CHECK (length(template_id) BETWEEN 1 AND 64)
        """,
    ),
)

AGENT_RUN_SCHEMA = Migration(
    version=7,
    name="agent_run_stage_checkpoint_and_budget",
    statements=(
        """
        CREATE TABLE agent_run (
            id TEXT PRIMARY KEY CHECK (length(id) BETWEEN 1 AND 100),
            case_id TEXT NOT NULL UNIQUE
                REFERENCES case_record(id) ON DELETE CASCADE,
            status TEXT NOT NULL CHECK (status IN (
                'queued', 'running', 'checkpointed', 'needs_review',
                'cancelled', 'completed'
            )),
            version INTEGER NOT NULL CHECK (version > 0),
            current_stage TEXT CHECK (current_stage IN (
                'extract', 'normalize', 'link_claims', 'classify_case',
                'check_gaps', 'propose_actions', 'validate'
            )),
            last_completed_stage TEXT CHECK (last_completed_stage IN (
                'extract', 'normalize', 'link_claims', 'classify_case',
                'check_gaps', 'propose_actions', 'validate'
            )),
            max_calls INTEGER NOT NULL CHECK (max_calls BETWEEN 1 AND 14),
            max_input_tokens INTEGER NOT NULL CHECK (max_input_tokens > 0),
            max_output_tokens INTEGER NOT NULL CHECK (max_output_tokens > 0),
            max_total_tokens INTEGER NOT NULL CHECK (
                max_total_tokens = max_input_tokens + max_output_tokens
            ),
            max_cost_minor INTEGER NOT NULL CHECK (max_cost_minor BETWEEN 0 AND 100),
            cost_currency TEXT NOT NULL CHECK (cost_currency = 'CNY'),
            used_calls INTEGER NOT NULL DEFAULT 0 CHECK (used_calls >= 0),
            used_input_tokens INTEGER NOT NULL DEFAULT 0
                CHECK (used_input_tokens >= 0),
            used_output_tokens INTEGER NOT NULL DEFAULT 0
                CHECK (used_output_tokens >= 0),
            used_total_tokens INTEGER NOT NULL DEFAULT 0 CHECK (
                used_total_tokens = used_input_tokens + used_output_tokens
            ),
            used_cost_minor INTEGER NOT NULL DEFAULT 0 CHECK (used_cost_minor >= 0),
            reserved_input_tokens INTEGER NOT NULL DEFAULT 0
                CHECK (reserved_input_tokens >= 0),
            reserved_output_tokens INTEGER NOT NULL DEFAULT 0
                CHECK (reserved_output_tokens >= 0),
            reserved_total_tokens INTEGER NOT NULL DEFAULT 0 CHECK (
                reserved_total_tokens = reserved_input_tokens + reserved_output_tokens
            ),
            reserved_cost_minor INTEGER NOT NULL DEFAULT 0
                CHECK (reserved_cost_minor >= 0),
            cancellation_requested INTEGER NOT NULL DEFAULT 0
                CHECK (cancellation_requested IN (0, 1)),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT,
            CHECK (used_calls <= max_calls),
            CHECK (used_input_tokens + reserved_input_tokens <= max_input_tokens),
            CHECK (used_output_tokens + reserved_output_tokens <= max_output_tokens),
            CHECK (used_total_tokens + reserved_total_tokens <= max_total_tokens),
            CHECK (used_cost_minor + reserved_cost_minor <= max_cost_minor),
            UNIQUE (id, case_id)
        )
        """,
        """
        CREATE INDEX agent_run_case_status_idx
        ON agent_run(case_id, status, id)
        """,
        """
        CREATE TABLE agent_stage_run (
            id TEXT PRIMARY KEY CHECK (length(id) BETWEEN 1 AND 100),
            run_id TEXT NOT NULL,
            case_id TEXT NOT NULL,
            stage TEXT NOT NULL CHECK (stage IN (
                'extract', 'normalize', 'link_claims', 'classify_case',
                'check_gaps', 'propose_actions', 'validate'
            )),
            stage_version TEXT NOT NULL CHECK (length(stage_version) BETWEEN 1 AND 64),
            input_schema_version TEXT NOT NULL
                CHECK (length(input_schema_version) BETWEEN 1 AND 64),
            output_schema_version TEXT NOT NULL
                CHECK (length(output_schema_version) BETWEEN 1 AND 64),
            checkpoint_schema_version TEXT NOT NULL
                CHECK (length(checkpoint_schema_version) BETWEEN 1 AND 64),
            input_scope_sha256 TEXT NOT NULL CHECK (length(input_scope_sha256) = 64),
            status TEXT NOT NULL CHECK (status IN (
                'running', 'checkpointed', 'needs_review', 'cancelled'
            )),
            version INTEGER NOT NULL CHECK (version > 0),
            max_calls INTEGER NOT NULL CHECK (max_calls BETWEEN 1 AND 2),
            max_input_tokens INTEGER NOT NULL CHECK (max_input_tokens > 0),
            max_output_tokens INTEGER NOT NULL CHECK (max_output_tokens > 0),
            max_total_tokens INTEGER NOT NULL CHECK (
                max_total_tokens = max_input_tokens + max_output_tokens
            ),
            max_cost_minor INTEGER NOT NULL CHECK (max_cost_minor BETWEEN 0 AND 100),
            cost_currency TEXT NOT NULL CHECK (cost_currency = 'CNY'),
            used_calls INTEGER NOT NULL DEFAULT 0 CHECK (used_calls >= 0),
            used_input_tokens INTEGER NOT NULL DEFAULT 0
                CHECK (used_input_tokens >= 0),
            used_output_tokens INTEGER NOT NULL DEFAULT 0
                CHECK (used_output_tokens >= 0),
            used_total_tokens INTEGER NOT NULL DEFAULT 0 CHECK (
                used_total_tokens = used_input_tokens + used_output_tokens
            ),
            used_cost_minor INTEGER NOT NULL DEFAULT 0 CHECK (used_cost_minor >= 0),
            reserved_input_tokens INTEGER NOT NULL DEFAULT 0
                CHECK (reserved_input_tokens >= 0),
            reserved_output_tokens INTEGER NOT NULL DEFAULT 0
                CHECK (reserved_output_tokens >= 0),
            reserved_total_tokens INTEGER NOT NULL DEFAULT 0 CHECK (
                reserved_total_tokens = reserved_input_tokens + reserved_output_tokens
            ),
            reserved_cost_minor INTEGER NOT NULL DEFAULT 0
                CHECK (reserved_cost_minor >= 0),
            repair_count INTEGER NOT NULL DEFAULT 0 CHECK (repair_count BETWEEN 0 AND 1),
            provider TEXT CHECK (provider IN ('disabled', 'fake', 'openai')),
            model_id TEXT CHECK (length(model_id) BETWEEN 1 AND 100),
            latency_ms INTEGER NOT NULL DEFAULT 0 CHECK (latency_ms >= 0),
            checkpoint_json TEXT CHECK (length(checkpoint_json) BETWEEN 1 AND 262144),
            checkpoint_sha256 TEXT CHECK (length(checkpoint_sha256) = 64),
            error_code TEXT CHECK (length(error_code) BETWEEN 1 AND 100),
            retryable INTEGER CHECK (retryable IN (0, 1)),
            started_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT,
            CHECK (used_calls <= max_calls),
            CHECK (used_input_tokens + reserved_input_tokens <= max_input_tokens),
            CHECK (used_output_tokens + reserved_output_tokens <= max_output_tokens),
            CHECK (used_total_tokens + reserved_total_tokens <= max_total_tokens),
            CHECK (used_cost_minor + reserved_cost_minor <= max_cost_minor),
            UNIQUE (run_id, stage),
            FOREIGN KEY (run_id, case_id)
                REFERENCES agent_run(id, case_id) ON DELETE CASCADE,
            CHECK (
                (checkpoint_json IS NULL AND checkpoint_sha256 IS NULL)
                OR
                (checkpoint_json IS NOT NULL AND checkpoint_sha256 IS NOT NULL)
            )
        )
        """,
        """
        CREATE INDEX agent_stage_run_case_order_idx
        ON agent_stage_run(case_id, run_id, stage, id)
        """,
    ),
)

DEADLINE_SCHEMA = Migration(
    version=8,
    name="deadline_authority_and_history",
    statements=(
        """
        CREATE TABLE deadline (
            id TEXT PRIMARY KEY CHECK (length(id) BETWEEN 1 AND 200),
            case_id TEXT NOT NULL REFERENCES case_record(id) ON DELETE CASCADE,
            kind TEXT NOT NULL CHECK (kind IN ('explicit', 'calculated', 'suggested')),
            date_kind TEXT NOT NULL CHECK (date_kind IN ('utc_instant', 'local_date')),
            due_value TEXT NOT NULL CHECK (length(due_value) BETWEEN 1 AND 40),
            timezone TEXT NOT NULL CHECK (length(timezone) BETWEEN 1 AND 100),
            status TEXT NOT NULL CHECK (status IN (
                'proposed', 'confirmed', 'passed', 'completed', 'dismissed'
            )),
            uncertainty TEXT NOT NULL CHECK (uncertainty IN (
                'evidence_backed_date',
                'deterministic_rule_result_requires_confirmation',
                'personal_follow_up_preference_not_external_obligation'
            )),
            confidence REAL CHECK (confidence >= 0.0 AND confidence <= 1.0),
            rule_id TEXT CHECK (length(rule_id) BETWEEN 1 AND 64),
            rule_version INTEGER CHECK (rule_version > 0),
            rule_parameters_json TEXT CHECK (length(rule_parameters_json) BETWEEN 2 AND 512),
            rule_parameters_sha256 TEXT CHECK (length(rule_parameters_sha256) = 64),
            version INTEGER NOT NULL CHECK (version > 0),
            definition_sha256 TEXT NOT NULL CHECK (length(definition_sha256) = 64),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (id, case_id),
            CHECK (
                (kind = 'calculated' AND rule_id IS NOT NULL
                    AND rule_version IS NOT NULL AND rule_parameters_json IS NOT NULL
                    AND rule_parameters_sha256 IS NOT NULL)
                OR
                (kind IN ('explicit', 'suggested') AND rule_id IS NULL
                    AND rule_version IS NULL AND rule_parameters_json IS NULL
                    AND rule_parameters_sha256 IS NULL)
            )
        )
        """,
        """
        CREATE INDEX deadline_case_order_idx
        ON deadline(case_id, created_at, id)
        """,
        """
        CREATE TABLE deadline_basis_claim (
            deadline_id TEXT NOT NULL,
            case_id TEXT NOT NULL,
            position INTEGER NOT NULL CHECK (position >= 0),
            claim_id TEXT NOT NULL REFERENCES claim(id) ON DELETE RESTRICT,
            claim_version INTEGER NOT NULL CHECK (claim_version > 0),
            value_sha256 TEXT NOT NULL CHECK (length(value_sha256) = 64),
            date_kind TEXT NOT NULL CHECK (date_kind IN ('utc_instant', 'local_date')),
            PRIMARY KEY (deadline_id, position),
            UNIQUE (deadline_id, claim_id),
            FOREIGN KEY (deadline_id, case_id)
                REFERENCES deadline(id, case_id) ON DELETE CASCADE
        )
        """,
        """
        CREATE INDEX deadline_basis_claim_case_idx
        ON deadline_basis_claim(case_id, claim_id, deadline_id)
        """,
        """
        CREATE TABLE deadline_event (
            id TEXT PRIMARY KEY CHECK (length(id) BETWEEN 1 AND 200),
            deadline_id TEXT NOT NULL,
            case_id TEXT NOT NULL,
            event_type TEXT NOT NULL CHECK (event_type IN (
                'created', 'confirmed', 'passed', 'completed', 'dismissed'
            )),
            prior_status TEXT CHECK (prior_status IN (
                'proposed', 'confirmed', 'passed', 'completed', 'dismissed'
            )),
            new_status TEXT NOT NULL CHECK (new_status IN (
                'proposed', 'confirmed', 'passed', 'completed', 'dismissed'
            )),
            prior_version INTEGER NOT NULL CHECK (prior_version >= 0),
            new_version INTEGER NOT NULL CHECK (new_version = prior_version + 1),
            definition_sha256 TEXT NOT NULL CHECK (length(definition_sha256) = 64),
            actor TEXT NOT NULL CHECK (length(actor) BETWEEN 1 AND 120),
            reason TEXT NOT NULL CHECK (length(reason) BETWEEN 1 AND 1000),
            occurred_at TEXT NOT NULL,
            UNIQUE (deadline_id, new_version),
            FOREIGN KEY (deadline_id, case_id)
                REFERENCES deadline(id, case_id) ON DELETE CASCADE,
            CHECK (
                (prior_version = 0 AND prior_status IS NULL
                    AND event_type = 'created' AND new_status = 'proposed')
                OR
                (prior_version > 0 AND prior_status IS NOT NULL
                    AND event_type != 'created')
            )
        )
        """,
        """
        CREATE INDEX deadline_event_case_order_idx
        ON deadline_event(case_id, occurred_at, deadline_id, new_version, id)
        """,
    ),
)

ACTION_SCHEMA = Migration(
    version=9,
    name="action_authority_graph_and_history",
    statements=(
        "CREATE UNIQUE INDEX claim_id_case_unique_idx ON claim(id, case_id)",
        "CREATE UNIQUE INDEX source_id_case_unique_idx ON source(id, case_id)",
        "CREATE UNIQUE INDEX artifact_id_case_unique_idx ON artifact(id, case_id)",
        """
        CREATE TABLE action (
            id TEXT PRIMARY KEY CHECK (length(id) BETWEEN 1 AND 200),
            case_id TEXT NOT NULL REFERENCES case_record(id) ON DELETE CASCADE,
            kind TEXT NOT NULL CHECK (kind IN (
                'collect_evidence', 'prepare_communication', 'follow_up', 'record_outcome'
            )),
            status TEXT NOT NULL CHECK (status IN (
                'proposed', 'blocked', 'ready', 'running', 'done', 'failed', 'cancelled'
            )),
            risk_level TEXT NOT NULL CHECK (risk_level IN ('A', 'B', 'C')),
            payload_json TEXT NOT NULL CHECK (length(payload_json) BETWEEN 2 AND 2048),
            payload_sha256 TEXT NOT NULL CHECK (length(payload_sha256) = 64),
            blockers_json TEXT NOT NULL CHECK (length(blockers_json) BETWEEN 2 AND 4096),
            blockers_sha256 TEXT NOT NULL CHECK (length(blockers_sha256) = 64),
            version INTEGER NOT NULL CHECK (version > 0),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (id, case_id)
        )
        """,
        """
        CREATE INDEX action_case_order_idx
        ON action(case_id, created_at, id)
        """,
        """
        CREATE TABLE action_requirement_claim (
            action_id TEXT NOT NULL,
            case_id TEXT NOT NULL,
            position INTEGER NOT NULL CHECK (position >= 0),
            claim_id TEXT NOT NULL,
            claim_version INTEGER NOT NULL CHECK (claim_version > 0),
            value_sha256 TEXT NOT NULL CHECK (length(value_sha256) = 64),
            field TEXT NOT NULL CHECK (length(field) BETWEEN 1 AND 64),
            PRIMARY KEY (action_id, position),
            UNIQUE (action_id, claim_id),
            UNIQUE (action_id, field),
            FOREIGN KEY (action_id, case_id)
                REFERENCES action(id, case_id) ON DELETE CASCADE,
            FOREIGN KEY (claim_id, case_id)
                REFERENCES claim(id, case_id) ON DELETE RESTRICT
        )
        """,
        """
        CREATE INDEX action_requirement_case_idx
        ON action_requirement_claim(case_id, claim_id, action_id)
        """,
        """
        CREATE TABLE action_dependency (
            action_id TEXT NOT NULL,
            case_id TEXT NOT NULL,
            position INTEGER NOT NULL CHECK (position >= 0),
            depends_on_action_id TEXT NOT NULL,
            depends_on_action_version INTEGER NOT NULL CHECK (depends_on_action_version > 0),
            PRIMARY KEY (action_id, position),
            UNIQUE (action_id, depends_on_action_id),
            CHECK (action_id != depends_on_action_id),
            FOREIGN KEY (action_id, case_id)
                REFERENCES action(id, case_id) ON DELETE CASCADE,
            FOREIGN KEY (depends_on_action_id, case_id)
                REFERENCES action(id, case_id) ON DELETE RESTRICT
        )
        """,
        """
        CREATE INDEX action_dependency_case_idx
        ON action_dependency(case_id, depends_on_action_id, action_id)
        """,
        """
        CREATE TABLE action_deadline (
            action_id TEXT PRIMARY KEY,
            case_id TEXT NOT NULL,
            deadline_id TEXT NOT NULL,
            deadline_version INTEGER NOT NULL CHECK (deadline_version > 0),
            definition_sha256 TEXT NOT NULL CHECK (length(definition_sha256) = 64),
            FOREIGN KEY (action_id, case_id)
                REFERENCES action(id, case_id) ON DELETE CASCADE,
            FOREIGN KEY (deadline_id, case_id)
                REFERENCES deadline(id, case_id) ON DELETE RESTRICT
        )
        """,
        """
        CREATE INDEX action_deadline_case_idx
        ON action_deadline(case_id, deadline_id, action_id)
        """,
        """
        CREATE TABLE action_completion_evidence (
            action_id TEXT NOT NULL,
            case_id TEXT NOT NULL,
            position INTEGER NOT NULL CHECK (position >= 0),
            authority_type TEXT NOT NULL CHECK (authority_type IN ('claim', 'source', 'artifact')),
            authority_id TEXT NOT NULL CHECK (length(authority_id) BETWEEN 1 AND 200),
            authority_version INTEGER CHECK (authority_version > 0),
            authority_sha256 TEXT NOT NULL CHECK (length(authority_sha256) = 64),
            PRIMARY KEY (action_id, position),
            UNIQUE (action_id, authority_type, authority_id),
            FOREIGN KEY (action_id, case_id)
                REFERENCES action(id, case_id) ON DELETE CASCADE,
            CHECK (
                (authority_type = 'claim' AND authority_version IS NOT NULL)
                OR
                (authority_type IN ('source', 'artifact') AND authority_version IS NULL)
            )
        )
        """,
        """
        CREATE INDEX action_completion_evidence_case_idx
        ON action_completion_evidence(case_id, authority_type, authority_id, action_id)
        """,
        """
        CREATE TABLE action_event (
            id TEXT PRIMARY KEY CHECK (length(id) BETWEEN 1 AND 200),
            action_id TEXT NOT NULL,
            case_id TEXT NOT NULL,
            event_type TEXT NOT NULL CHECK (event_type IN (
                'created', 'revised', 'revalidated', 'started',
                'completed', 'failed', 'cancelled'
            )),
            prior_status TEXT CHECK (prior_status IN (
                'proposed', 'blocked', 'ready', 'running', 'done', 'failed', 'cancelled'
            )),
            new_status TEXT NOT NULL CHECK (new_status IN (
                'proposed', 'blocked', 'ready', 'running', 'done', 'failed', 'cancelled'
            )),
            prior_version INTEGER NOT NULL CHECK (prior_version >= 0),
            new_version INTEGER NOT NULL CHECK (new_version = prior_version + 1),
            payload_sha256 TEXT NOT NULL CHECK (length(payload_sha256) = 64),
            blockers_sha256 TEXT NOT NULL CHECK (length(blockers_sha256) = 64),
            actor TEXT NOT NULL CHECK (length(actor) BETWEEN 1 AND 120),
            reason TEXT NOT NULL CHECK (length(reason) BETWEEN 1 AND 1000),
            occurred_at TEXT NOT NULL,
            UNIQUE (action_id, new_version),
            FOREIGN KEY (action_id, case_id)
                REFERENCES action(id, case_id) ON DELETE CASCADE,
            CHECK (
                (prior_version = 0 AND prior_status IS NULL
                    AND event_type = 'created' AND new_version = 1)
                OR
                (prior_version > 0 AND prior_status IS NOT NULL
                    AND event_type != 'created')
            )
        )
        """,
        """
        CREATE INDEX action_event_case_order_idx
        ON action_event(case_id, occurred_at, action_id, new_version, id)
        """,
    ),
)


def _approval_schema_statements() -> tuple[str, ...]:
    """Build the forward-only Action table rebuild used by migration 10."""

    table_names = (
        "action",
        "action_requirement_claim",
        "action_dependency",
        "action_deadline",
        "action_completion_evidence",
        "action_event",
    )
    create_positions = (3, 5, 7, 9, 11, 13)
    create_v10: list[str] = []
    for table_name, position in zip(table_names, create_positions, strict=True):
        statement = ACTION_SCHEMA.statements[position]
        statement = statement.replace(
            f"CREATE TABLE {table_name} (",
            f"CREATE TABLE {table_name}_v10 (",
            1,
        )
        statement = statement.replace(
            "REFERENCES action(id, case_id)", "REFERENCES action_v10(id, case_id)"
        )
        statement = statement.replace(
            "'failed', 'cancelled'",
            "'failed', 'cancelled', 'approved'",
        )
        create_v10.append(statement)

    copy_rows = tuple(
        f"INSERT INTO {table_name}_v10 SELECT * FROM {table_name}" for table_name in table_names
    )
    drop_old = tuple(f"DROP TABLE {table_name}" for table_name in reversed(table_names))
    rename_new = tuple(
        f"ALTER TABLE {table_name}_v10 RENAME TO {table_name}" for table_name in table_names
    )
    recreate_indexes = tuple(
        ACTION_SCHEMA.statements[position] for position in (4, 6, 8, 10, 12, 14)
    )
    approval_tables = (
        """
        CREATE TABLE approval_request (
            id TEXT PRIMARY KEY CHECK (length(id) BETWEEN 1 AND 200),
            action_id TEXT NOT NULL,
            case_id TEXT NOT NULL,
            action_version INTEGER NOT NULL CHECK (action_version > 0),
            payload_sha256 TEXT NOT NULL CHECK (length(payload_sha256) = 64),
            summary_text TEXT NOT NULL CHECK (length(summary_text) BETWEEN 1 AND 1000),
            summary_sha256 TEXT NOT NULL CHECK (length(summary_sha256) = 64),
            status TEXT NOT NULL CHECK (status IN (
                'pending', 'approved', 'rejected', 'expired', 'invalidated'
            )),
            version INTEGER NOT NULL CHECK (version > 0),
            requested_by TEXT NOT NULL CHECK (length(requested_by) BETWEEN 1 AND 120),
            requested_reason TEXT NOT NULL CHECK (length(requested_reason) BETWEEN 1 AND 1000),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (id, case_id),
            FOREIGN KEY (action_id, case_id)
                REFERENCES action(id, case_id) ON DELETE CASCADE
        )
        """,
        """
        CREATE UNIQUE INDEX approval_request_active_action_idx
        ON approval_request(action_id)
        WHERE status IN ('pending', 'approved')
        """,
        """
        CREATE INDEX approval_request_case_order_idx
        ON approval_request(case_id, created_at, id)
        """,
        """
        CREATE TABLE approval_event (
            id TEXT PRIMARY KEY CHECK (length(id) BETWEEN 1 AND 200),
            approval_id TEXT NOT NULL,
            action_id TEXT NOT NULL,
            case_id TEXT NOT NULL,
            event_type TEXT NOT NULL CHECK (event_type IN (
                'requested', 'approved', 'rejected', 'expired', 'invalidated'
            )),
            prior_status TEXT CHECK (prior_status IN (
                'pending', 'approved', 'rejected', 'expired', 'invalidated'
            )),
            new_status TEXT NOT NULL CHECK (new_status IN (
                'pending', 'approved', 'rejected', 'expired', 'invalidated'
            )),
            prior_version INTEGER NOT NULL CHECK (prior_version >= 0),
            new_version INTEGER NOT NULL CHECK (new_version = prior_version + 1),
            actor TEXT NOT NULL CHECK (length(actor) BETWEEN 1 AND 120),
            reason TEXT NOT NULL CHECK (length(reason) BETWEEN 1 AND 1000),
            occurred_at TEXT NOT NULL,
            UNIQUE (approval_id, new_version),
            FOREIGN KEY (approval_id, case_id)
                REFERENCES approval_request(id, case_id) ON DELETE CASCADE,
            FOREIGN KEY (action_id, case_id)
                REFERENCES action(id, case_id) ON DELETE CASCADE,
            CHECK (
                (prior_version = 0 AND prior_status IS NULL
                    AND event_type = 'requested' AND new_version = 1)
                OR
                (prior_version > 0 AND prior_status IS NOT NULL
                    AND event_type != 'requested')
            )
        )
        """,
        """
        CREATE INDEX approval_event_case_order_idx
        ON approval_event(case_id, occurred_at, approval_id, new_version, id)
        """,
    )
    return (
        *create_v10,
        *copy_rows,
        *drop_old,
        *rename_new,
        *recreate_indexes,
        *approval_tables,
    )


APPROVAL_SCHEMA = Migration(
    version=10,
    name="approval_gate_and_action_approved_state",
    statements=_approval_schema_statements(),
)


def _ollama_provider_schema_statements() -> tuple[str, ...]:
    create_stage = AGENT_RUN_SCHEMA.statements[2]
    create_stage = create_stage.replace(
        "CREATE TABLE agent_stage_run (",
        "CREATE TABLE agent_stage_run_v11 (",
        1,
    ).replace(
        "provider TEXT CHECK (provider IN ('disabled', 'fake', 'openai'))",
        "provider TEXT CHECK (provider IN ('disabled', 'fake', 'ollama', 'openai'))",
        1,
    )
    return (
        create_stage,
        "INSERT INTO agent_stage_run_v11 SELECT * FROM agent_stage_run",
        "DROP TABLE agent_stage_run",
        "ALTER TABLE agent_stage_run_v11 RENAME TO agent_stage_run",
        AGENT_RUN_SCHEMA.statements[3],
    )


OLLAMA_PROVIDER_SCHEMA = Migration(
    version=11,
    name="ollama_provider_identity",
    statements=_ollama_provider_schema_statements(),
)


def _groq_provider_schema_statements() -> tuple[str, ...]:
    create_stage = OLLAMA_PROVIDER_SCHEMA.statements[0]
    create_stage = create_stage.replace(
        "CREATE TABLE agent_stage_run_v11 (",
        "CREATE TABLE agent_stage_run_v12 (",
        1,
    ).replace(
        "provider TEXT CHECK (provider IN ('disabled', 'fake', 'ollama', 'openai'))",
        "provider TEXT CHECK (provider IN ('disabled', 'fake', 'groq', 'ollama', 'openai'))",
        1,
    )
    return (
        create_stage,
        "INSERT INTO agent_stage_run_v12 SELECT * FROM agent_stage_run",
        "DROP TABLE agent_stage_run",
        "ALTER TABLE agent_stage_run_v12 RENAME TO agent_stage_run",
        AGENT_RUN_SCHEMA.statements[3],
    )


GROQ_PROVIDER_SCHEMA = Migration(
    version=12,
    name="groq_provider_identity",
    statements=_groq_provider_schema_statements(),
)


def _mistral_provider_schema_statements() -> tuple[str, ...]:
    create_stage = GROQ_PROVIDER_SCHEMA.statements[0]
    create_stage = create_stage.replace(
        "CREATE TABLE agent_stage_run_v12 (",
        "CREATE TABLE agent_stage_run_v13 (",
        1,
    ).replace(
        "provider TEXT CHECK (provider IN ('disabled', 'fake', 'groq', 'ollama', 'openai'))",
        "provider TEXT CHECK (provider IN "
        "('disabled', 'fake', 'groq', 'mistral', 'ollama', 'openai'))",
        1,
    )
    return (
        create_stage,
        "INSERT INTO agent_stage_run_v13 SELECT * FROM agent_stage_run",
        "DROP TABLE agent_stage_run",
        "ALTER TABLE agent_stage_run_v13 RENAME TO agent_stage_run",
        AGENT_RUN_SCHEMA.statements[3],
    )


MISTRAL_PROVIDER_SCHEMA = Migration(
    version=13,
    name="mistral_provider_identity",
    statements=_mistral_provider_schema_statements(),
)


def _deepseek_evaluation_provider_schema_statements() -> tuple[str, ...]:
    create_stage = MISTRAL_PROVIDER_SCHEMA.statements[0]
    create_stage = create_stage.replace(
        "CREATE TABLE agent_stage_run_v13 (",
        "CREATE TABLE agent_stage_run_v14 (",
        1,
    ).replace(
        "provider TEXT CHECK (provider IN "
        "('disabled', 'fake', 'groq', 'mistral', 'ollama', 'openai'))",
        "provider TEXT CHECK (provider IN "
        "('deepseek', 'disabled', 'fake', 'groq', 'mistral', 'ollama', 'openai'))",
        1,
    )
    return (
        create_stage,
        "INSERT INTO agent_stage_run_v14 SELECT * FROM agent_stage_run",
        "DROP TABLE agent_stage_run",
        "ALTER TABLE agent_stage_run_v14 RENAME TO agent_stage_run",
        AGENT_RUN_SCHEMA.statements[3],
    )


DEEPSEEK_EVALUATION_PROVIDER_SCHEMA = Migration(
    version=14,
    name="deepseek_evaluation_provider_identity",
    statements=_deepseek_evaluation_provider_schema_statements(),
)


def _gemini_anthropic_provider_schema_statements() -> tuple[str, ...]:
    create_stage = DEEPSEEK_EVALUATION_PROVIDER_SCHEMA.statements[0]
    create_stage = create_stage.replace(
        "CREATE TABLE agent_stage_run_v14 (",
        "CREATE TABLE agent_stage_run_v15 (",
        1,
    ).replace(
        "provider TEXT CHECK (provider IN "
        "('deepseek', 'disabled', 'fake', 'groq', 'mistral', 'ollama', 'openai'))",
        "provider TEXT CHECK (provider IN "
        "('anthropic', 'deepseek', 'disabled', 'fake', 'gemini', 'groq', "
        "'mistral', 'ollama', 'openai'))",
        1,
    )
    return (
        create_stage,
        "INSERT INTO agent_stage_run_v15 SELECT * FROM agent_stage_run",
        "DROP TABLE agent_stage_run",
        "ALTER TABLE agent_stage_run_v15 RENAME TO agent_stage_run",
        AGENT_RUN_SCHEMA.statements[3],
    )


GEMINI_ANTHROPIC_PROVIDER_SCHEMA = Migration(
    version=15,
    name="gemini_anthropic_provider_identity",
    statements=_gemini_anthropic_provider_schema_statements(),
)


def _versioned_agent_run_schema_statements() -> tuple[str, ...]:
    create_run = (
        AGENT_RUN_SCHEMA.statements[0]
        .replace("CREATE TABLE agent_run (", "CREATE TABLE agent_run_v16 (", 1)
        .replace(
            "case_id TEXT NOT NULL UNIQUE\n                REFERENCES case_record(id)",
            "case_id TEXT NOT NULL\n                REFERENCES case_record(id)",
            1,
        )
    )
    create_stage = (
        GEMINI_ANTHROPIC_PROVIDER_SCHEMA.statements[0]
        .replace(
            "CREATE TABLE agent_stage_run_v15 (",
            "CREATE TABLE agent_stage_run_v16 (",
            1,
        )
        .replace(
            "REFERENCES agent_run(id, case_id)",
            "REFERENCES agent_run_v16(id, case_id)",
            1,
        )
    )
    return (
        create_run,
        create_stage,
        "INSERT INTO agent_run_v16 SELECT * FROM agent_run",
        "INSERT INTO agent_stage_run_v16 SELECT * FROM agent_stage_run",
        "DROP TABLE agent_stage_run",
        "DROP TABLE agent_run",
        "ALTER TABLE agent_run_v16 RENAME TO agent_run",
        "ALTER TABLE agent_stage_run_v16 RENAME TO agent_stage_run",
        AGENT_RUN_SCHEMA.statements[1],
        AGENT_RUN_SCHEMA.statements[3],
    )


VERSIONED_AGENT_RUN_SCHEMA = Migration(
    version=16,
    name="versioned_agent_run_history",
    statements=_versioned_agent_run_schema_statements(),
)

MIGRATIONS: tuple[Migration, ...] = (
    INITIAL_SCHEMA,
    EXTRACTION_SCHEMA,
    RETRIEVAL_SCHEMA,
    CASE_TRANSITION_SCHEMA,
    CLAIM_PROVENANCE_SCHEMA,
    CASE_TEMPLATE_BINDING_SCHEMA,
    AGENT_RUN_SCHEMA,
    DEADLINE_SCHEMA,
    ACTION_SCHEMA,
    APPROVAL_SCHEMA,
    OLLAMA_PROVIDER_SCHEMA,
    GROQ_PROVIDER_SCHEMA,
    MISTRAL_PROVIDER_SCHEMA,
    DEEPSEEK_EVALUATION_PROVIDER_SCHEMA,
    GEMINI_ANTHROPIC_PROVIDER_SCHEMA,
    VERSIONED_AGENT_RUN_SCHEMA,
)

_CREATE_MIGRATION_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migration (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    checksum TEXT NOT NULL,
    applied_at TEXT NOT NULL
)
"""


def apply_migrations(
    connection: sqlite3.Connection,
    migrations: Sequence[Migration] = MIGRATIONS,
) -> None:
    """Apply pending migrations in order, one atomic transaction at a time."""

    _require_safe_connection(connection)
    ordered = _validate_catalog(migrations)
    _ensure_migration_table(connection)
    applied = _read_applied_migrations(connection)
    _validate_applied_migrations(applied, ordered)

    for migration in ordered:
        if migration.version in applied:
            continue
        _apply_one(connection, migration)


def pending_migrations(
    connection: sqlite3.Connection,
    migrations: Sequence[Migration] = MIGRATIONS,
) -> tuple[Migration, ...]:
    """Inspect a catalog without creating tables or applying pending migrations."""

    _require_safe_connection(connection)
    ordered = _validate_catalog(migrations)
    table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migration'"
    ).fetchone()
    if table is None:
        return ordered
    applied = _read_applied_migrations(connection)
    _validate_applied_migrations(applied, ordered)
    return tuple(item for item in ordered if item.version not in applied)


def has_applied_migrations(connection: sqlite3.Connection) -> bool:
    """Return whether a database contains at least one committed application migration."""

    table = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migration'"
    ).fetchone()
    if table is None:
        return False
    return connection.execute("SELECT 1 FROM schema_migration LIMIT 1").fetchone() is not None


def _require_safe_connection(connection: sqlite3.Connection) -> None:
    if connection.in_transaction:
        raise MigrationError("Migrations require a connection outside a transaction")

    foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()
    if foreign_keys is None or foreign_keys[0] != 1:
        raise ForeignKeysDisabledError(
            "SQLite foreign keys must be enabled before applying migrations"
        )


def _validate_catalog(migrations: Sequence[Migration]) -> tuple[Migration, ...]:
    ordered = tuple(migrations)
    versions = [migration.version for migration in ordered]
    if any(version <= 0 for version in versions):
        raise MigrationCatalogError("Migration versions must be positive")
    if versions != sorted(versions) or len(versions) != len(set(versions)):
        raise MigrationCatalogError("Migrations must have unique, ascending versions")
    if any(not migration.name.strip() or not migration.statements for migration in ordered):
        raise MigrationCatalogError("Each migration needs a name and at least one statement")
    return ordered


def _ensure_migration_table(connection: sqlite3.Connection) -> None:
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(_CREATE_MIGRATION_TABLE)
        connection.execute("COMMIT")
    except Exception:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise


def _read_applied_migrations(connection: sqlite3.Connection) -> dict[int, tuple[str, str]]:
    rows = connection.execute(
        "SELECT version, name, checksum FROM schema_migration ORDER BY version"
    ).fetchall()
    return {int(row[0]): (str(row[1]), str(row[2])) for row in rows}


def _validate_applied_migrations(
    applied: dict[int, tuple[str, str]], migrations: tuple[Migration, ...]
) -> None:
    catalog = {migration.version: migration for migration in migrations}
    unknown = sorted(set(applied) - set(catalog))
    if unknown:
        raise MigrationDriftError(f"Database contains migrations unknown to this build: {unknown}")

    applied_versions = sorted(applied)
    expected_prefix = [migration.version for migration in migrations[: len(applied_versions)]]
    if applied_versions != expected_prefix:
        raise MigrationDriftError("Applied migrations are not a contiguous catalog prefix")

    for version, (name, checksum) in applied.items():
        migration = catalog[version]
        if name != migration.name or checksum != migration.checksum:
            raise MigrationDriftError(f"Migration {version} differs from the applied migration")


def _apply_one(connection: sqlite3.Connection, migration: Migration) -> None:
    try:
        connection.execute("BEGIN IMMEDIATE")
        for statement in migration.statements:
            connection.execute(statement)
        connection.execute(
            """
            INSERT INTO schema_migration (version, name, checksum, applied_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                migration.version,
                migration.name,
                migration.checksum,
                _utc_now(),
            ),
        )
        connection.execute("COMMIT")
    except Exception as error:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise MigrationApplyError(
            f"Migration {migration.version} ({migration.name}) failed and was rolled back"
        ) from error


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
