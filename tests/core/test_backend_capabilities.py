"""Tests for BackendCapabilities frozen invariants and CLAUDE_CODE_CAPABILITIES field values."""

from __future__ import annotations

import dataclasses
import typing

import pytest

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def test_backend_capabilities_importable_from_core():
    """BackendCapabilities and CLAUDE_CODE_CAPABILITIES resolve via the public gateway."""
    from autoskillit.core import CLAUDE_CODE_CAPABILITIES, BackendCapabilities

    assert dataclasses.is_dataclass(BackendCapabilities)
    assert isinstance(CLAUDE_CODE_CAPABILITIES, BackendCapabilities)


def test_backend_capabilities_in_core_all():
    """Both symbols appear in the core gateway __all__."""
    from autoskillit.core import __all__ as core_all

    assert "BackendCapabilities" in core_all
    assert "CLAUDE_CODE_CAPABILITIES" in core_all


def test_backend_capabilities_in_types_all():
    """Both symbols appear in the types hub __all__."""
    from autoskillit.core.types import __all__ as types_all

    assert "BackendCapabilities" in types_all
    assert "CLAUDE_CODE_CAPABILITIES" in types_all


def test_backend_capabilities_is_frozen_dataclass():
    """BackendCapabilities instances are immutable."""
    from autoskillit.core import CLAUDE_CODE_CAPABILITIES

    with pytest.raises(dataclasses.FrozenInstanceError):
        CLAUDE_CODE_CAPABILITIES.pty_required = False  # type: ignore[misc]


def test_backend_capabilities_slots():
    """BackendCapabilities uses __slots__ (no __dict__)."""
    from autoskillit.core import CLAUDE_CODE_CAPABILITIES

    assert not hasattr(CLAUDE_CODE_CAPABILITIES, "__dict__")


def test_backend_capabilities_field_count():
    """Exactly 8 fields: 6 bool + 2 frozenset[str]."""
    from autoskillit.core import BackendCapabilities

    fields = dataclasses.fields(BackendCapabilities)
    hints = typing.get_type_hints(BackendCapabilities)
    assert len(fields) == 8
    bool_fields = [f for f in fields if hints[f.name] is bool]
    frozenset_fields = [f for f in fields if hints[f.name] == frozenset[str]]
    assert len(bool_fields) == 6
    assert len(frozenset_fields) == 2


def test_backend_capabilities_field_names_locked():
    """Field names match the design specification."""
    from autoskillit.core import BackendCapabilities

    expected = {
        "channel_b_capable",
        "pty_required",
        "session_resume_capable",
        "skill_injection_capable",
        "supports_thinking_blocks",
        "exit_code_is_terminal",
        "completion_record_types",
        "session_record_types",
    }
    actual = {f.name for f in dataclasses.fields(BackendCapabilities)}
    assert actual == expected


def test_claude_code_capabilities_field_values():
    """CLAUDE_CODE_CAPABILITIES field values match the Part 13.2 design."""
    from autoskillit.core import CLAUDE_CODE_CAPABILITIES

    assert CLAUDE_CODE_CAPABILITIES.channel_b_capable is True
    assert CLAUDE_CODE_CAPABILITIES.pty_required is True
    assert CLAUDE_CODE_CAPABILITIES.session_resume_capable is True
    assert CLAUDE_CODE_CAPABILITIES.skill_injection_capable is True
    assert CLAUDE_CODE_CAPABILITIES.supports_thinking_blocks is True
    assert CLAUDE_CODE_CAPABILITIES.exit_code_is_terminal is False
    assert CLAUDE_CODE_CAPABILITIES.completion_record_types == frozenset({"result"})
    assert CLAUDE_CODE_CAPABILITIES.session_record_types == frozenset({"assistant"})


def test_no_autoskillit_imports():
    """_type_backend.py has zero imports from autoskillit.* (IL-0 constraint)."""
    from autoskillit.core import paths

    backend_path = paths.pkg_root() / "core" / "types" / "_type_backend.py"
    assert backend_path.exists(), f"Source file not found: {backend_path}"
    source = backend_path.read_text()
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("from autoskillit") or stripped.startswith("import autoskillit"):
            pytest.fail(f"IL-0 violation: {stripped}")


def test_backend_capabilities_module_all():
    """__all__ contains exactly the two public symbols."""
    from autoskillit.core.types._type_backend import __all__

    assert set(__all__) == {"BackendCapabilities", "CLAUDE_CODE_CAPABILITIES"}
