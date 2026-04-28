"""Tests: fleet CLI campaign command."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.cli._fleet import fleet_campaign as _fleet_campaign
from tests.cli._fleet_helpers import (
    _capture_subprocess,
    _setup_existing_campaign_state,
    _stub_campaign_resolution,
    _stub_guards,
)

pytestmark = [pytest.mark.layer("cli"), pytest.mark.medium, pytest.mark.feature("fleet")]


def test_fleet_run_exits_when_campaign_not_found(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """fleet run exits 1 when campaign name doesn't resolve."""
    monkeypatch.chdir(tmp_path)
    _stub_guards(monkeypatch)
    with pytest.raises(SystemExit, match="1"):
        _fleet_campaign("nonexistent-campaign")


def test_fleet_run_sets_session_type_fleet(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Subprocess env includes AUTOSKILLIT_SESSION_TYPE=fleet."""
    _stub_guards(monkeypatch)
    monkeypatch.chdir(tmp_path)
    _stub_campaign_resolution(monkeypatch, tmp_path, "test-campaign")
    captured = _capture_subprocess(monkeypatch)
    _fleet_campaign("test-campaign")
    env = captured["env"]
    assert "AUTOSKILLIT_SESSION_TYPE" in env and env["AUTOSKILLIT_SESSION_TYPE"] == "fleet"
    assert "AUTOSKILLIT_CAMPAIGN_ID" in env
    assert "AUTOSKILLIT_CAMPAIGN_STATE_PATH" in env
    assert captured["env"].get("AUTOSKILLIT_FLEET_MODE") == "campaign"


def test_fleet_run_writes_initial_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """state.json exists in fleet temp dir after launch."""
    _stub_guards(monkeypatch)
    monkeypatch.chdir(tmp_path)
    _stub_campaign_resolution(monkeypatch, tmp_path, "test-campaign")
    _capture_subprocess(monkeypatch)
    _fleet_campaign("test-campaign")
    state_dirs = list((tmp_path / ".autoskillit" / "temp" / "fleet").iterdir())
    assert len(state_dirs) == 1
    state_file = state_dirs[0] / "state.json"
    assert state_file.exists()


def test_fleet_run_resume_reads_existing_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """--resume-campaign reuses existing campaign_id and passes completed block to prompt."""
    _stub_guards(monkeypatch)
    monkeypatch.chdir(tmp_path)
    campaign_id = "abc123def456ab12"
    _setup_existing_campaign_state(tmp_path, campaign_id, "test-campaign")
    _stub_campaign_resolution(monkeypatch, tmp_path, "test-campaign")
    captured = _capture_subprocess(monkeypatch)
    _fleet_campaign("test-campaign", resume_campaign=campaign_id)
    assert captured["env"]["AUTOSKILLIT_CAMPAIGN_ID"] == campaign_id


def test_fleet_run_exit_code_passthrough(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """fleet run propagates subprocess exit code."""
    import subprocess

    _stub_guards(monkeypatch)
    monkeypatch.chdir(tmp_path)
    _stub_campaign_resolution(monkeypatch, tmp_path, "test-campaign")
    monkeypatch.setattr("autoskillit.cli._init_helpers._is_plugin_installed", lambda: True)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: type("R", (), {"returncode": 2, "stdout": "", "stderr": ""})(),
    )
    with pytest.raises(SystemExit) as exc_info:
        _fleet_campaign("test-campaign")
    assert exc_info.value.code == 2


def test_fleet_campaign_exits_when_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """fleet_campaign exits 1 when fleet feature is disabled via feature gate."""
    monkeypatch.chdir(tmp_path)
    _stub_guards(monkeypatch)
    checked_features: list[str] = []
    monkeypatch.setattr(
        "autoskillit.cli._fleet.is_feature_enabled",
        lambda name, features, *, experimental_enabled=False: (
            checked_features.append(name) or False
        ),
    )
    monkeypatch.setattr(
        "autoskillit.config.load_config",
        lambda path: type("C", (), {"features": {}, "experimental_enabled": False})(),
    )
    with pytest.raises(SystemExit) as exc_info:
        _fleet_campaign("any-campaign")
    assert exc_info.value.code == 1
    assert "fleet" in checked_features
