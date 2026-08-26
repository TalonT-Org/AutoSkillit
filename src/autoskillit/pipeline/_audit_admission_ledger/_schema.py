"""DDL, schema-version, and connection-level constants for the audit admission ledger.

The DDL blob defines every table the ledger owns. The schema version, database
file mode, and directory mode are connection-open invariants; ``_connections``
imports them here so the schema is the single source of truth.
"""

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) STRICT;
CREATE TABLE IF NOT EXISTS installations (
    recipe_execution_id TEXT PRIMARY KEY,
    installation_version TEXT NOT NULL,
    snapshot_digest TEXT NOT NULL,
    retired INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
) STRICT;
CREATE TABLE IF NOT EXISTS installation_occurrences (
    recipe_execution_id TEXT NOT NULL,
    installation_version TEXT NOT NULL,
    snapshot_digest TEXT NOT NULL,
    created_at TEXT NOT NULL,
    retired_at TEXT,
    PRIMARY KEY (recipe_execution_id, installation_version)
) STRICT;
CREATE TABLE IF NOT EXISTS slots (
    slot_id TEXT PRIMARY KEY,
    recipe_execution_id TEXT NOT NULL,
    installation_version TEXT NOT NULL,
    step_name TEXT NOT NULL,
    head_key TEXT NOT NULL,
    slot_key_json TEXT NOT NULL,
    current_attempt_id TEXT NOT NULL,
    created_at TEXT NOT NULL
) STRICT;
CREATE TABLE IF NOT EXISTS attempts (
    attempt_id TEXT PRIMARY KEY,
    slot_id TEXT NOT NULL,
    lifecycle TEXT NOT NULL,
    semantic_digest TEXT,
    correction_predecessor TEXT,
    handle_digest TEXT UNIQUE,
    reservation_json TEXT NOT NULL,
    committed_outcome_json TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (slot_id) REFERENCES slots(slot_id)
) STRICT;
CREATE TABLE IF NOT EXISTS prepared_effects (
    attempt_id TEXT NOT NULL,
    path TEXT NOT NULL,
    artifact_kind TEXT NOT NULL,
    content_digest TEXT NOT NULL,
    canonical_bytes BLOB NOT NULL,
    delivery_status TEXT NOT NULL,
    canonicalization_profile TEXT NOT NULL,
    semantic_fingerprint TEXT NOT NULL,
    PRIMARY KEY (attempt_id, path),
    FOREIGN KEY (attempt_id) REFERENCES attempts(attempt_id)
) STRICT;
CREATE TABLE IF NOT EXISTS finalization_effects (
    attempt_id TEXT NOT NULL,
    effect_name TEXT NOT NULL,
    result_json TEXT NOT NULL,
    acknowledged_at TEXT NOT NULL,
    PRIMARY KEY (attempt_id, effect_name),
    FOREIGN KEY (attempt_id) REFERENCES attempts(attempt_id)
) STRICT;
CREATE TABLE IF NOT EXISTS response_commits (
    attempt_id TEXT PRIMARY KEY,
    required_effect_names_json TEXT NOT NULL,
    outcome_json TEXT NOT NULL,
    replay_projection_json TEXT NOT NULL,
    committed_at TEXT NOT NULL,
    FOREIGN KEY (attempt_id) REFERENCES attempts(attempt_id)
) STRICT;
CREATE TRIGGER IF NOT EXISTS reject_late_finalization_effect
BEFORE INSERT ON finalization_effects
WHEN (
    SELECT lifecycle FROM attempts WHERE attempt_id = NEW.attempt_id
) = 'RESPONSE_COMMITTED'
BEGIN
    SELECT RAISE(ABORT, 'finalization-effect-after-response-commit');
END;
CREATE TABLE IF NOT EXISTS head_claims (
    head_key TEXT PRIMARY KEY,
    recipe_execution_id TEXT NOT NULL,
    cycle_id TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    part_id TEXT NOT NULL,
    head_json TEXT NOT NULL
) STRICT;
CREATE TABLE IF NOT EXISTS preflight_projections (
    recipe_execution_id TEXT NOT NULL,
    installation_version TEXT NOT NULL,
    step_name TEXT NOT NULL,
    plan_set_id TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    part_id TEXT NOT NULL,
    PRIMARY KEY (recipe_execution_id, installation_version, step_name)
) STRICT;
CREATE TABLE IF NOT EXISTS disposition_projections (
    installation_version TEXT NOT NULL,
    authority_digest TEXT NOT NULL,
    plan_digest TEXT NOT NULL,
    report_digest TEXT NOT NULL,
    report_path TEXT NOT NULL,
    association_digest TEXT NOT NULL,
    association_path TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    PRIMARY KEY (installation_version, authority_digest, plan_digest)
) STRICT;
"""

_METADATA_SCHEMA_VERSION = "1"
_DATABASE_MODE = 0o600
_DIRECTORY_MODE = 0o700

__all__ = [
    "_SCHEMA_SQL",
    "_METADATA_SCHEMA_VERSION",
    "_DATABASE_MODE",
    "_DIRECTORY_MODE",
]
