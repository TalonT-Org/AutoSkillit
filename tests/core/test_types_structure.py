"""Tests for core/types.py split into focused sub-modules (P8-F2)."""

from __future__ import annotations


def test_enums_importable_from_sub_module():
    from autoskillit.core._type_enums import (
        RetryReason,
    )

    assert issubclass(RetryReason, str)


def test_protocols_importable_from_sub_module():
    from typing import Protocol as TypingProtocol

    from autoskillit.core._type_protocols import GatePolicy, HeadlessExecutor

    assert issubclass(GatePolicy, TypingProtocol)
    assert GatePolicy.__module__ == "autoskillit.core._type_protocols"
    assert issubclass(HeadlessExecutor, TypingProtocol)
    assert HeadlessExecutor.__module__ == "autoskillit.core._type_protocols"


def test_types_hub_backward_compat():
    """All symbols must still be importable from autoskillit.core.types."""
    import dataclasses
    from typing import Protocol as TypingProtocol

    from autoskillit.core.types import (
        FREE_RANGE_TOOLS,
        GATED_TOOLS,
        FailureRecord,
        GatePolicy,
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
    assert issubclass(GatePolicy, TypingProtocol)  # _type_protocols — Protocol
    assert issubclass(HeadlessExecutor, TypingProtocol)  # _type_protocols — Protocol
    assert callable(extract_skill_name)  # _type_helpers — function


def test_types_hub_line_count_under_threshold():
    """After split, core/types.py must be under 200 lines (re-export hub only)."""
    from autoskillit.core import paths

    types_path = paths.pkg_root() / "core" / "types.py"
    lines = types_path.read_text().splitlines()
    assert len(lines) < 200, f"types.py has {len(lines)} lines; expected re-export hub only"
