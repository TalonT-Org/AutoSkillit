"""Tests for the cli/_hooks.py unified hook registration helpers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoskillit import __version__
from autoskillit.cli._install_contract import InstallMode, InstallRequest

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]


def _direct_request(scope: str = "user") -> InstallRequest:
    return InstallRequest(
        scope=scope,
        mode=InstallMode.DIRECT,
        require_registered_plugin=True,
        expected_version=__version__,
    )


def _extract_script_from_cmd(cmd: str) -> str:
    """Extract the hooks-dir-relative script path from a hook command string."""
    parts = cmd.split()
    if "_dispatch.py" in cmd and len(parts) >= 3:
        return parts[-1] + ".py"
    if "/hooks/" in cmd:
        return cmd.split("/hooks/", 1)[1]
    return cmd.split("/")[-1]


# HK9
def test_claude_settings_path_user_scope():
    """_claude_settings_path('user') returns ~/.claude/settings.json."""
    from autoskillit.cli._hooks import _claude_settings_path

    p = _claude_settings_path("user", cwd=Path.cwd())
    assert p == Path.home() / ".claude" / "settings.json"


# HK10
def test_claude_settings_path_project_scope(tmp_path, monkeypatch):
    """_claude_settings_path('project') returns <cwd>/.claude/settings.json."""
    monkeypatch.chdir(tmp_path)
    from autoskillit.cli._hooks import _claude_settings_path

    p = _claude_settings_path("project", cwd=tmp_path)
    assert p == tmp_path / ".claude" / "settings.json"


def test_claude_settings_path_rejects_invalid_scope(tmp_path: Path) -> None:
    from autoskillit.cli._hooks import _claude_settings_path

    with pytest.raises(ValueError, match="invalid Claude settings scope"):
        _claude_settings_path("invalid", cwd=tmp_path)


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
    registered_pretooluse_scripts = {
        _extract_script_from_cmd(cmd) for cmd in registered_pretooluse
    }
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
    registered_posttooluse_scripts = {
        _extract_script_from_cmd(cmd) for cmd in registered_posttooluse
    }
    assert posttooluse_scripts == registered_posttooluse_scripts, (
        f"PostToolUse missing: {posttooluse_scripts - registered_posttooluse_scripts}, "
        f"Extra: {registered_posttooluse_scripts - posttooluse_scripts}"
    )


def test_sync_validates_lifecycle_before_plugin_installed_early_return(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import autoskillit.cli._hooks as hooks_module

    class ExpectedValidationFailure(RuntimeError):
        pass

    def fail_validation(*args: object, **kwargs: object) -> None:
        raise ExpectedValidationFailure

    def plugin_check_must_not_run(**kwargs: object) -> bool:
        pytest.fail("plugin-installed early return ran before lifecycle validation")

    monkeypatch.setattr(hooks_module, "validate_lifecycle_contracts", fail_validation)
    monkeypatch.setattr(
        "autoskillit.cli._init_helpers._is_plugin_installed",
        plugin_check_must_not_run,
    )

    with pytest.raises(ExpectedValidationFailure):
        hooks_module.sync_hooks_to_settings(tmp_path / "settings.json")


# HK13
def test_evict_stale_hooks_removes_legacy_formats(tmp_path):
    """install() must remove all legacy autoskillit hook formats before writing fresh ones."""
    from autoskillit.cli._hooks import (
        _evict_stale_autoskillit_hooks,
        _find_autoskillit_hook_commands,
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
                        {
                            "type": "command",
                            "command": "python3 -m autoskillit.hooks.guards.quota_guard",
                        },
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

    assert len(_find_autoskillit_hook_commands(legacy_data)) == 3

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
    """install() must register quota_guard.py in hooks.json (plugin authority path)."""
    from autoskillit.hooks import generate_hooks_json

    hooks_data = generate_hooks_json()
    pretooluse = hooks_data.get("hooks", {}).get("PreToolUse", [])
    all_commands = [h["command"] for e in pretooluse for h in e.get("hooks", [])]
    assert any("quota_guard" in c for c in all_commands), (
        "quota_guard missing from hooks.json — silent drop bug present"
    )


# T-REG-2
def test_hooks_json_matches_hook_registry_after_generate():
    """generate_hooks_json() must contain every script from HOOK_REGISTRY."""
    from autoskillit.hooks import HOOK_REGISTRY, generate_hooks_json

    data = generate_hooks_json()
    for hook_def in HOOK_REGISTRY:
        event_entries = data.get("hooks", {}).get(hook_def.event_type, [])
        # Per REQ-B39: matcherless events (SessionStart, Stop, matcherless
        # PreToolUse) omit the matcher key entirely; matcher-bearing events
        # carry an explicit matcher string.
        if hook_def.event_type in {"SessionStart", "Stop"} or (
            hook_def.event_type == "PreToolUse" and not hook_def.matcher
        ):
            matching = [e for e in event_entries if "matcher" not in e]
        else:
            matching = [e for e in event_entries if e.get("matcher") == hook_def.matcher]
        assert len(matching) == 1, (
            f"Expected exactly 1 {hook_def.event_type} entry for matcher "
            f"{hook_def.matcher!r}, got {len(matching)}"
        )
        # Each registered script under this matcher must appear in the rendered
        # command list — otherwise the registry silently drops scripts.
        entry_commands = [h["command"] for h in matching[0].get("hooks", [])]
        for script in hook_def.scripts:
            logical_name = script.removesuffix(".py")
            assert any(logical_name in c for c in entry_commands), (
                f"Script {script!r} missing from matcher {hook_def.matcher!r} "
                f"in {hook_def.event_type} section of hooks.json"
            )


def test_render_shape_for_matcherless_events() -> None:
    """REQ-B39: matcherless Stop / matcherless PreToolUse entries omit the matcher key.

    Claude Code's documented matcherless event schema has no matcher field;
    emitting ``{"matcher": ""}`` would be a render-shape deviation. This
    test asserts that the rendered ``hooks.json`` (a) omits ``matcher`` for
    every matcherless entry and (b) still includes a ``hooks`` array for
    those entries.
    """
    from autoskillit.hook_registry import (
        HOOK_REGISTRY,
        LIFECYCLE_CONTRACTS,
        generate_hooks_json,
    )

    payload = generate_hooks_json(HOOK_REGISTRY, LIFECYCLE_CONTRACTS)
    hooks = payload["hooks"]
    # SessionStart and Stop are always matcherless.
    for event_type in ("SessionStart", "Stop"):
        assert event_type in hooks, f"missing event type: {event_type}"
        for entry in hooks[event_type]:
            assert "matcher" not in entry, (
                f"always-matcherless event {event_type!r} must omit 'matcher'; got: {entry}"
            )
            assert "hooks" in entry and entry["hooks"], (
                f"always-matcherless event {event_type!r} must carry a 'hooks' array"
            )
    # PreToolUse with empty matcher (matcherless) — REQ-JOIN-005 hook entries.
    for entry in hooks.get("PreToolUse", []):
        if "matcher" not in entry:
            assert "hooks" in entry and entry["hooks"], (
                "matcherless PreToolUse entry must carry a 'hooks' array"
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
            logical_name = script.removesuffix(".py")
            assert any(logical_name in c for c in all_commands), (
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


def test_sync_hooks_uses_dispatcher_format(tmp_path, monkeypatch):
    """sync_hooks_to_settings() must produce dispatcher-format commands."""
    import autoskillit.cli._hooks as _hooks_mod

    monkeypatch.setattr(_hooks_mod, "is_git_worktree", lambda path: False)

    from autoskillit.cli._hooks import sync_hooks_to_settings

    settings = tmp_path / "settings.json"
    sync_hooks_to_settings(settings)
    data = json.loads(settings.read_text())
    for event_type, entries in data.get("hooks", {}).items():
        for entry in entries:
            for hook in entry.get("hooks", []):
                cmd = hook["command"]
                assert "_dispatch.py" in cmd, (
                    f"{event_type} command does not use dispatcher: {cmd}"
                )
                parts = cmd.split()
                assert parts[-2].endswith("_dispatch.py"), (
                    f"{event_type} dispatcher not in expected position: {cmd}"
                )


# T-DUAL-1
def test_install_does_not_write_hooks_when_plugin_active(tmp_path, monkeypatch):
    """install() must not write autoskillit hooks to settings.json when plugin is active.

    When the plugin is installed, hooks are provided via hooks.json (plugin cache).
    Writing them to settings.json creates dual registration and doubles hook execution.
    """
    import importlib
    from types import SimpleNamespace

    from autoskillit.cli._marketplace import install

    settings_path = tmp_path / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)

    app_module = importlib.import_module("autoskillit.cli._hooks")
    monkeypatch.setattr(
        app_module,
        "_claude_settings_path",
        lambda scope, **_kwargs: settings_path,
    )
    monkeypatch.setattr(
        "autoskillit.cli._init_helpers._is_plugin_installed",
        lambda **kwargs: True,
    )
    monkeypatch.setattr("subprocess.run", lambda *a, **kw: type("R", (), {"returncode": 0})())
    monkeypatch.setattr("shutil.which", lambda cmd, *, path=None: f"/usr/bin/{cmd}")
    monkeypatch.setattr(
        "autoskillit.cli._plugin_artifact.publish_installed_plugin_artifact",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "autoskillit.workspace.verify_installed_plugin_artifact",
        lambda _spec: SimpleNamespace(
            identity=SimpleNamespace(
                incarnation_id="test-incarnation",
                semantic_key=f"autoskillit@autoskillit-local:{__version__}",
            ),
            findings=(),
        ),
    )

    _app_mod = importlib.import_module("autoskillit.cli._marketplace")
    monkeypatch.setattr(_app_mod, "is_git_worktree", lambda path: False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    install(request=_direct_request("local"))

    data = json.loads(settings_path.read_text()) if settings_path.exists() else {}
    all_commands = [
        h["command"]
        for event_entries in data.get("hooks", {}).values()
        if isinstance(event_entries, list)
        for entry in event_entries
        for h in entry.get("hooks", [])
    ]
    autoskillit_hooks = [c for c in all_commands if "_dispatch.py" in c]
    assert autoskillit_hooks == [], (
        f"install() wrote {len(autoskillit_hooks)} autoskillit hooks to settings.json "
        f"even though plugin is active. Expected zero autoskillit hooks in settings.json "
        f"when the plugin provides hooks via hooks.json."
    )


# T-DUAL-2
def test_register_all_skips_hook_sync_when_plugin_active(tmp_path, monkeypatch):
    """_register_all() must not write autoskillit hooks to settings.json when plugin is active."""
    import importlib

    settings_path = tmp_path / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)

    _hooks_mod = importlib.import_module("autoskillit.cli._hooks")
    monkeypatch.setattr(
        _hooks_mod,
        "_claude_settings_path",
        lambda scope, **_kwargs: settings_path,
    )
    monkeypatch.setattr(
        "autoskillit.cli._init_helpers._is_plugin_installed",
        lambda **kwargs: True,
    )
    from autoskillit.cli._init_helpers import _register_all

    _register_all(scope="user", project_dir=tmp_path)

    data = json.loads(settings_path.read_text())
    all_commands = [
        h["command"]
        for event_entries in data.get("hooks", {}).values()
        if isinstance(event_entries, list)
        for entry in event_entries
        for h in entry.get("hooks", [])
    ]
    autoskillit_hooks = [c for c in all_commands if "_dispatch.py" in c]
    assert autoskillit_hooks == [], (
        f"_register_all() wrote {len(autoskillit_hooks)} autoskillit hooks to settings.json "
        f"even though plugin is active. Expected zero autoskillit hooks in settings.json "
        f"when the plugin provides hooks via hooks.json."
    )


# T-DUAL-3
def test_register_all_writes_hooks_when_plugin_not_active(tmp_path, monkeypatch):
    """_register_all() must write autoskillit hooks to settings.json when plugin is NOT active."""
    import importlib

    settings_path = tmp_path / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)

    _hooks_mod = importlib.import_module("autoskillit.cli._hooks")
    monkeypatch.setattr(
        _hooks_mod,
        "_claude_settings_path",
        lambda scope, **_kwargs: settings_path,
    )
    monkeypatch.setattr(
        "autoskillit.cli._init_helpers._is_plugin_installed",
        lambda **kwargs: False,
    )
    monkeypatch.setattr("subprocess.run", lambda *a, **kw: type("R", (), {"returncode": 0})())

    from autoskillit.cli._init_helpers import _register_all
    from autoskillit.hooks import HOOK_REGISTRY

    _register_all(scope="user", project_dir=tmp_path)

    data = json.loads(settings_path.read_text())
    all_commands = [
        h["command"]
        for event_entries in data.get("hooks", {}).values()
        if isinstance(event_entries, list)
        for entry in event_entries
        for h in entry.get("hooks", [])
    ]
    # Should contain all scripts from HOOK_REGISTRY
    expected_scripts = {s for h in HOOK_REGISTRY for s in h.scripts}
    registered_scripts = {cmd.split()[-1] for cmd in all_commands if "_dispatch.py" in cmd}
    expected_logical = {s.removesuffix(".py") for s in expected_scripts}
    assert expected_logical <= registered_scripts, (
        f"Missing autoskillit hooks after _register_all(): {expected_logical - registered_scripts}"
    )
