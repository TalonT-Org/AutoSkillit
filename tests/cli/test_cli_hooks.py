"""Tests for the cli/_hooks.py unified hook registration helpers."""

from __future__ import annotations

import json
from pathlib import Path


# HK1
def test_hooks_module_exists():
    """cli/_hooks.py must exist as a module."""


# HK2
def test_register_pretooluse_hook_creates_new_entry(tmp_path):
    """_register_pretooluse_hook creates a new PreToolUse entry in settings.json."""
    from autoskillit.cli._hooks import _register_pretooluse_hook

    settings = tmp_path / "settings.json"
    _register_pretooluse_hook(settings, matcher="mcp__foo.*", command="python3 -m foo")
    data = json.loads(settings.read_text())
    entries = data["hooks"]["PreToolUse"]
    assert any(e["matcher"] == "mcp__foo.*" for e in entries)


# HK3
def test_register_pretooluse_hook_idempotent_by_matcher(tmp_path):
    """Calling twice with same matcher does not duplicate the entry."""
    from autoskillit.cli._hooks import _register_pretooluse_hook

    settings = tmp_path / "settings.json"
    _register_pretooluse_hook(settings, matcher="mcp__foo.*", command="cmd1")
    _register_pretooluse_hook(settings, matcher="mcp__foo.*", command="cmd2")
    entries = json.loads(settings.read_text())["hooks"]["PreToolUse"]
    assert len([e for e in entries if e["matcher"] == "mcp__foo.*"]) == 1


# HK4
def test_register_pretooluse_hook_idempotent_by_command(tmp_path):
    """Calling twice with same command does not duplicate even with different matcher."""
    from autoskillit.cli._hooks import _register_pretooluse_hook

    settings = tmp_path / "settings.json"
    _register_pretooluse_hook(settings, matcher="mcp__foo.*", command="python3 -m quota")
    _register_pretooluse_hook(settings, matcher="mcp__bar.*", command="python3 -m quota")
    entries = json.loads(settings.read_text())["hooks"]["PreToolUse"]
    commands = [h["command"] for e in entries for h in e.get("hooks", [])]
    assert commands.count("python3 -m quota") == 1


# HK5
def test_register_pretooluse_hook_creates_parent_dirs(tmp_path):
    """_register_pretooluse_hook creates the .claude/ parent directory if missing."""
    from autoskillit.cli._hooks import _register_pretooluse_hook

    settings = tmp_path / ".claude" / "settings.json"
    _register_pretooluse_hook(settings, "mcp__x.*", "python3 -m x")
    assert settings.exists()


# HK6
def test_quota_hook_uses_unified_helper():
    """_register_quota_hook is a thin wrapper calling _register_pretooluse_hook."""
    import inspect

    from autoskillit.cli import _hooks

    src = inspect.getsource(_hooks._register_quota_hook)
    assert "_register_pretooluse_hook" in src


# HK7
def test_remove_clone_hook_uses_unified_helper():
    """_register_remove_clone_guard_hook calls _register_pretooluse_hook."""
    import inspect

    from autoskillit.cli import _hooks

    src = inspect.getsource(_hooks._register_remove_clone_guard_hook)
    assert "_register_pretooluse_hook" in src


# HK8
def test_register_skill_command_guard_appends_to_existing_matcher(tmp_path):
    """_register_skill_command_guard_hook appends command to existing run_skill entry."""
    from autoskillit.cli._hooks import _register_quota_hook, _register_skill_command_guard_hook

    settings = tmp_path / "settings.json"
    _register_quota_hook(settings)  # Creates run_skill matcher entry
    _register_skill_command_guard_hook(settings)  # Should append to that entry
    data = json.loads(settings.read_text())
    entries = data["hooks"]["PreToolUse"]
    run_skill_entries = [e for e in entries if "run_skill" in e.get("matcher", "")]
    # Both commands in same matcher entry
    all_commands = [h["command"] for e in run_skill_entries for h in e.get("hooks", [])]
    assert any("quota_check" in c for c in all_commands)
    assert any("skill_command_guard" in c for c in all_commands)


# HK9
def test_claude_settings_path_user_scope():
    """_claude_settings_path('user') returns ~/.claude/settings.json."""
    from autoskillit.cli._hooks import _claude_settings_path

    p = _claude_settings_path("user")
    assert p == Path.home() / ".claude" / "settings.json"


# HK10
def test_claude_settings_path_project_scope(tmp_path, monkeypatch):
    """_claude_settings_path('project') returns <cwd>/.claude/settings.json."""
    monkeypatch.chdir(tmp_path)
    from autoskillit.cli._hooks import _claude_settings_path

    p = _claude_settings_path("project")
    assert p == tmp_path / ".claude" / "settings.json"


