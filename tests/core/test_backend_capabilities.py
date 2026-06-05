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


def test_backend_capabilities_all_false_empty_constructible() -> None:
    """BackendCapabilities() with zero args yields the declared default for every field."""
    from autoskillit.core import BackendCapabilities

    caps = BackendCapabilities()
    fields = dataclasses.fields(BackendCapabilities)
    hints = typing.get_type_hints(BackendCapabilities)

    for f in fields:
        hint = hints.get(f.name)
        actual = getattr(caps, f.name)
        if hint is bool:
            assert actual is f.default, (
                f"{f.name!r}: zero-arg yields {actual!r}, declared default is {f.default!r}"
            )
        elif hint == frozenset[str]:
            assert actual == frozenset(), f"{f.name!r}: expected frozenset(), got {actual!r}"


def test_backend_capabilities_field_count():
    """Field count by type must match the dataclass definition."""
    from autoskillit.core import BackendCapabilities

    fields = dataclasses.fields(BackendCapabilities)
    hints = typing.get_type_hints(BackendCapabilities)
    bool_fields = {f.name for f in fields if hints[f.name] is bool}
    frozenset_fields = {f.name for f in fields if hints[f.name] == frozenset[str]}
    str_fields = {f.name for f in fields if hints[f.name] is str}
    tuple_fields = {f.name for f in fields if hints[f.name] == tuple[str, ...]}
    assert bool_fields == {
        "channel_b_capable",
        "has_unguarded_filesystem_access",
        "pty_required",
        "session_resume_capable",
        "skill_injection_capable",
        "supports_thinking_blocks",
        "supports_claude_format_stdout",
        "exit_code_is_terminal",
        "mcp_config_capable",
        "food_truck_capable",
        "triage_capable",
        "supports_context_exhaustion_detection",
        "project_local_skills_capable",
        "supports_tool_list_changed",
        "replay_capable",
        "record_capable",
        "anthropic_provider_capable",
        "plugin_install_capable",
        "inspector_capable",
        "supports_context_window_suffix",
    }
    assert frozenset_fields == {
        "completion_record_types",
        "session_record_types",
        "required_skill_fields",
        "required_session_files",
        "session_dir_symlinks",
        "applicable_guards",
        "mcp_env_forward_vars",
    }
    assert str_fields == {
        "min_version",
        "version_check_command",
        "process_name",
        "skills_subdir",
        "hook_config_format",
        "write_detection_strategy",
        "patch_format",
        "default_skill_sandbox_mode",
    }
    assert tuple_fields == {"env_denylist_prefixes"}


