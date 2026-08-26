"""Tests for core/types.py split into focused sub-modules (P8-F2)."""

from __future__ import annotations

from importlib import import_module
from typing import get_args, get_type_hints

import pytest

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


# Issue #4735 — pre-split public-symbol snapshots captured from HEAD before the
# decomposition refactor. The facade trims its own __all__ to exclude the
# moved names, but every name below MUST remain reachable through the original
# import path with object identity preserved.
_PRE_SPLIT_ENUM_NAMES: frozenset[str] = frozenset(
    {
        "RetryReason",
        "MergeFailedStep",
        "MergeState",
        "RestartScope",
        "SkillExecutionRole",
        "SkillSource",
        "SkillInvalidityKind",
        "RemediationAction",
        "RecipeSource",
        "ClaudeFlags",
        "VARIADIC_CLAUDE_FLAGS",
        "NON_VARIADIC_CLAUDE_FLAGS",
        "OutputFormat",
        "Severity",
        "TerminationReason",
        "TerminationAction",
        "KillReason",
        "ChannelConfirmation",
        "SessionOutcome",
        "HookTrustPolicy",
        "ObserverStatus",
        "CliSubtype",
        "ChannelBStatus",
        "PRState",
        "SessionType",
        "session_type_for_skill_execution_role",
        "FleetErrorCode",
        "ExplorationFailureCode",
        "FeatureLifecycle",
        "IssueLabelState",
        "DispatchGateType",
        "ClaudeContentBlockType",
        "FaultDomain",
        "InfraExitCategory",
        "BackendEventKind",
        "CodexEventType",
        "CodexItemType",
        "SynthesisStrategy",
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
    }
)
_PRE_SPLIT_CONSTANT_NAMES: frozenset[str] = frozenset(
    {
        "OUTPUT_DISCIPLINE_POLICY_VERSION",
        "OUTPUT_DISCIPLINE_BLOCK",
        "OUTPUT_DISCIPLINE_BLOCK_SHA256",
        "OUTPUT_DISCIPLINE_COMBINED_SHA256",
        "OUTPUT_DISCIPLINE_DIGEST",
        "OUTPUT_DISCIPLINE_REQUIRED_SKILLS",
        "RETIRED_SKILL_NAMES",
        "KNOWN_UNAFFECTED_SKILL_IDS",
        "RETIRED_AGENT_NAMES",
        "RETIRED_INTAKE_RULE_IDS",
        "RETIRED_INSTALL_ARTIFACT_SHAPES",
        "RetiredArtifactShape",
        "DurableArtifactWriterDef",
        "DURABLE_ARTIFACT_WRITERS",
        "SkillContractRemediationDef",
        "SKILL_CONTRACT_REMEDIATIONS",
        "SKILL_COMMAND_PREFIX",
        "SKILL_COMMAND_DISPLAY_MAX",
        "AUTOSKILLIT_SKILL_PREFIX",
        "RETIRED_READINESS_TOKENS",
        "SKILL_FILE_ADVISORY_MAP",
        "SKILL_ACTIVATE_DEPS_REQUIRED",
        "SOUS_CHEF_MANDATORY_SECTIONS",
        "ROUTING_AUTHORITY_CLAUSE",
        "ADMIRAL_DISPATCH_SECTIONS",
        "PR_TELEMETRY_SECTIONS",
        "KNOWN_CI_EVENTS",
        "DATA_MANIFEST_SOURCE_TYPES",
        "REVIEW_APPROACH_MARKER",
        "INVESTIGATION_COMPLETE_MARKER",
        "DRY_WALKTHROUGH_VERIFIED_MARKER",
        "QUOTA_GUARD_DENY_TRIGGER",
        "QUOTA_BUDGET_EXCEEDED_TRIGGER",
        "QUOTA_POST_WARNING_TRIGGER",
        "QUOTA_POST_BUDGET_EXCEEDED_TRIGGER",
        "CONFIG_AUTHORITY_KEYS",
        "CALLER_SOVEREIGN_INGREDIENTS",
        "RUN_PYTHON_PATH_LIKE_ARGS",
        "RUN_PYTHON_SENTINEL_KEYS",
        "SCOPE_DIRECTION_SOURCE_TYPES",
        "WORKTREE_SKILLS",
        "SkillFamilyDef",
        "GITHUB_API_SKILL_FAMILIES",
        "CODEX_ACTIVE_VIEWS_SUBDIR",
        "CODEX_ATTEMPT_RECONCILIATIONS_SUBDIR",
        "CODEX_ATTEMPT_RECONCILIATION_TOMBSTONES_SUBDIR",
        "CODEX_ARCHIVED_SESSIONS_SUBDIR",
        "CODEX_SESSIONS_SUBDIR",
        "SESSION_ADD_DIR_SUBDIR",
        "RECIPE_EXECUTION_ATTESTATION_MISSING_MESSAGE",
        "RECIPE_EXECUTION_INACTIVE_MESSAGE",
        "INFRASTRUCTURE_FAULT_OVERRIDE_CLAUSE",
    }
)
_PRE_SPLIT_PERSISTENCE_NAMES: frozenset[str] = frozenset(
    {
        "CONTEXT_ADMISSION_ENCODING_VERSION",
        "CONTEXT_ADMISSION_TOP_LEVEL_DISCRIMINATORS",
        "CONTEXT_ADMISSION_ENVELOPE_UPCASTERS",
        "ContextAdmissionStreamKey",
        "ContextAdmissionStoreAuthority",
        "ShadowContextAdmissionTargetRecord",
        "ShadowContextAdmissionRecord",
        "DurableContextAdmissionPayload",
        "StoredContextAdmissionEnvelope",
        "ContextAdmissionStoreHealth",
        "ContextAdmissionStreamHealth",
        "ContextAdmissionAccountingResult",
        "ContextAdmissionRecoveryResult",
        "ContextAdmissionInspectionResult",
        "ContextAdmissionLedger",
        "make_stored_context_admission_envelope",
        "encode_stored_context_admission_envelope",
        "decode_stored_context_admission_envelope",
        "context_admission_envelope_header",
        "validate_context_admission_persistence_value",
    }
)


