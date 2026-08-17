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
    _interactive_invocation_environment_policy,
    assert_agent_teams_inactive,
    detect_repository_agent_teams_setting,
    find_malformed_agent_teams_settings,
    neutralize_repository_agent_teams_settings,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def _write_settings(root: Path, name: str, env: dict) -> None:
    claude_dir = root / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    payload = {"env": env}
    (claude_dir / name).write_text(json.dumps(payload), encoding="utf-8")


def _write_raw_settings(root: Path, name: str, body: str) -> None:
    claude_dir = root / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    (claude_dir / name).write_text(body, encoding="utf-8")


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


# --- find_malformed_agent_teams_settings (fail-closed on malformed files) ---


def test_find_malformed_returns_empty_when_no_settings(tmp_path: Path) -> None:
    """No settings files at all is not a malformed condition."""
    assert find_malformed_agent_teams_settings(tmp_path) == []


def test_find_malformed_returns_empty_for_well_formed_settings(tmp_path: Path) -> None:
    """A well-formed settings file must not be reported as malformed."""
    _write_settings(tmp_path, "settings.json", {"OTHER_VAR": "x"})
    assert find_malformed_agent_teams_settings(tmp_path) == []


def test_find_malformed_detects_invalid_json(tmp_path: Path) -> None:
    """A settings file with garbage JSON is a fail-closed malformed condition."""
    _write_raw_settings(tmp_path, "settings.json", "{not valid json")
    result = find_malformed_agent_teams_settings(tmp_path)
    assert len(result) == 1
    assert result[0].endswith("settings.json")


def test_find_malformed_detects_non_dict_content(tmp_path: Path) -> None:
    """A JSON-list at the top level is not a valid settings object."""
    _write_raw_settings(tmp_path, "settings.json", '["CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS"]')
    result = find_malformed_agent_teams_settings(tmp_path)
    assert len(result) == 1


def test_find_malformed_detects_non_dict_env(tmp_path: Path) -> None:
    """A settings file whose ``env`` field is not a dict is malformed."""
    _write_raw_settings(tmp_path, "settings.json", json.dumps({"env": "1"}))
    result = find_malformed_agent_teams_settings(tmp_path)
    assert len(result) == 1
    assert result[0].endswith("settings.json")


def test_find_malformed_detects_settings_local_json(tmp_path: Path) -> None:
    """settings.local.json is also covered."""
    _write_raw_settings(tmp_path, "settings.local.json", "{garbage")
    result = find_malformed_agent_teams_settings(tmp_path)
    assert len(result) == 1
    assert result[0].endswith("settings.local.json")


def test_find_malformed_returns_none_project_root() -> None:
    """A None project_root never produces a malformed report."""
    assert find_malformed_agent_teams_settings(None) == []


def test_assert_inactive_fails_on_malformed_settings_json(tmp_path: Path) -> None:
    """Garbage JSON must trigger a fail-closed RuntimeError, not a silent pass."""
    _write_raw_settings(tmp_path, "settings.json", "{garbage")
    with pytest.raises(RuntimeError, match="could not be parsed"):
        assert_agent_teams_inactive({}, str(tmp_path), force_inactive=True)


def test_assert_inactive_fails_on_non_dict_env_settings(tmp_path: Path) -> None:
    """A settings file with a non-dict env field is malformed, so must raise."""
    _write_raw_settings(tmp_path, "settings.json", json.dumps({"env": "1"}))
    with pytest.raises(RuntimeError, match="could not be parsed"):
        assert_agent_teams_inactive({}, str(tmp_path), force_inactive=True)


def test_assert_inactive_silent_pass_when_force_false_even_if_malformed(tmp_path: Path) -> None:
    """When force_inactive=False, malformed files are reported but never block."""
    _write_raw_settings(tmp_path, "settings.json", "{garbage")
    assert_agent_teams_inactive({}, str(tmp_path), force_inactive=False)


def test_interactive_policy_reports_malformed_settings(tmp_path: Path) -> None:
    """The interactive cook/order policy must also flag malformed settings."""
    _write_raw_settings(tmp_path, "settings.json", "{garbage")
    errors = _interactive_invocation_environment_policy({}, str(tmp_path))
    assert any("could not be parsed" in e for e in errors)
