"""Tests for the cli/_hooks.py unified hook registration helpers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]


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
    from autoskillit.cli._hooks import sync_hooks_to_settings

    settings = tmp_path / "settings.json"
    sync_hooks_to_settings(settings)
    data = json.loads(settings.read_text())
    for event_type, entries in data["hooks"].items():
        for entry in entries:
            for hook in entry.get("hooks", []):
                cmd = hook["command"]
                assert "python3 -m" not in cmd, (
                    f"Registered hook in {event_type} uses python3 -m: {cmd}"
                )
                assert "${" not in cmd, f"Registered hook in {event_type} uses env var: {cmd}"


# HK12
def test_hooks_py_covers_full_registry(tmp_path):
    """sync_hooks_to_settings() registers all scripts from HOOK_REGISTRY."""
    from autoskillit.cli._hooks import _evict_stale_autoskillit_hooks, sync_hooks_to_settings
    from autoskillit.hooks import HOOK_REGISTRY

    settings = tmp_path / "settings.json"
    _evict_stale_autoskillit_hooks(settings)
    sync_hooks_to_settings(settings)
    data = json.loads(settings.read_text())

    # Verify PreToolUse entries cover all PreToolUse registry entries
    pretooluse_scripts = {
        s for h in HOOK_REGISTRY if h.event_type == "PreToolUse" for s in h.scripts
    }
    registered_pretooluse = [
        h["command"]
        for entry in data["hooks"].get("PreToolUse", [])
        for h in entry.get("hooks", [])
    ]
    registered_pretooluse_scripts = {cmd.split("/")[-1] for cmd in registered_pretooluse}
    assert pretooluse_scripts == registered_pretooluse_scripts, (
        f"PreToolUse missing: {pretooluse_scripts - registered_pretooluse_scripts}, "
        f"Extra: {registered_pretooluse_scripts - pretooluse_scripts}"
    )

    # Verify PostToolUse entries cover all PostToolUse registry entries
    posttooluse_scripts = {
        s for h in HOOK_REGISTRY if h.event_type == "PostToolUse" for s in h.scripts
    }
    registered_posttooluse = [
        h["command"]
        for entry in data["hooks"].get("PostToolUse", [])
        for h in entry.get("hooks", [])
    ]
    registered_posttooluse_scripts = {cmd.split("/")[-1] for cmd in registered_posttooluse}
    assert posttooluse_scripts == registered_posttooluse_scripts, (
        f"PostToolUse missing: {posttooluse_scripts - registered_posttooluse_scripts}, "
        f"Extra: {registered_posttooluse_scripts - posttooluse_scripts}"
    )


# HK13
def test_evict_stale_hooks_removes_legacy_formats(tmp_path):
    """install() must remove all legacy autoskillit hook formats before writing fresh ones."""
    from autoskillit.cli._hooks import (
        _evict_stale_autoskillit_hooks,
        sync_hooks_to_settings,
    )

    settings = tmp_path / "settings.json"
    # Seed with three legacy format entries
    legacy_data = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "mcp__.*autoskillit.*__run_skill.*",
                    "hooks": [
                        {"type": "command", "command": "python3 -m autoskillit.hooks.guards.quota_guard"},
                    ],
                },
                {
                    "matcher": "mcp__.*autoskillit.*__run_skill.*",
                    "hooks": [
                        {
                            "type": "command",
                            "command": "python3 /old/path/hooks/quota_guard.py",
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
    sync_hooks_to_settings(settings)
    data = json.loads(settings.read_text())
    all_commands = [
        h["command"] for entry in data["hooks"]["PreToolUse"] for h in entry.get("hooks", [])
    ]
    quota_commands = [c for c in all_commands if "quota_guard" in c]
    assert len(quota_commands) == 1


# T-REG-1
def test_install_production_order_includes_quota_check(tmp_path, monkeypatch):
    """install() must register quota_guard.py regardless of function call order."""
    import importlib

    from autoskillit.cli._marketplace import install

    settings_path = tmp_path / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)

    app_module = importlib.import_module("autoskillit.cli._hooks")
    monkeypatch.setattr(app_module, "_claude_settings_path", lambda scope: settings_path)
    monkeypatch.setattr("subprocess.run", lambda *a, **kw: type("R", (), {"returncode": 0})())
    monkeypatch.setattr("shutil.which", lambda cmd: f"/usr/bin/{cmd}")
    monkeypatch.delenv("CLAUDECODE", raising=False)

    _app_mod = importlib.import_module("autoskillit.cli._marketplace")
    monkeypatch.setattr(_app_mod, "is_git_worktree", lambda path: False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    install(scope="local")

    data = json.loads(settings_path.read_text())
    pretooluse = data.get("hooks", {}).get("PreToolUse", [])
    all_commands = [h["command"] for e in pretooluse for h in e.get("hooks", [])]
    assert any("quota_guard.py" in c for c in all_commands), (
        "quota_guard.py missing from settings.json after install() — silent drop bug present"
    )


# T-REG-2
def test_settings_json_matches_hook_registry_after_install(tmp_path, monkeypatch):
    """After install(), settings.json must contain every script from HOOK_REGISTRY."""
    import importlib

    from autoskillit.cli._marketplace import install
    from autoskillit.hooks import HOOK_REGISTRY

    settings_path = tmp_path / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)

    app_module = importlib.import_module("autoskillit.cli._hooks")
    monkeypatch.setattr(app_module, "_claude_settings_path", lambda scope: settings_path)
    monkeypatch.setattr("subprocess.run", lambda *a, **kw: type("R", (), {"returncode": 0})())
    monkeypatch.setattr("shutil.which", lambda cmd: f"/usr/bin/{cmd}")
    monkeypatch.delenv("CLAUDECODE", raising=False)

    _app_mod = importlib.import_module("autoskillit.cli._marketplace")
    monkeypatch.setattr(_app_mod, "is_git_worktree", lambda path: False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    install(scope="local")

    data = json.loads(settings_path.read_text())
    for hook_def in HOOK_REGISTRY:
        event_entries = data.get("hooks", {}).get(hook_def.event_type, [])
        if hook_def.event_type == "SessionStart":
            matching = [e for e in event_entries if "matcher" not in e]
        else:
            matching = [e for e in event_entries if e.get("matcher") == hook_def.matcher]
        assert len(matching) == 1, (
            f"Expected exactly 1 {hook_def.event_type} entry for matcher "
            f"{hook_def.matcher!r}, got {len(matching)}"
        )
        entry_commands = [h["command"] for h in matching[0].get("hooks", [])]
        for script in hook_def.scripts:
            assert any(script in c for c in entry_commands), (
                f"Script {script!r} missing from matcher {hook_def.matcher!r} "
                f"in {hook_def.event_type} section of settings.json"
            )


# T-REG-3
def test_sync_hooks_to_settings_writes_all_registry_scripts(tmp_path):
    """sync_hooks_to_settings() writes all HOOK_REGISTRY scripts to settings.json."""
    from autoskillit.cli._hooks import sync_hooks_to_settings
    from autoskillit.hooks import HOOK_REGISTRY

    settings = tmp_path / "settings.json"
    sync_hooks_to_settings(settings)

    data = json.loads(settings.read_text())

    # Verify PreToolUse entry count matches unique (event_type, matcher) pairs.
    # HookDef entries sharing a matcher are consolidated into one settings.json entry.
    pretooluse_matchers = {h.matcher for h in HOOK_REGISTRY if h.event_type == "PreToolUse"}
    pretooluse = data["hooks"].get("PreToolUse", [])
    assert len(pretooluse) == len(pretooluse_matchers), (
        f"Expected {len(pretooluse_matchers)} PreToolUse entries, got {len(pretooluse)}"
    )

    # Verify PostToolUse entries exist
    posttooluse_matchers = {h.matcher for h in HOOK_REGISTRY if h.event_type == "PostToolUse"}
    posttooluse = data["hooks"].get("PostToolUse", [])
    assert len(posttooluse) == len(posttooluse_matchers), (
        f"Expected {len(posttooluse_matchers)} PostToolUse entries, got {len(posttooluse)}"
    )

    # All scripts from all event types must be present
    all_commands = [
        h["command"]
        for event_entries in data["hooks"].values()
        for entry in event_entries
        for h in entry.get("hooks", [])
    ]
    for hook_def in HOOK_REGISTRY:
        for script in hook_def.scripts:
            assert any(script in c for c in all_commands), (
                f"Script {script!r} missing from settings.json after sync_hooks_to_settings()"
            )


# T-REG-4
def test_sync_hooks_to_settings_is_idempotent(tmp_path):
    """Calling evict + sync twice produces no duplicate entries."""
    from autoskillit.cli._hooks import _evict_stale_autoskillit_hooks, sync_hooks_to_settings
    from autoskillit.hooks import HOOK_REGISTRY

    settings = tmp_path / "settings.json"
    _evict_stale_autoskillit_hooks(settings)
    sync_hooks_to_settings(settings)
    _evict_stale_autoskillit_hooks(settings)
    sync_hooks_to_settings(settings)

    data = json.loads(settings.read_text())

    # HookDef entries sharing a matcher are consolidated into one settings.json entry.
    pretooluse_count = len({h.matcher for h in HOOK_REGISTRY if h.event_type == "PreToolUse"})
    posttooluse_count = len({h.matcher for h in HOOK_REGISTRY if h.event_type == "PostToolUse"})

    pretooluse = data["hooks"].get("PreToolUse", [])
    posttooluse = data["hooks"].get("PostToolUse", [])

    assert len(pretooluse) == pretooluse_count, (
        f"Duplicate entries after evict+sync twice: {len(pretooluse)} PreToolUse entries"
    )
    assert len(posttooluse) == posttooluse_count, (
        f"Duplicate entries after evict+sync twice: {len(posttooluse)} PostToolUse entries"
    )


# T-WT-1: sync_hooks_to_settings rejects worktree pkg_root
def test_sync_hooks_rejects_worktree_pkg_root(tmp_path, monkeypatch):
    """sync_hooks_to_settings must raise when pkg_root() is inside a git worktree."""
    from autoskillit.cli._hooks import sync_hooks_to_settings

    fake_pkg = tmp_path / "worktree" / "src" / "autoskillit"
    fake_pkg.mkdir(parents=True)

    monkeypatch.setattr("autoskillit.cli._hooks.pkg_root", lambda: fake_pkg)
    monkeypatch.setattr("autoskillit.cli._hooks.is_git_worktree", lambda path: True)

    settings_path = tmp_path / "settings.json"
    settings_path.write_text("{}")

    with pytest.raises(RuntimeError, match="worktree"):
        sync_hooks_to_settings(settings_path)


# T-CROSS-1
def test_sync_hooks_to_settings_session_start_no_matcher(tmp_path):
    """sync_hooks_to_settings() must not emit 'matcher' key for SessionStart entries."""
    from autoskillit.cli._hooks import _evict_stale_autoskillit_hooks, sync_hooks_to_settings

    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir()
    settings.write_text('{"hooks": {}}')
    _evict_stale_autoskillit_hooks(settings)
    sync_hooks_to_settings(settings)
    data = json.loads(settings.read_text())
    session_start_entries = data.get("hooks", {}).get("SessionStart", [])
    assert session_start_entries, "Expected at least one SessionStart entry"
    for entry in session_start_entries:
        assert "matcher" not in entry, (
            f"SessionStart entry must not have 'matcher' key, got: {entry}"
        )
