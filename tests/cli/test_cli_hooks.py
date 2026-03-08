"""Tests for the cli/_hooks.py unified hook registration helpers."""

from __future__ import annotations

import json
from pathlib import Path


# HK1
def test_hooks_module_exists():
    """cli/_hooks.py must exist as a module."""


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
    for entry in data["hooks"]["PreToolUse"]:
        for hook in entry.get("hooks", []):
            cmd = hook["command"]
            assert "python3 -m" not in cmd, f"Registered hook uses python3 -m: {cmd}"
            assert "${" not in cmd, f"Registered hook uses env var: {cmd}"


# HK12
def test_hooks_py_covers_full_registry(tmp_path):
    """sync_hooks_to_settings() registers all scripts from HOOK_REGISTRY."""
    from autoskillit.cli._hooks import _evict_stale_autoskillit_hooks, sync_hooks_to_settings
    from autoskillit.hooks import HOOK_REGISTRY

    all_registry_scripts = {s for h in HOOK_REGISTRY for s in h.scripts}
    settings = tmp_path / "settings.json"
    _evict_stale_autoskillit_hooks(settings)
    sync_hooks_to_settings(settings)
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
    sync_hooks_to_settings(settings)
    data = json.loads(settings.read_text())
    all_commands = [
        h["command"] for entry in data["hooks"]["PreToolUse"] for h in entry.get("hooks", [])
    ]
    quota_commands = [c for c in all_commands if "quota_check" in c]
    assert len(quota_commands) == 1


# T-REG-1
def test_install_production_order_includes_quota_check(tmp_path, monkeypatch):
    """install() must register quota_check.py regardless of function call order."""
    import importlib

    import autoskillit.cli as cli

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

    cli.install(scope="local")

    data = json.loads(settings_path.read_text())
    pretooluse = data.get("hooks", {}).get("PreToolUse", [])
    all_commands = [h["command"] for e in pretooluse for h in e.get("hooks", [])]
    assert any("quota_check.py" in c for c in all_commands), (
        "quota_check.py missing from settings.json after install() — silent drop bug present"
    )


# T-REG-2
def test_settings_json_matches_hook_registry_after_install(tmp_path, monkeypatch):
    """After install(), settings.json must contain every script from HOOK_REGISTRY."""
    import importlib

    import autoskillit.cli as cli
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

    cli.install(scope="local")

    data = json.loads(settings_path.read_text())
    pretooluse = data.get("hooks", {}).get("PreToolUse", [])
    for hook_def in HOOK_REGISTRY:
        matching = [e for e in pretooluse if e.get("matcher") == hook_def.matcher]
        assert len(matching) == 1, (
            f"Expected exactly 1 entry for matcher {hook_def.matcher!r}, got {len(matching)}"
        )
        entry_commands = [h["command"] for h in matching[0].get("hooks", [])]
        for script in hook_def.scripts:
            assert any(script in c for c in entry_commands), (
                f"Script {script!r} missing from matcher {hook_def.matcher!r} in settings.json"
            )


# T-REG-3
def test_sync_hooks_to_settings_writes_all_registry_scripts(tmp_path):
    """sync_hooks_to_settings() writes all HOOK_REGISTRY scripts to settings.json."""
    from autoskillit.cli._hooks import sync_hooks_to_settings
    from autoskillit.hooks import HOOK_REGISTRY

    settings = tmp_path / "settings.json"
    sync_hooks_to_settings(settings)

    data = json.loads(settings.read_text())
    pretooluse = data["hooks"]["PreToolUse"]
    assert len(pretooluse) == len(HOOK_REGISTRY), (
        f"Expected {len(HOOK_REGISTRY)} PreToolUse entries, got {len(pretooluse)}"
    )
    all_commands = [h["command"] for e in pretooluse for h in e.get("hooks", [])]
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
    pretooluse = data["hooks"]["PreToolUse"]
    assert len(pretooluse) == len(HOOK_REGISTRY), (
        f"Duplicate entries after evict+sync twice: {len(pretooluse)} entries"
    )