def _recipe_section_facade_names() -> tuple[str, ...]:
    """Resolve the facade's recipe-section re-exports dynamically.

    The hardcoded list was previously a snapshot of the 10 names that the
    legacy ``_type_constants_registries`` facade re-exports from
    ``_type_recipe_sections``. Derive the list from the facade's own
    attributes (intersected with the canonical module's __all__) so that
    adding/removing a re-export in the facade cannot silently drift this
    test's coverage.
    """
    import autoskillit.core.types._type_constants_registries as legacy_mod
    import autoskillit.core.types._type_recipe_sections as canonical_mod

    canonical_names = set(canonical_mod.__all__)
    return tuple(
        sorted(
            name
            for name in legacy_mod.__dict__
            if name in canonical_names
            and legacy_mod.__dict__[name] is getattr(canonical_mod, name, None)
        )
    )


# Per-shard ownership of protocol-v1 facade names. The single source of truth:
# the public surface is derived from this map, so additions or removals must
# change exactly one place (the owning shard's tuple).
_CONTEXT_ADMISSION_SHARD_OWNERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "_type_context_admission_base",
        (
            "CONTEXT_ADMISSION_PROTOCOL_VERSION",
            "ContextAdmissionValidationError",
            "UnsupportedContextAdmissionProtocolError",
        ),
    ),
    (
        "_type_context_admission_identities",
        (
            "ContextSessionId",
            "AgentInstanceId",
            "ContextThreadId",
            "ForkOccurrenceId",
            "TurnId",
            "ProducerInstanceId",
            "ToolCallId",
            "ModelItemId",
            "AdmissionRequestId",
            "AdmissionBatchId",
            "WindowEpochId",
            "TokenizerIdentity",
            "CanonicalSpanId",
            "AdmissionOccurrenceId",
            "AdmissionAttemptId",
            "DeliveryOccurrenceId",
            "AdmissionEventId",
            "AdmissionReservationId",
            "AdmissionWitnessId",
            "AuthoritySourceId",
            "GenerationReservationId",
            "ProtectedPoolOwnerId",
            "RepresentationRevision",
            "RepresentationBindingId",
            "AggregateRevision",
            "AdmissionSequence",
            "IdempotencyNamespace",
            "ContextLineage",
        ),
    ),
    (
        "_type_context_admission_records",
        (
            "ContextWindowSnapshot",
            "CanonicalSpanOwner",
            "CanonicalRepresentationManifest",
            "AdmissionOccurrence",
            "AdmissionBatch",
            "AdmissionReservationKey",
            "AdmissionReservation",
            "AdmissionWitness",
            "RepresentationBindingWitness",
            "EpochFenceProof",
            "ProtectedPoolSpec",
            "AdmissionDecision",
            "AdmissionOccurrenceRecord",
            "AdmissionBatchRecord",
            "GenerationReservationRecord",
            "ClosedEpochAudit",
        ),
    ),
    (
        "_type_context_admission_events",
        (
            "OpenEpochEvent",
            "AuthorityUnavailableEvent",
            "ProposeOccurrenceEvent",
            "ReserveRequestEvent",
            "PrepareBatchEvent",
            "StageHistoryEvent",
            "DispatchRequestEvent",
            "AcceptInputEvent",
            "ReleaseNonAdmissionEvent",
            "RollbackAdmissionEvent",
            "MarkIndeterminateEvent",
            "ResolveIndeterminateAcceptedEvent",
            "ResolveIndeterminateNonAdmissionEvent",
            "ResolveIndeterminateRollbackEvent",
            "StartGenerationEvent",
            "ReconcileGenerationEvent",
            "MarkGenerationIndeterminateEvent",
            "RequestReconciliationEvent",
            "ExpireIdempotencyKeyEvent",
            "RolloverEpochEvent",
            "ContextAdmissionEvent",
        ),
    ),
    (
        "_type_context_admission_effects",
        (
            "ReservationRecordedEffect",
            "ReservationReleasedEffect",
            "OccurrenceStateChangedEffect",
            "ChargeCommittedEffect",
            "GenerationReservationRecordedEffect",
            "GenerationReconciledEffect",
            "ReconciliationQueryRequestedEffect",
            "ReconciliationEscalationEffect",
            "ConflictRejectedEffect",
            "IdempotencyExpiredEffect",
            "ReservationInvalidatedEffect",
            "EpochClosedEffect",
            "QuarantineRecordedEffect",
            "AuthorityUnavailableEffect",
            "AdmissionEffect",
        ),
    ),
    (
        "_type_context_admission_states",
        (
            "ProcessedEventRecord",
            "IdempotencyRecord",
            "ExpiredIdempotencyTombstone",
            "UninitializedContextAdmissionState",
            "ActiveContextAdmissionState",
            "ContextAdmissionState",
            "AdmissionTransition",
            "AdmissionReplay",
        ),
    ),
    (
        "_type_context_admission_coverage",
        (
            "CONTEXT_ADMISSION_COVERAGE",
            "CoverageEvidence",
            "ProducerCoverageDef",
        ),
    ),
)

