"""Context-admission StrEnum discriminators.

Zero autoskillit imports. Provides the closed vocabularies for the
context-admission protocol v1 (lifecycle states, decision kinds, accounting
statuses, storage health, capacity domains, generation states, measurement
authority, coverage, reserves, witnesses, producer surfaces).

Sibling shard of ``_type_enums.py`` — see the package hub ``__init__.py`` for
the re-export contract that preserves every original import path.
"""

from __future__ import annotations

from enum import StrEnum, unique

__all__ = [
    "AdmissionState",
    "AdmissionDecisionKind",
    "ContextAdmissionAccountingStatus",
    "ContextAdmissionStorageHealthStatus",
    "ContextAdmissionStorageFailureReason",
    "ChargeDomain",
    "GenerationState",
    "MeasurementKind",
    "CoverageState",
    "CoverageEvidenceKind",
    "ReserveClass",
    "WitnessKind",
    "ProducerSurface",
]


@unique
class AdmissionState(StrEnum):
    """Lifecycle states for one immutable context-admission occurrence."""

    PROPOSED = "proposed"
    RESERVED = "reserved"
    PREPARED = "prepared"
    HISTORY_STAGED = "history_staged"
    REQUEST_DISPATCHED = "request_dispatched"
    COMMITTED = "committed"
    RELEASED = "released"
    ROLLED_BACK = "rolled_back"
    INVALIDATED = "invalidated"
    INDETERMINATE = "indeterminate"
    QUARANTINED = "quarantined"


@unique
class AdmissionDecisionKind(StrEnum):
    """Closed decision vocabulary returned by the protocol-v1 reducer."""

    WOULD_ADMIT = "would_admit"
    WOULD_REJECT = "would_reject"
    WATERMARK_UNAVAILABLE = "watermark_unavailable"
    UPSTREAM_GATED = "upstream_gated"
    NOOP_IDEMPOTENT = "noop_idempotent"
    CONFLICT = "conflict"
    IDEMPOTENCY_EXPIRED = "idempotency_expired"
    QUARANTINED = "quarantined"


@unique
class ContextAdmissionAccountingStatus(StrEnum):
    """Closed outcome vocabulary for durable context accounting."""

    RECORDED = "recorded"
    EXACT_REPLAY = "exact_replay"
    SEMANTIC_REJECTION = "semantic_rejection"
    RECONCILIATION_REQUIRED = "reconciliation_required"
    PROTOCOL_QUARANTINED = "protocol_quarantined"
    CONTENDED = "contended"
    STORAGE_FAIL_CLOSED = "storage_fail_closed"


@unique
class ContextAdmissionStorageHealthStatus(StrEnum):
    """Storage health, intentionally separate from protocol lifecycle."""

    UNINITIALIZED = "uninitialized"
    HEALTHY = "healthy"
    FAIL_CLOSED = "fail_closed"


@unique
class ContextAdmissionStorageFailureReason(StrEnum):
    """Bounded reasons for sticky storage-health failure."""

    CONFIGURATION = "configuration"
    IO = "io"
    SECURITY_IDENTITY = "security_identity"
    INTEGRITY = "integrity"
    UNSUPPORTED_SCHEMA = "unsupported_schema"
    UNSUPPORTED_ENCODING = "unsupported_encoding"
    UNSUPPORTED_PROTOCOL = "unsupported_protocol"
    REPLAY_MISMATCH = "replay_mismatch"
    IDENTITY_MISMATCH = "identity_mismatch"
    AMBIGUOUS_RECOVERY = "ambiguous_recovery"


@unique
class ChargeDomain(StrEnum):
    """Capacity domains kept separate by the admission contract."""

    INPUT_CONTEXT = "input_context"
    OUTPUT_GENERATION = "output_generation"


@unique
class GenerationState(StrEnum):
    """Lifecycle of a generated-output allowance."""

    RESERVED = "reserved"
    STREAMING = "streaming"
    RECONCILED = "reconciled"
    INDETERMINATE = "indeterminate"
    QUARANTINED = "quarantined"


@unique
class MeasurementKind(StrEnum):
    """Authority level of a count supplied to the pure reducer."""

    PROVIDER_EXACT = "provider_exact"
    TOKENIZER_EXACT = "tokenizer_exact"
    HOST_ESTIMATE = "host_estimate"
    BYTE_EMERGENCY = "byte_emergency"


@unique
class CoverageState(StrEnum):
    """Evidence-backed observation or authority coverage state."""

    VERIFIED = "verified"
    PARTIAL = "partial"
    UPSTREAM_GATED = "upstream_gated"


@unique
class CoverageEvidenceKind(StrEnum):
    """Primary and inference evidence kinds accepted by the coverage registry."""

    AUTOSKILLIT_SOURCE = "autoskillit_source"
    CODEX_SOURCE = "codex_source"
    CODEX_OFFICIAL_DOC = "codex_official_doc"
    CODEX_RUNTIME_PROBE = "codex_runtime_probe"
    INFERENCE = "inference"


@unique
class ReserveClass(StrEnum):
    """Capability-scoped context reserve classes."""

    ORDINARY = "ordinary"
    SYNTHESIS = "synthesis"
    FINAL_RESPONSE = "final_response"


@unique
class WitnessKind(StrEnum):
    """Closed vocabulary of authoritative admission witnesses."""

    EPOCH_SNAPSHOT = "epoch_snapshot"
    INPUT_COUNTED = "input_counted"
    HISTORY_STAGED = "history_staged"
    REPRESENTATION_BOUND = "representation_bound"
    REQUEST_INCLUDED = "request_included"
    PROVIDER_ACCEPTED = "provider_accepted"
    OUTPUT_USAGE = "output_usage"
    TRUNCATION = "truncation"
    NON_ADMISSION = "non_admission"
    ROLLBACK = "rollback"
    RECONCILIATION = "reconciliation"
    IDEMPOTENCY_EXPIRY = "idempotency_expiry"
    EPOCH_FENCE = "epoch_fence"
    EPOCH_ROLLOVER = "epoch_rollover"


@unique
class ProducerSurface(StrEnum):
    """Every model-visible producer covered by protocol version 1."""

    NATIVE_SHELL = "native_shell"
    UNIFIED_EXEC_AND_WRITE_STDIN = "unified_exec_and_write_stdin"
    APPLY_PATCH = "apply_patch"
    AUTOSKILLIT_MCP = "autoskillit_mcp"
    EXTERNAL_MCP = "external_mcp"
    AUTOSKILLIT_LOCAL_FUNCTION = "autoskillit_local_function"
    OTHER_LOCAL_FUNCTION = "other_local_function"
    MCP_RESOURCE = "mcp_resource"
    CLIENT_PROVIDER_RETRIEVAL = "client_provider_retrieval"
    CODE_MODE_AGGREGATE = "code_mode_aggregate"
    HOSTED_SPECIALIZED_TOOL = "hosted_specialized_tool"
    HOOK_FEEDBACK = "hook_feedback"
    TOOL_ARGUMENT = "tool_argument"
    TOOL_RESULT_ENVELOPE = "tool_result_envelope"
    USER_PROMPT = "user_prompt"
    ASSISTANT_OUTPUT_HISTORY = "assistant_output_history"
    SKILL_PLUGIN_CONTEXT = "skill_plugin_context"
    OTHER_CONTEXT_INJECTION = "other_context_injection"
    HEADLESS_CHILD_PROMPT = "headless_child_prompt"
    PARENT_VISIBLE_CHILD_DELIVERY = "parent_visible_child_delivery"
    COMPACTION_MODEL_WINDOW_TRANSITION = "compaction_model_window_transition"