# HK11
def test_registered_hooks_use_absolute_paths(tmp_path):
    """Hook commands written to settings.json must use absolute paths, not python3 -m."""
    from autoskillit.cli._hooks import (
        _register_native_tool_guard_hook,
        _register_quota_hook,
        _register_remove_clone_guard_hook,
        _register_skill_cmd_check_hook,
        _register_skill_command_guard_hook,
    )

    settings = tmp_path / "settings.json"
    _register_quota_hook(settings)
    _register_skill_cmd_check_hook(settings)
    _register_native_tool_guard_hook(settings)
    _register_skill_command_guard_hook(settings)
    _register_remove_clone_guard_hook(settings)
    data = json.loads(settings.read_text())
    for entry in data["hooks"]["PreToolUse"]:
        for hook in entry.get("hooks", []):
            cmd = hook["command"]
            assert "python3 -m" not in cmd, f"Registered hook uses python3 -m: {cmd}"
            assert "${" not in cmd, f"Registered hook uses env var: {cmd}"


# HK12
def test_hooks_py_covers_full_registry():
    """Every hook in HOOK_REGISTRY has a corresponding _register_* function."""
    from autoskillit.cli._hooks import (
        _register_native_tool_guard_hook,
        _register_quota_hook,
        _register_remove_clone_guard_hook,
        _register_skill_cmd_check_hook,
        _register_skill_command_guard_hook,
    )
    from autoskillit.hooks import HOOK_REGISTRY

    all_registry_scripts = {s for h in HOOK_REGISTRY for s in h.scripts}
    register_functions = [
        _register_quota_hook,
        _register_skill_cmd_check_hook,
        _register_native_tool_guard_hook,
        _register_remove_clone_guard_hook,
        _register_skill_command_guard_hook,
    ]
    # Each function registers a script — verify by calling them all
    # and checking that every registry script appears in settings.json
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        settings = Path(td) / "settings.json"
        for fn in register_functions:
            fn(settings)
        data = json.loads(settings.read_text())
        registered_commands = [
            h["command"] for entry in data["hooks"]["PreToolUse"] for h in entry.get("hooks", [])
        ]
        registered_scripts = {cmd.split("/")[-1] for cmd in registered_commands}
        assert all_registry_scripts == registered_scripts, (
            f"Missing: {all_registry_scripts - registered_scripts}, "
            f"Extra: {registered_scripts - all_registry_scripts}"
        )


# HK13
def test_evict_stale_hooks_removes_legacy_formats(tmp_path):
    """install() must remove all legacy autoskillit hook formats before writing fresh ones."""
    from autoskillit.cli._hooks import (
        _evict_stale_autoskillit_hooks,
        _register_quota_hook,
    )

    settings = tmp_path / "settings.json"
    # Seed with three legacy format entries
    legacy_data = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "mcp__.*autoskillit.*__run_skill.*",
                    "hooks": [
                        {"type": "command", "command": "python3 -m autoskillit.hooks.quota_check"},
                    ],
                },
                {
                    "matcher": "mcp__.*autoskillit.*__run_skill.*",
                    "hooks": [
                        {
                            "type": "command",
                            "command": "python3 /old/path/hooks/quota_check.py",
                        },
                    ],
                },
                {
                    "matcher": "mcp__.*autoskillit.*__remove_clone",
                    "hooks": [
                        {
                            "type": "command",
                            "command": "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/remove_clone_guard.py",
                        },
                    ],
                },
                {
                    "matcher": "some_other_matcher",
                    "hooks": [
                        {"type": "command", "command": "python3 /unrelated/hook.py"},
                    ],
                },
            ]
        }
    }
    settings.write_text(json.dumps(legacy_data, indent=2))

    # Evict all autoskillit entries
    _evict_stale_autoskillit_hooks(settings)
    data = json.loads(settings.read_text())
    remaining = data["hooks"]["PreToolUse"]
    # Only the unrelated hook should remain
    assert len(remaining) == 1
    assert remaining[0]["matcher"] == "some_other_matcher"

    # Now register fresh entries — no duplicates
    _register_quota_hook(settings)
    data = json.loads(settings.read_text())
    all_commands = [
        h["command"] for entry in data["hooks"]["PreToolUse"] for h in entry.get("hooks", [])
    ]
    quota_commands = [c for c in all_commands if "quota_check" in c]
    assert len(quota_commands) == 1