# Derived from _CONTEXT_ADMISSION_SHARD_OWNERS so the two stay in lock-step
# without a hand-maintained duplicate.
_CONTEXT_ADMISSION_PUBLIC_SURFACE: tuple[str, ...] = tuple(
    name for _, names in _CONTEXT_ADMISSION_SHARD_OWNERS for name in names
)


def test_context_admission_facade_public_surface_is_frozen() -> None:
    facade = import_module("autoskillit.core.types._type_context_admission")

    # Set comparison because the derived surface follows shard-ownership order
    # while facade.__all__ interleaves coverage near the top; order does not
    # affect user-visible behaviour, only the frozen set does.
    assert set(facade.__all__) == set(_CONTEXT_ADMISSION_PUBLIC_SURFACE)


def test_context_admission_shard_ownership_covers_public_surface() -> None:
    owned_names = tuple(name for _, names in _CONTEXT_ADMISSION_SHARD_OWNERS for name in names)

    assert len(owned_names) == len(set(owned_names))
    assert set(owned_names) == set(_CONTEXT_ADMISSION_PUBLIC_SURFACE)


@pytest.mark.parametrize(
    ("shard_stem", "owned_names"),
    _CONTEXT_ADMISSION_SHARD_OWNERS,
    ids=[shard_stem for shard_stem, _ in _CONTEXT_ADMISSION_SHARD_OWNERS],
)
def test_context_admission_facade_reexports_owned_shard_objects(
    shard_stem: str,
    owned_names: tuple[str, ...],
) -> None:
    """Each shard remains directly importable and owns its facade bindings."""
    shard = import_module(f"autoskillit.core.types.{shard_stem}")
    facade = import_module("autoskillit.core.types._type_context_admission")

    for name in owned_names:
        assert getattr(facade, name) is getattr(shard, name)


