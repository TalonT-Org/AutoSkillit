"""Tests for core/types.py split into focused sub-modules (P8-F2)."""

from __future__ import annotations


def test_enums_importable_from_sub_module():
    from autoskillit.core._type_enums import (
        RetryReason,
    )

    assert issubclass(RetryReason, str)


def test_protocols_importable_from_sub_module():
    from autoskillit.core._type_protocols import GatePolicy, HeadlessExecutor

    assert callable(GatePolicy)
    assert callable(HeadlessExecutor)


def test_types_hub_backward_compat():
    """All symbols must still be importable from autoskillit.core.types."""
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

    assert RetryReason is not None  # _type_enums
    assert SubprocessResult is not None  # _type_subprocess
    assert SubprocessRunner is not None  # _type_subprocess
    assert GATED_TOOLS is not None  # _type_constants
    assert FREE_RANGE_TOOLS is not None  # _type_constants
    assert LoadResult is not None  # _type_results
    assert SkillResult is not None  # _type_results
    assert FailureRecord is not None  # _type_results
    assert GatePolicy is not None  # _type_protocols
    assert HeadlessExecutor is not None  # _type_protocols
    assert extract_skill_name is not None  # _type_helpers


def test_types_hub_line_count_under_threshold():
    """After split, core/types.py must be under 200 lines (re-export hub only)."""
    from autoskillit.core import paths

    types_path = paths.pkg_root() / "core" / "types.py"
    lines = types_path.read_text().splitlines()
    assert len(lines) < 200, f"types.py has {len(lines)} lines; expected re-export hub only"