def test_backend_capabilities_field_names_locked():
    """Field names match the design specification."""
    from autoskillit.core import BackendCapabilities

    expected = {
        "channel_b_capable",
        "has_unguarded_filesystem_access",
        "pty_required",
        "session_resume_capable",
        "skill_injection_capable",
        "supports_thinking_blocks",
        "supports_claude_format_stdout",
        "exit_code_is_terminal",
        "mcp_config_capable",
        "food_truck_capable",
        "completion_record_types",
        "session_record_types",
        "triage_capable",
        "supports_context_exhaustion_detection",
        "project_local_skills_capable",
        "required_skill_fields",
        "required_session_files",
        "session_dir_symlinks",
        "applicable_guards",
        "env_denylist_prefixes",
        "min_version",
        "version_check_command",
        "process_name",
        "skills_subdir",
        "hook_config_format",
        "write_detection_strategy",
        "patch_format",
        "default_skill_sandbox_mode",
        "supports_tool_list_changed",
        "mcp_env_forward_vars",
        "replay_capable",
        "record_capable",
        "anthropic_provider_capable",
        "plugin_install_capable",
        "inspector_capable",
        "supports_context_window_suffix",
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
    assert CLAUDE_CODE_CAPABILITIES.supports_claude_format_stdout is True
    assert CLAUDE_CODE_CAPABILITIES.exit_code_is_terminal is False
    assert CLAUDE_CODE_CAPABILITIES.mcp_config_capable is False
    assert CLAUDE_CODE_CAPABILITIES.food_truck_capable is True
    assert CLAUDE_CODE_CAPABILITIES.completion_record_types == frozenset({"result"})
    assert CLAUDE_CODE_CAPABILITIES.session_record_types == frozenset({"assistant"})
    assert CLAUDE_CODE_CAPABILITIES.triage_capable is True
    assert CLAUDE_CODE_CAPABILITIES.supports_context_exhaustion_detection is True
    assert CLAUDE_CODE_CAPABILITIES.project_local_skills_capable is True
    assert CLAUDE_CODE_CAPABILITIES.required_skill_fields == frozenset({"name", "description"})
    assert CLAUDE_CODE_CAPABILITIES.required_session_files == frozenset()
    assert CLAUDE_CODE_CAPABILITIES.session_dir_symlinks == frozenset()
    assert CLAUDE_CODE_CAPABILITIES.applicable_guards == frozenset({"skill_load_guard"})
    assert CLAUDE_CODE_CAPABILITIES.env_denylist_prefixes == ()
    assert CLAUDE_CODE_CAPABILITIES.min_version == ""
    assert CLAUDE_CODE_CAPABILITIES.version_check_command == "claude --version"
    assert CLAUDE_CODE_CAPABILITIES.process_name == "claude"
    assert CLAUDE_CODE_CAPABILITIES.skills_subdir == ".claude/skills"
    assert CLAUDE_CODE_CAPABILITIES.supports_tool_list_changed is False
    assert CLAUDE_CODE_CAPABILITIES.mcp_env_forward_vars == frozenset()
    assert CLAUDE_CODE_CAPABILITIES.replay_capable is True
    assert CLAUDE_CODE_CAPABILITIES.record_capable is True
    assert CLAUDE_CODE_CAPABILITIES.anthropic_provider_capable is True
    assert CLAUDE_CODE_CAPABILITIES.plugin_install_capable is True
    assert CLAUDE_CODE_CAPABILITIES.inspector_capable is True
    assert CLAUDE_CODE_CAPABILITIES.supports_context_window_suffix is True
    assert CLAUDE_CODE_CAPABILITIES.hook_config_format == ""
    assert CLAUDE_CODE_CAPABILITIES.write_detection_strategy == "tool_names"
    assert CLAUDE_CODE_CAPABILITIES.patch_format == "unified_diff"
    assert CLAUDE_CODE_CAPABILITIES.default_skill_sandbox_mode == ""


def test_backend_capabilities_frozenset_defaults():
    """Five new frozenset[str] fields default to frozenset()."""
    from autoskillit.core import BackendCapabilities

    instance = BackendCapabilities(
        channel_b_capable=True,
        pty_required=True,
        session_resume_capable=True,
        skill_injection_capable=True,
        supports_thinking_blocks=True,
        supports_claude_format_stdout=True,
        exit_code_is_terminal=False,
        mcp_config_capable=False,
        food_truck_capable=True,
        completion_record_types=frozenset(),
        session_record_types=frozenset(),
    )
    new_frozenset_fields = {
        "required_skill_fields",
        "required_session_files",
        "session_dir_symlinks",
        "applicable_guards",
        "mcp_env_forward_vars",
    }
    field_names = {f.name for f in dataclasses.fields(instance)}
    hints = typing.get_type_hints(BackendCapabilities)
    for name in new_frozenset_fields:
        assert name in field_names
        assert hints[name] == frozenset[str]
        assert getattr(instance, name) == frozenset()


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


def test_cmd_spec_has_is_resume_bool_field():
    """CmdSpec.is_resume is a bool field (typed and present)."""
    import typing

    from autoskillit.core import CmdSpec

    hints = typing.get_type_hints(CmdSpec)
    assert "is_resume" in hints, "CmdSpec must have an is_resume field"
    assert hints["is_resume"] is bool
