"""Tests: fleet CLI campaign command."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from autoskillit.cli._fleet import fleet_campaign as _fleet_campaign
from tests.cli._fleet_helpers import (
    _capture_subprocess,
    _setup_campaign_with_status,
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


def _stub_list_campaign_recipes(monkeypatch: pytest.MonkeyPatch, names: list[str]) -> list[object]:
    """Stub list_campaign_recipes to return mock items with given names."""
    items = []
    for name in names:
        r = MagicMock()
        r.name = name
        r.description = f"Description for {name}"
        items.append(r)

    result = MagicMock()
    result.items = items
    monkeypatch.setattr("autoskillit.recipe.list_campaign_recipes", lambda *a, **kw: result)
    return items


def test_fleet_campaign_no_name_shows_menu_and_launches(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """When campaign_name is omitted, menu is shown and selected campaign launches."""
    _stub_guards(monkeypatch)
    monkeypatch.chdir(tmp_path)
    _stub_list_campaign_recipes(monkeypatch, ["campaign-alpha", "campaign-beta"])
    _stub_campaign_resolution(monkeypatch, tmp_path, "campaign-alpha")
    monkeypatch.setattr("autoskillit.cli._menu.timed_prompt", lambda *a, **kw: "1")
    captured = _capture_subprocess(monkeypatch)
    _fleet_campaign(campaign_name=None)
    assert "AUTOSKILLIT_CAMPAIGN_ID" in captured["env"]
    assert "AUTOSKILLIT_CAMPAIGN_STATE_PATH" in captured["env"]
    from autoskillit.fleet import read_state

    state = read_state(Path(captured["env"]["AUTOSKILLIT_CAMPAIGN_STATE_PATH"]))
    assert state is not None
    assert state.campaign_name == "campaign-alpha"
    out = capsys.readouterr().out
    assert "Available campaigns:" in out
    assert "campaign-alpha" in out
    assert "campaign-beta" in out


def test_fleet_campaign_no_name_no_campaigns_exits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When no campaigns exist and name is omitted, exits with message."""
    _stub_guards(monkeypatch)
    monkeypatch.chdir(tmp_path)
    _stub_list_campaign_recipes(monkeypatch, [])
    with pytest.raises(SystemExit, match="1"):
        _fleet_campaign(campaign_name=None)


def test_fleet_campaign_no_name_invalid_selection_exits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Empty selection from menu exits with code 1."""
    _stub_guards(monkeypatch)
    monkeypatch.chdir(tmp_path)
    _stub_list_campaign_recipes(monkeypatch, ["campaign-alpha"])
    monkeypatch.setattr("autoskillit.cli._menu.timed_prompt", lambda *a, **kw: "")
    with pytest.raises(SystemExit, match="1"):
        _fleet_campaign(campaign_name=None)


def test_fleet_campaign_resume_no_name_lists_active_campaigns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Resume without name shows only active (non-terminal) campaigns."""
    _stub_guards(monkeypatch)
    monkeypatch.chdir(tmp_path)

    _setup_existing_campaign_state(tmp_path, "active-id-1111111", "campaign-active-1")
    _setup_existing_campaign_state(tmp_path, "active-id-2222222", "campaign-active-2")
    _setup_campaign_with_status(
        tmp_path, "terminal-id-333333", "campaign-terminal", status="success"
    )

    _stub_campaign_resolution(monkeypatch, tmp_path, "campaign-active-1")
    monkeypatch.setattr("autoskillit.cli._menu.timed_prompt", lambda *a, **kw: "1")
    monkeypatch.setattr(
        "autoskillit.fleet.resume_campaign_from_state",
        lambda *a, **kw: MagicMock(
            completed_dispatches_block="", next_dispatch_name="", is_resumable=False
        ),
    )
    captured = _capture_subprocess(monkeypatch)

    _fleet_campaign(campaign_name=None, resume_campaign="__pick__")

    assert captured["env"].get("AUTOSKILLIT_CAMPAIGN_ID") == "active-id-1111111"
    assert "terminal-id-333333" not in captured["env"].get("AUTOSKILLIT_CAMPAIGN_ID", "")
    out = capsys.readouterr().out
    assert "Active campaigns (resumable):" in out
    assert "campaign-active-1" in out
    assert "campaign-active-2" in out
    assert "campaign-terminal" not in out


class TestFleetCampaignResumeHaltedExits:
    def test_resume_halted_campaign_exits_with_error(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _stub_guards(monkeypatch)
        monkeypatch.chdir(tmp_path)
        campaign_id = "halted-campaign-id1"
        _setup_existing_campaign_state(tmp_path, campaign_id, "test-campaign")
        _stub_campaign_resolution(monkeypatch, tmp_path, "test-campaign")

        from autoskillit.fleet import FLEET_HALTED_SENTINEL

        monkeypatch.setattr(
            "autoskillit.fleet.resume_campaign_from_state",
            lambda *a, **kw: MagicMock(
                completed_dispatches_block=FLEET_HALTED_SENTINEL,
                next_dispatch_name="",
                is_resumable=False,
            ),
        )
        with pytest.raises(SystemExit, match="1"):
            _fleet_campaign("test-campaign", resume_campaign=campaign_id)
        err = capsys.readouterr().err
        assert "halted" in err.lower()
        assert "continue_on_failure" in err

    def test_resume_halted_does_not_launch_session(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _stub_guards(monkeypatch)
        monkeypatch.chdir(tmp_path)
        campaign_id = "halted-campaign-id2"
        _setup_existing_campaign_state(tmp_path, campaign_id, "test-campaign")
        _stub_campaign_resolution(monkeypatch, tmp_path, "test-campaign")

        from autoskillit.fleet import FLEET_HALTED_SENTINEL

        monkeypatch.setattr(
            "autoskillit.fleet.resume_campaign_from_state",
            lambda *a, **kw: MagicMock(
                completed_dispatches_block=FLEET_HALTED_SENTINEL,
                next_dispatch_name="",
                is_resumable=False,
            ),
        )
        launch_called = False
        original_launch = None

        def _track_launch(*a: object, **kw: object) -> None:
            nonlocal launch_called
            launch_called = True

        monkeypatch.setattr("autoskillit.cli._fleet._launch_fleet_session", _track_launch)
        with pytest.raises(SystemExit):
            _fleet_campaign("test-campaign", resume_campaign=campaign_id)
        assert not launch_called