def test_context_admission_package_hub_preserves_facade_identity() -> None:
    facade = import_module("autoskillit.core.types._type_context_admission")
    hub = import_module("autoskillit.core.types")

    for name in _CONTEXT_ADMISSION_PUBLIC_SURFACE:
        assert getattr(hub, name) is getattr(facade, name)


def test_context_admission_private_codec_and_producer_surface_identity() -> None:
    base = import_module("autoskillit.core.types._type_context_admission_base")
    envelope = import_module("autoskillit.core.types._type_context_admission_persistence_envelope")
    reducer = import_module("autoskillit.core.context_admission")
    enums = import_module("autoskillit.core.types._type_enums_context_admission")

    for name in ("_ContractValue", "_encode", "_decode"):
        assert getattr(envelope, name) is getattr(base, name), name
    assert enums.ProducerSurface is reducer.ProducerSurface


def test_context_admission_registered_types_resolve_annotations_in_owning_shards() -> None:
    base = import_module("autoskillit.core.types._type_context_admission_base")

    assert base._TYPE_REGISTRY
    for name, contract_type in base._TYPE_REGISTRY.items():
        assert get_type_hints(contract_type), f"{name} has no resolved type hints"


def test_context_admission_event_union_is_closed_and_ordered() -> None:
    events = import_module("autoskillit.core.types._type_context_admission_events")
    facade = import_module("autoskillit.core.types._type_context_admission")

    assert facade.ContextAdmissionEvent is events.ContextAdmissionEvent
    assert get_args(events.ContextAdmissionEvent) == (
        events.OpenEpochEvent,
        events.AuthorityUnavailableEvent,
        events.ProposeOccurrenceEvent,
        events.ReserveRequestEvent,
        events.PrepareBatchEvent,
        events.StageHistoryEvent,
        events.DispatchRequestEvent,
        events.AcceptInputEvent,
        events.ReleaseNonAdmissionEvent,
        events.RollbackAdmissionEvent,
        events.MarkIndeterminateEvent,
        events.ResolveIndeterminateAcceptedEvent,
        events.ResolveIndeterminateNonAdmissionEvent,
        events.ResolveIndeterminateRollbackEvent,
        events.StartGenerationEvent,
        events.ReconcileGenerationEvent,
        events.MarkGenerationIndeterminateEvent,
        events.RequestReconciliationEvent,
        events.ExpireIdempotencyKeyEvent,
        events.RolloverEpochEvent,
    )


