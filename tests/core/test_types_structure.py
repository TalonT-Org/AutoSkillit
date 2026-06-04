"""Tests for core/types.py split into focused sub-modules (P8-F2)."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


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

    assert set(__all__) == {"SubprocessResult", "SubprocessRunner"}
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
    assert callable(PhoropterPrescription)
    assert callable(ReadingToken)
    assert callable(PhoropterPhaseSkip)
    assert callable(CrossDomainPrescription)
    assert callable(CrossDomainAssessment)


def test_phoropter_all_in_types_all() -> None:
    """Every _type_phoropter.__all__ member must appear in core.types.__all__."""
    from autoskillit.core.types import __all__ as types_all
    from autoskillit.core.types._type_phoropter import __all__ as phoropter_all

    missing = set(phoropter_all) - set(types_all)
    assert not missing, f"Missing from core.types.__all__: {missing}"
