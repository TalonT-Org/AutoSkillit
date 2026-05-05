"""Tests for hook command executability — validates the invocation path, not just logic.

Every hook command in hooks.json must execute successfully as a subprocess,
which is how Claude Code actually runs them. This catches the ModuleNotFoundError
bug where `python3 -m autoskillit.hooks.*` fails under system Python.
"""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from autoskillit.core import pkg_root
from autoskillit.hooks import HOOK_REGISTRY, generate_hooks_json


def _extract_hook_commands() -> list[str]:
    """Extract all command strings from generate_hooks_json() output."""
    data = generate_hooks_json()
    hooks = data.get("hooks", {})
    commands: list[str] = []
    for event_type in ("PreToolUse", "PostToolUse", "SessionStart"):
        for entry in hooks.get(event_type, []):
            for hook in entry.get("hooks", []):
                cmd = hook.get("command", "")
                if cmd:
                    commands.append(cmd)
    return commands


@pytest.mark.parametrize("command", _extract_hook_commands(), ids=_extract_hook_commands())
def test_hook_command_executable(command: str) -> None:
    """Every hook command must execute successfully as a subprocess."""
    parts = shlex.split(command)
    # Replace python3 with sys.executable for test isolation
    if parts[0] == "python3":
        parts[0] = sys.executable
    # Run with a minimal valid event on stdin (tool_name only)
    event = json.dumps({"tool_name": "Read", "tool_input": {}})
    proc = subprocess.run(parts, input=event, capture_output=True, text=True, timeout=10)
    assert proc.returncode == 0, (
        f"Hook command failed with exit code {proc.returncode}.\n"
        f"Command: {command}\n"
        f"Resolved: {' '.join(parts)}\n"
        f"stderr: {proc.stderr}"
    )


def test_hook_registry_matches_generated_hooks_json() -> None:
    """Every hook in HOOK_REGISTRY must appear in generate_hooks_json() output and vice versa."""
    data = generate_hooks_json()
    generated_pairs: set[tuple[str, str]] = set()
    for event_entries in data.get("hooks", {}).values():
        for entry in event_entries:
            matcher = entry.get("matcher", "")
            for hook in entry["hooks"]:
                cmd = hook["command"]
                parts = cmd.split()
                if "_dispatch.py" in cmd and len(parts) >= 3:
                    script_name = parts[-1] + ".py"
                else:
                    path_parts = cmd.split("/hooks/", 1)
                    script_name = path_parts[1] if len(path_parts) == 2 else cmd.split("/")[-1]
                generated_pairs.add((matcher, script_name))

    registry_pairs: set[tuple[str, str]] = set()
    for hook_def in HOOK_REGISTRY:
        for script in hook_def.scripts:
            registry_pairs.add((hook_def.matcher, script))

    assert registry_pairs == generated_pairs


def test_committed_registry_hash_matches_live_registry() -> None:
    """registry.sha256 (committed) must match the live HOOK_REGISTRY_HASH.

    Fails when HOOK_REGISTRY is edited without running `task sync-hooks-hash`.
    Unlike the old byte-equality test, this cannot be silenced by CI pre-regen
    because the anchor is committed.
    """
    from autoskillit.hook_registry import HOOK_REGISTRY_HASH, HOOKS_DIR

    sha_file = HOOKS_DIR / "registry.sha256"
    assert sha_file.exists(), (
        "src/autoskillit/hooks/registry.sha256 is missing. "
        "Run `task sync-hooks-hash` and commit the result."
    )
    committed = sha_file.read_text().strip()
    assert committed == HOOK_REGISTRY_HASH, (
        "registry.sha256 is stale. Run `task sync-hooks-hash` and commit the result."
    )