def test_context_admission_effect_union_is_closed_and_ordered() -> None:
    effects = import_module("autoskillit.core.types._type_context_admission_effects")
    facade = import_module("autoskillit.core.types._type_context_admission")

    assert facade.AdmissionEffect is effects.AdmissionEffect
    assert get_args(effects.AdmissionEffect) == (
        effects.ReservationRecordedEffect,
        effects.ReservationReleasedEffect,
        effects.OccurrenceStateChangedEffect,
        effects.ChargeCommittedEffect,
        effects.GenerationReservationRecordedEffect,
        effects.GenerationReconciledEffect,
        effects.ReconciliationQueryRequestedEffect,
        effects.ReconciliationEscalationEffect,
        effects.ConflictRejectedEffect,
        effects.IdempotencyExpiredEffect,
        effects.ReservationInvalidatedEffect,
        effects.EpochClosedEffect,
        effects.QuarantineRecordedEffect,
        effects.AuthorityUnavailableEffect,
    )


def test_decomposition_preserves_public_symbol_set() -> None:
    """Every pre-split _type_enums / _type_constants __all__ entry must remain
    reachable through the original facade path with object identity preserved."""
    # Hub __all__ (union of all shards) preserves every original name.
    import autoskillit.core.types as types_hub
    import autoskillit.core.types._type_constants as constants_mod
    import autoskillit.core.types._type_enums as enums_mod

    expected_all = _PRE_SPLIT_ENUM_NAMES | _PRE_SPLIT_CONSTANT_NAMES
    assert expected_all <= set(types_hub.__all__), (
        f"Names missing from core.types.__all__: {sorted(expected_all - set(types_hub.__all__))}"
    )

    # Each facade's own __all__ must be a strict subset of the pre-split snapshot —
    # any name that was moved to a sibling shard must NOT reappear in the facade's
    # __all__, otherwise the hub's concatenated __all__ would carry duplicates.
    assert set(enums_mod.__all__) < _PRE_SPLIT_ENUM_NAMES, (
        f"Names unexpectedly re-added to _type_enums.__all__: "
        f"{sorted(set(enums_mod.__all__) - _PRE_SPLIT_ENUM_NAMES)}"
    )
    assert set(constants_mod.__all__) < _PRE_SPLIT_CONSTANT_NAMES, (
        f"Names unexpectedly re-added to _type_constants.__all__: "
        f"{sorted(set(constants_mod.__all__) - _PRE_SPLIT_CONSTANT_NAMES)}"
    )

    # Identity preserved: name in facade and the same name imported directly from
    # the new shard resolve to the exact same object (no wrapping).
    from autoskillit.core.types._type_enums_context_admission import (
        AdmissionState,
        ProducerSurface,
    )

    assert enums_mod.AdmissionState is AdmissionState
    assert enums_mod.ProducerSurface is ProducerSurface

    from autoskillit.core.types._type_constants_durable_writers import (
        DURABLE_ARTIFACT_WRITERS,
    )
    from autoskillit.core.types._type_constants_retirements import RETIRED_SKILL_NAMES
    from autoskillit.core.types._type_constants_skill_contract import (
        SKILL_CONTRACT_REMEDIATIONS,
    )

    assert constants_mod.RETIRED_SKILL_NAMES is RETIRED_SKILL_NAMES
    assert constants_mod.SKILL_CONTRACT_REMEDIATIONS is SKILL_CONTRACT_REMEDIATIONS
    assert constants_mod.DURABLE_ARTIFACT_WRITERS is DURABLE_ARTIFACT_WRITERS

    # Every pre-split name resolves through its original facade path.
    for name in _PRE_SPLIT_ENUM_NAMES:
        assert hasattr(enums_mod, name), f"_type_enums.{name} missing after decomposition"
    for name in _PRE_SPLIT_CONSTANT_NAMES:
        assert hasattr(constants_mod, name), f"_type_constants.{name} missing after decomposition"

    # Wavefront 2 split: persistence facade + envelope shard.
    import autoskillit.core.types._type_context_admission_persistence as persistence_mod
    import autoskillit.core.types._type_context_admission_persistence_envelope as envelope_mod

    # Hub __all__ preserves every pre-split persistence name.
    assert _PRE_SPLIT_PERSISTENCE_NAMES <= set(types_hub.__all__), (
        f"Persistence names missing from core.types.__all__: "
        f"{sorted(_PRE_SPLIT_PERSISTENCE_NAMES - set(types_hub.__all__))}"
    )

    # Facade __all__ must be a strict subset of the pre-split snapshot —
    # moved names belong only to the sibling shard.
    assert set(persistence_mod.__all__) < _PRE_SPLIT_PERSISTENCE_NAMES, (
        f"Names unexpectedly re-added to _type_context_admission_persistence.__all__: "
        f"{sorted(set(persistence_mod.__all__) - _PRE_SPLIT_PERSISTENCE_NAMES)}"
    )

    # Identity preserved: each pre-split name resolves through its owning shard.
    for name in _PRE_SPLIT_PERSISTENCE_NAMES:
        owner = envelope_mod if name in envelope_mod.__all__ else persistence_mod
        assert getattr(types_hub, name) is getattr(owner, name), name


