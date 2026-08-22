"""Tests for core/types.py split into focused sub-modules (P8-F2)."""

from __future__ import annotations

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
    assert dataclasses.is_dataclass(FailureRecord)  # _type_results
    assert callable(GateState)  # _type_protocols_infra — Protocol
    assert callable(HeadlessExecutor)  # _type_protocols_execution — Protocol
    assert callable(extract_skill_name)  # _type_helpers — function


def test_types_hub_line_count_under_threshold():
    """After split, core/types.py must be under 200 lines (re-export hub only)."""
    from autoskillit.core import paths

    types_path = paths.pkg_root() / "core" / "types" / "__init__.py"
    lines = types_path.read_text().splitlines()
    assert len(lines) < 200, f"types.py has {len(lines)} lines; expected re-export hub only"


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