def test_hook_registry_scripts_exist_on_disk() -> None:
    """Every script referenced in HOOK_REGISTRY must exist as a file in hooks/."""
    hooks_dir = pkg_root() / "hooks"
    for hook_def in HOOK_REGISTRY:
        for script in hook_def.scripts:
            script_path = hooks_dir / script
            assert script_path.is_file(), f"Registry script not found on disk: {script_path}"
    dispatch_path = hooks_dir / "_dispatch.py"
    assert dispatch_path.is_file(), f"Stable dispatcher not found on disk: {dispatch_path}"


# REQ-HOOK-001
def test_hook_registry_has_session_start_entry() -> None:
    session_start_entries = [h for h in HOOK_REGISTRY if h.event_type == "SessionStart"]
    assert session_start_entries, "HOOK_REGISTRY must contain a SessionStart entry"


def test_generate_hooks_json_session_start_no_matcher() -> None:
    result = generate_hooks_json()
    session_start_entries = result["hooks"].get("SessionStart", [])
    assert session_start_entries, "hooks.json must include SessionStart"
    for entry in session_start_entries:
        assert "matcher" not in entry, "SessionStart entries must not have a matcher key"


# T-CROSS-2
def test_generate_hooks_json_and_sync_produce_equivalent_entries(tmp_path, monkeypatch) -> None:
    """Both generation paths must produce structurally identical hook entries
    for every event type. Verifies that _build_hook_entry() is shared,
    preventing path A/B divergence.
    """
    import autoskillit.cli._hooks as _hooks_mod

    monkeypatch.setattr(_hooks_mod, "is_git_worktree", lambda path: False)

    from autoskillit.cli._hooks import _evict_stale_autoskillit_hooks, sync_hooks_to_settings

    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir()
    settings.write_text('{"hooks": {}}')
    _evict_stale_autoskillit_hooks(settings)
    sync_hooks_to_settings(settings)
    deployed = json.loads(settings.read_text())

    canonical = generate_hooks_json()

    for event_type in ("PreToolUse", "PostToolUse", "SessionStart"):
        canonical_entries = canonical.get("hooks", {}).get(event_type, [])
        deployed_entries = deployed.get("hooks", {}).get(event_type, [])
        assert len(canonical_entries) == len(deployed_entries), (
            f"{event_type}: canonical has {len(canonical_entries)} entries, "
            f"deployed has {len(deployed_entries)}"
        )
        for i, (c_entry, d_entry) in enumerate(zip(canonical_entries, deployed_entries)):
            assert set(c_entry.keys()) == set(d_entry.keys()), (
                f"{event_type}[{i}]: key mismatch. "
                f"canonical keys={set(c_entry.keys())}, "
                f"deployed keys={set(d_entry.keys())}"
            )
            if "matcher" in c_entry:
                assert c_entry["matcher"] == d_entry["matcher"], (
                    f"{event_type}[{i}]: matcher mismatch"
                )


def test_generated_hooks_json_includes_ask_user_question_gate() -> None:
    from autoskillit.hook_registry import generate_hooks_json

    h = generate_hooks_json()
    pretool = h["hooks"].get("PreToolUse", [])
    matchers = [entry["matcher"] for entry in pretool]
    assert "AskUserQuestion" in matchers


def test_generated_hooks_json_embeds_registry_hash() -> None:
    from autoskillit.hook_registry import HOOK_REGISTRY_HASH, generate_hooks_json

    h = generate_hooks_json()
    assert h.get("_autoskillit_registry_hash") == HOOK_REGISTRY_HASH


def test_synced_settings_json_embeds_registry_hash(tmp_path: Path, monkeypatch) -> None:
    import autoskillit.cli._hooks as _hooks_mod

    monkeypatch.setattr(_hooks_mod, "is_git_worktree", lambda path: False)

    from autoskillit.cli._hooks import sync_hooks_to_settings
    from autoskillit.hook_registry import HOOK_REGISTRY_HASH

    settings_path = tmp_path / "settings.json"
    sync_hooks_to_settings(settings_path)
    data = json.loads(settings_path.read_text())
    assert data.get("_autoskillit_registry_hash") == HOOK_REGISTRY_HASH