@pytest.mark.parametrize("name", _recipe_section_facade_names())
def test_recipe_section_facade_preserves_identity_and_export_ownership(name: str) -> None:
    import autoskillit.core as core_mod
    import autoskillit.core.types as types_hub
    import autoskillit.core.types._type_constants_registries as legacy_mod
    import autoskillit.core.types._type_recipe_sections as canonical_mod

    canonical = getattr(canonical_mod, name)
    assert getattr(legacy_mod, name) is canonical
    assert getattr(types_hub, name) is canonical
    assert getattr(core_mod, name) is canonical
    assert types_hub.__all__.count(name) == 1
    assert name in core_mod.__all__
    assert name in canonical_mod.__all__
    assert name not in legacy_mod.__all__


def test_enums_importable_from_sub_module():
    from autoskillit.core.types._type_enums import (
        RetryReason,
    )

    assert issubclass(RetryReason, str)


def test_protocols_importable_from_sub_module():
    from autoskillit.core.types._type_protocols_execution import HeadlessExecutor
    from autoskillit.core.types._type_protocols_infra import GateState

    assert callable(GateState)
    assert GateState.__module__ == "autoskillit.core.types._type_protocols_infra"
    assert callable(HeadlessExecutor)
    assert HeadlessExecutor.__module__ == "autoskillit.core.types._type_protocols_execution"


def test_types_hub_backward_compat():
    """All symbols must still be importable from autoskillit.core.types."""
    import dataclasses
    from typing import Protocol as TypingProtocol

    from autoskillit.core.types import (
        FREE_RANGE_TOOLS,
        GATED_TOOLS,
        FailureRecord,
        GateState,
        HeadlessExecutor,
        LoadResult,
        RetryReason,
        SkillResult,
        SubprocessResult,
        SubprocessRunner,
        extract_skill_name,
    )

    assert issubclass(RetryReason, str)  # _type_enums — StrEnum
    assert dataclasses.is_dataclass(SubprocessResult)  # _type_subprocess
    assert issubclass(SubprocessRunner, TypingProtocol)  # _type_subprocess — Protocol
    assert isinstance(GATED_TOOLS, frozenset)  # _type_constants
    assert isinstance(FREE_RANGE_TOOLS, frozenset)  # _type_constants
    assert dataclasses.is_dataclass(LoadResult)  # _type_results
    assert dataclasses.is_dataclass(SkillResult)  # _type_results
    assert dataclasses.is_dataclass(FailureRecord)  # _type_results_records
    assert callable(GateState)  # _type_protocols_infra — Protocol
    assert callable(HeadlessExecutor)  # _type_protocols_execution — Protocol
    assert callable(extract_skill_name)  # _type_helpers — function


