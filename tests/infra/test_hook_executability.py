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
                script_name = cmd.split("/")[-1]
                generated_pairs.add((matcher, script_name))

    registry_pairs: set[tuple[str, str]] = set()
    for hook_def in HOOK_REGISTRY:
        for script in hook_def.scripts:
            registry_pairs.add((hook_def.matcher, script))

    assert registry_pairs == generated_pairs


def test_hooks_json_on_disk_exists_and_matches() -> None:
    """hooks.json on disk must exist and match generate_hooks_json() exactly.

    This test fails when the generation step has not been run. It guards
    against drift between HOOK_REGISTRY and the on-disk plugin manifest.
    """
    hooks_json_path = pkg_root() / "hooks" / "hooks.json"
    assert hooks_json_path.exists(), (
        "src/autoskillit/hooks/hooks.json is missing. "
        'Run: uv run python -c "'
        "from autoskillit.hooks import generate_hooks_json; "
        "from autoskillit.core.io import atomic_write; "
        "from autoskillit.core.paths import pkg_root; "
        "import json; "
        "atomic_write(pkg_root() / 'hooks' / 'hooks.json', "
        "json.dumps(generate_hooks_json(), indent=2) + chr(10))"
        '"'
    )
    on_disk = json.loads(hooks_json_path.read_text())
    expected = generate_hooks_json()
    assert on_disk == expected, (
        "hooks.json on disk does not match generate_hooks_json(). "
        "Re-run the generation command to regenerate."
    )


def test_hook_registry_scripts_exist_on_disk() -> None:
    """Every script referenced in HOOK_REGISTRY must exist as a file in hooks/."""
    hooks_dir = pkg_root() / "hooks"
    for hook_def in HOOK_REGISTRY:
        for script in hook_def.scripts:
            script_path = hooks_dir / script
            assert script_path.is_file(), f"Registry script not found on disk: {script_path}"


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
def test_generate_hooks_json_and_sync_produce_equivalent_entries(tmp_path) -> None:
    """Both generation paths must produce structurally identical hook entries
    for every event type. Verifies that _build_hook_entry() is shared,
    preventing path A/B divergence.
    """
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
