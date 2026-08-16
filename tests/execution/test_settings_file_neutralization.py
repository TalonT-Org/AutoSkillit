"""Tests for repository settings file neutralization.

Per Plan § Step 5.4 (REQ-EXTRACT-053), the launcher must read and
neutralize (or refuse) a conflicting env.CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS
entry in the target repository's .claude/settings.json or
.claude/settings.local.json.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoskillit.execution.backends.claude import (
    assert_agent_teams_inactive,
    detect_repository_agent_teams_setting,
    neutralize_repository_agent_teams_settings,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def _write_settings(root: Path, name: str, env: dict) -> None:
    claude_dir = root / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    payload = {"env": env}
    (claude_dir / name).write_text(json.dumps(payload), encoding="utf-8")


def test_detect_settings_file_value(tmp_path: Path) -> None:
    _write_settings(tmp_path, "settings.json", {"CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1"})
    value, path = detect_repository_agent_teams_setting(tmp_path)
    assert value == "1"
    assert path.endswith("settings.json")


def test_detect_local_settings_file_value(tmp_path: Path) -> None:
    _write_settings(
        tmp_path, "settings.local.json", {"CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "true"}
    )
    value, _path = detect_repository_agent_teams_setting(tmp_path)
    assert value == "true"


def test_detect_returns_none_when_no_settings(tmp_path: Path) -> None:
    value, path = detect_repository_agent_teams_setting(tmp_path)
    assert value is None
    assert path == ""


def test_detect_returns_none_when_no_conflict(tmp_path: Path) -> None:
    _write_settings(tmp_path, "settings.json", {"OTHER_VAR": "x"})
    value, path = detect_repository_agent_teams_setting(tmp_path)
    assert value is None
    assert path == ""


def test_neutralize_strips_env_var(tmp_path: Path) -> None:
    _write_settings(
        tmp_path,
        "settings.json",
        {"CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1", "OTHER": "x"},
    )
    modified = neutralize_repository_agent_teams_settings(tmp_path)
    assert modified == 1
    parsed = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    assert "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS" not in parsed["env"]
    assert parsed["env"]["OTHER"] == "x"


def test_neutralize_handles_both_files(tmp_path: Path) -> None:
    _write_settings(tmp_path, "settings.json", {"CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1"})
    _write_settings(
        tmp_path,
        "settings.local.json",
        {"CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "true"},
    )
    modified = neutralize_repository_agent_teams_settings(tmp_path)
    assert modified == 2


def test_neutralize_skips_unaffected_files(tmp_path: Path) -> None:
    _write_settings(tmp_path, "settings.json", {"OTHER": "x"})
    modified = neutralize_repository_agent_teams_settings(tmp_path)
    assert modified == 0


def test_neutralize_handles_no_settings(tmp_path: Path) -> None:
    modified = neutralize_repository_agent_teams_settings(tmp_path)
    assert modified == 0


def test_assert_inactive_passes_when_clean(tmp_path: Path) -> None:
    assert_agent_teams_inactive({}, str(tmp_path), force_inactive=True)


def test_assert_inactive_fails_when_settings_has_conflict(tmp_path: Path) -> None:
    _write_settings(tmp_path, "settings.json", {"CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1"})
    with pytest.raises(RuntimeError, match="settings.json"):
        assert_agent_teams_inactive({}, str(tmp_path), force_inactive=True)


def test_assert_inactive_fails_when_env_var_set() -> None:
    with pytest.raises(RuntimeError, match="set to '1'"):
        assert_agent_teams_inactive(
            {"CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1"},
            None,
            force_inactive=True,
        )


def test_assert_inactive_skips_when_force_false(tmp_path: Path) -> None:
    _write_settings(tmp_path, "settings.json", {"CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1"})
    # When force_inactive=False, the assertion is a no-op
    assert_agent_teams_inactive({}, str(tmp_path), force_inactive=False)