def test_types_hub_line_count_under_threshold():
    """After split, core/types.py must be under 207 lines (re-export hub only)."""
    from autoskillit.core import paths

    types_path = paths.pkg_root() / "core" / "types" / "__init__.py"
    lines = types_path.read_text().splitlines()
    assert len(lines) < 207, f"types.py has {len(lines)} lines; expected re-export hub only"


def test_launch_id_env_var_in_private_vars() -> None:
    from autoskillit.core import AUTOSKILLIT_PRIVATE_ENV_VARS, LAUNCH_ID_ENV_VAR

    assert LAUNCH_ID_ENV_VAR in AUTOSKILLIT_PRIVATE_ENV_VARS


def test_supports_debug_importable_from_core() -> None:
    from typing import Protocol

    from autoskillit.core import SupportsDebug

    assert issubclass(SupportsDebug, Protocol)


def test_supports_debug_in_core_all() -> None:
    import autoskillit.core as core_mod

    assert hasattr(core_mod, "SupportsDebug")


def test_subprocess_shard_all() -> None:
    from autoskillit.core.types._type_subprocess import __all__

    assert set(__all__) == {
        "ProcessCleanupResult",
        "SubprocessResult",
        "SubprocessRunner",
    }
    assert "_TERMINATION_CONTRACT" not in __all__


def test_subprocess_termination_contract_variable_still_defined() -> None:
    import autoskillit.core.types._type_subprocess as m

    assert hasattr(m, "_TERMINATION_CONTRACT")


def test_phoropter_symbols_importable_from_types_hub() -> None:
    """All phoropter-related symbols must be importable from autoskillit.core.types."""
    import dataclasses

    from autoskillit.core.types import (
        READING_TOKEN_PATTERN,
        CrossDomainAssessment,
        CrossDomainPrescription,
        PhoropterPhaseSkip,
        PhoropterPrescription,
        ReadingToken,
        SynthesisStrategy,
    )

    assert isinstance(READING_TOKEN_PATTERN, str)
    assert issubclass(SynthesisStrategy, str)
    assert dataclasses.is_dataclass(PhoropterPrescription)
    assert dataclasses.is_dataclass(ReadingToken)
    assert dataclasses.is_dataclass(PhoropterPhaseSkip)
    assert dataclasses.is_dataclass(CrossDomainPrescription)
    assert dataclasses.is_dataclass(CrossDomainAssessment)


def test_phoropter_symbols_importable_from_core_gateway() -> None:
    """All seven phoropter-related symbols must resolve via autoskillit.core (lazy stub)."""
    import dataclasses

    from autoskillit.core import (
        READING_TOKEN_PATTERN,
        CrossDomainAssessment,
        CrossDomainPrescription,
        PhoropterPhaseSkip,
        PhoropterPrescription,
        ReadingToken,
        SynthesisStrategy,
    )

    assert isinstance(READING_TOKEN_PATTERN, str)
    assert issubclass(SynthesisStrategy, str)
    assert dataclasses.is_dataclass(PhoropterPrescription)
    assert dataclasses.is_dataclass(ReadingToken)
    assert dataclasses.is_dataclass(PhoropterPhaseSkip)
    assert dataclasses.is_dataclass(CrossDomainPrescription)
    assert dataclasses.is_dataclass(CrossDomainAssessment)


def test_phoropter_all_in_types_all() -> None:
    """Every _type_phoropter.__all__ member must appear in core.types.__all__."""
    from autoskillit.core.types import __all__ as types_all
    from autoskillit.core.types._type_phoropter import __all__ as phoropter_all

    missing = set(phoropter_all) - set(types_all)
    assert not missing, f"Missing from core.types.__all__: {missing}"
