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


# T6b
def test_register_native_tool_guard_hook(tmp_path):
    """Registration creates the correct matcher and command entry in settings.json."""
    from autoskillit.cli._hooks import _register_native_tool_guard_hook

    settings = tmp_path / "settings.json"
    _register_native_tool_guard_hook(settings)

    data = json.loads(settings.read_text())
    entries = data["hooks"]["PreToolUse"]

    guard_entries = [
        e
        for e in entries
        if any("native_tool_guard" in h.get("command", "") for h in e.get("hooks", []))
    ]
    assert guard_entries, "native_tool_guard hook entry not found in settings.json"

    entry = guard_entries[0]
    matcher = entry.get("matcher", "")
    # Matcher should cover the main native tools
    import re

    for tool in ("Read", "Write", "Edit", "Bash", "Glob", "Grep", "Agent"):
        assert re.match(matcher, tool), f"Matcher {matcher!r} should match {tool!r}"
