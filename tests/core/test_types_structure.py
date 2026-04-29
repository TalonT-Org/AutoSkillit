"""Tests for core/types.py split into focused sub-modules (P8-F2)."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def test_enums_importable_from_sub_module():
    from autoskillit.core._type_enums import (
        RetryReason,
    )

    assert issubclass(RetryReason, str)


def test_protocols_importable_from_sub_module():
    from autoskillit.core._type_protocols_execution import HeadlessExecutor
    from autoskillit.core._type_protocols_infra import GateState

    assert callable(GateState)
    assert GateState.__module__ == "autoskillit.core._type_protocols_infra"
    assert callable(HeadlessExecutor)
    assert HeadlessExecutor.__module__ == "autoskillit.core._type_protocols_execution"


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

    types_path = paths.pkg_root() / "core" / "types.py"
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
