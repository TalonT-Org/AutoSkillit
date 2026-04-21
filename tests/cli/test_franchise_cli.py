"""Tests for the franchise CLI sub-app (run, list, status)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from autoskillit.cli._franchise import franchise_list as _franchise_list
from autoskillit.cli._franchise import franchise_run as _franchise_run
from autoskillit.cli._franchise import franchise_status as _franchise_status

pytestmark = [pytest.mark.layer("cli"), pytest.mark.medium]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _stub_guards(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub all franchise_run guard conditions to pass."""
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_SESSION_TYPE", raising=False)
    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/claude")


def _stub_campaign_resolution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, name: str
) -> MagicMock:
    """Stub find_campaign_by_name, load_recipe, and validate_recipe."""
    campaign_path = tmp_path / f"{name}.yaml"
    campaign_path.write_text("")

    recipe_info = MagicMock()
    recipe_info.name = name
    recipe_info.path = campaign_path

    recipe = MagicMock()
    recipe.name = name
    recipe.dispatches = []
    recipe.continue_on_failure = False
    recipe.description = f"Test campaign {name}"

    monkeypatch.setattr("autoskillit.recipe.find_campaign_by_name", lambda *a, **kw: recipe_info)
    monkeypatch.setattr("autoskillit.recipe.load_recipe", lambda *a, **kw: recipe)
    monkeypatch.setattr("autoskillit.recipe.validate_recipe", lambda *a: [])
    return recipe


def _capture_subprocess(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Replace subprocess.run with a capturing stub. Returns captured dict."""
    captured: dict = {}

    def mock_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        captured["cmd"] = list(cmd)
        captured["env"] = kwargs.get("env", {}) or {}
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(subprocess, "run", mock_run)
    return captured


def _setup_existing_campaign_state(tmp_path: Path, campaign_id: str, campaign_name: str) -> None:
    """Create a state.json for an existing campaign in tmp_path."""
    from autoskillit.franchise import DispatchRecord, write_initial_state

    state_dir = tmp_path / ".autoskillit" / "temp" / "franchise" / campaign_id
    state_dir.mkdir(parents=True)
    dispatches = [DispatchRecord(name="dispatch-1")]
    write_initial_state(
        state_dir / "state.json", campaign_id, campaign_name, "manifest.yaml", dispatches
    )


def _setup_campaign_recipes(tmp_path: Path, names: list[str]) -> None:
    """Create campaign recipe YAML files under .autoskillit/recipes/campaigns/."""
    campaigns_dir = tmp_path / ".autoskillit" / "recipes" / "campaigns"
    campaigns_dir.mkdir(parents=True)
    for name in names:
        recipe_yaml = (
            f"name: {name}\ndescription: Test campaign {name}\nkind: campaign\ndispatches: []\n"
        )
        (campaigns_dir / f"{name}.yaml").write_text(recipe_yaml)


def _mock_batch_delete(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Stub batch_delete and return the mock."""
    mock = MagicMock(return_value={"deleted": [], "delete_failures": [], "preserved": []})
    monkeypatch.setattr("autoskillit.workspace.batch_delete", mock)
    return mock


# ---------------------------------------------------------------------------
# T1. franchise run — CLAUDECODE guard
# ---------------------------------------------------------------------------


def test_franchise_run_rejects_inside_claude_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """franchise run exits 1 when CLAUDECODE env is set."""
    monkeypatch.setenv("CLAUDECODE", "1")
    with pytest.raises(SystemExit, match="1"):
        _franchise_run("my-campaign")


# ---------------------------------------------------------------------------
# T2. franchise run — SESSION_TYPE=leaf guard
# ---------------------------------------------------------------------------


def test_franchise_run_rejects_leaf_session_type(monkeypatch: pytest.MonkeyPatch) -> None:
    """franchise run exits 1 when ambient SESSION_TYPE is leaf."""
    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "leaf")
    monkeypatch.delenv("CLAUDECODE", raising=False)
    with pytest.raises(SystemExit, match="1"):
        _franchise_run("my-campaign")


# ---------------------------------------------------------------------------
# T3. franchise run — claude not on PATH
# ---------------------------------------------------------------------------


def test_franchise_run_exits_when_claude_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """franchise run exits 1 when claude is not on PATH."""
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_SESSION_TYPE", raising=False)
    monkeypatch.setattr(shutil, "which", lambda _: None)
    with pytest.raises(SystemExit, match="1"):
        _franchise_run("my-campaign")


# ---------------------------------------------------------------------------
# T4. franchise run — campaign not found
# ---------------------------------------------------------------------------


def test_franchise_run_exits_when_campaign_not_found(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """franchise run exits 1 when campaign name doesn't resolve."""
    monkeypatch.chdir(tmp_path)
    _stub_guards(monkeypatch)
    with pytest.raises(SystemExit, match="1"):
        _franchise_run("nonexistent-campaign")


# ---------------------------------------------------------------------------
# T5. franchise run — subprocess receives correct env
# ---------------------------------------------------------------------------


def test_franchise_run_sets_session_type_franchise(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Subprocess env includes AUTOSKILLIT_SESSION_TYPE=franchise."""
    _stub_guards(monkeypatch)
    monkeypatch.chdir(tmp_path)
    _stub_campaign_resolution(monkeypatch, tmp_path, "test-campaign")
    captured = _capture_subprocess(monkeypatch)
    _franchise_run("test-campaign")
    env = captured["env"]
    assert env["AUTOSKILLIT_SESSION_TYPE"] == "franchise"
    assert "AUTOSKILLIT_CAMPAIGN_ID" in env
    assert "AUTOSKILLIT_CAMPAIGN_STATE_PATH" in env


# ---------------------------------------------------------------------------
# T6. franchise run — state.json written before launch
# ---------------------------------------------------------------------------


def test_franchise_run_writes_initial_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """state.json exists in franchise temp dir after launch."""
    _stub_guards(monkeypatch)
    monkeypatch.chdir(tmp_path)
    _stub_campaign_resolution(monkeypatch, tmp_path, "test-campaign")
    _capture_subprocess(monkeypatch)
    _franchise_run("test-campaign")
    state_dirs = list((tmp_path / ".autoskillit" / "temp" / "franchise").iterdir())
    assert len(state_dirs) == 1
    state_file = state_dirs[0] / "state.json"
    assert state_file.exists()


# ---------------------------------------------------------------------------
# T7. franchise run — resume loads existing state
# ---------------------------------------------------------------------------


def test_franchise_run_resume_reads_existing_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """--resume-campaign reuses existing campaign_id and passes completed block to prompt."""
    _stub_guards(monkeypatch)
    monkeypatch.chdir(tmp_path)
    campaign_id = "abc123def456ab12"
    _setup_existing_campaign_state(tmp_path, campaign_id, "test-campaign")
    _stub_campaign_resolution(monkeypatch, tmp_path, "test-campaign")
    captured = _capture_subprocess(monkeypatch)
    _franchise_run("test-campaign", resume_campaign=campaign_id)
    assert captured["env"]["AUTOSKILLIT_CAMPAIGN_ID"] == campaign_id


# ---------------------------------------------------------------------------
# T8. franchise list — empty output
# ---------------------------------------------------------------------------


def test_franchise_list_no_campaigns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """franchise list prints 'No campaigns found' when directory is empty."""
    monkeypatch.chdir(tmp_path)
    _franchise_list()
    assert "No campaigns found" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# T9. franchise list — tabular output
# ---------------------------------------------------------------------------


def test_franchise_list_shows_campaigns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """franchise list shows tabular campaign listing."""
    _setup_campaign_recipes(tmp_path, ["alpha", "beta"])
    monkeypatch.chdir(tmp_path)
    _franchise_list()
    output = capsys.readouterr().out
    assert "alpha" in output
    assert "beta" in output


# ---------------------------------------------------------------------------
# T10. franchise status — reads state.json
# ---------------------------------------------------------------------------


def test_franchise_status_shows_dispatches(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """franchise status shows dispatch table from state.json."""
    _setup_existing_campaign_state(tmp_path, "abc123", "test-campaign")
    monkeypatch.chdir(tmp_path)
    _franchise_status("abc123")
    output = capsys.readouterr().out
    assert "test-campaign" in output


# ---------------------------------------------------------------------------
# T11. franchise status — JSON output
# ---------------------------------------------------------------------------


def test_franchise_status_json_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """--json produces valid JSON output."""
    _setup_existing_campaign_state(tmp_path, "abc123", "test-campaign")
    monkeypatch.chdir(tmp_path)
    _franchise_status("abc123", json_output=True)
    output = capsys.readouterr().out
    data = json.loads(output)
    assert data["campaign_id"] == "abc123"


# ---------------------------------------------------------------------------
# T12. franchise status — cleanup
# ---------------------------------------------------------------------------


def test_franchise_status_cleanup_calls_batch_delete(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """--cleanup invokes clone_registry.batch_delete with owner filter."""
    _setup_existing_campaign_state(tmp_path, "abc123", "test-campaign")
    monkeypatch.chdir(tmp_path)
    mock_delete = _mock_batch_delete(monkeypatch)
    _franchise_status("abc123", cleanup=True)
    assert mock_delete.called
    assert mock_delete.call_args.args[0] == ""
    assert mock_delete.call_args.kwargs.get("owner") == "abc123"


# ---------------------------------------------------------------------------
# T16. franchise run — exit codes
# ---------------------------------------------------------------------------


def test_franchise_run_exit_code_passthrough(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """franchise run propagates subprocess exit code."""
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
        _franchise_run("test-campaign")
    assert exc_info.value.code == 2


# ---------------------------------------------------------------------------
# T17. franchise status — no state file
# ---------------------------------------------------------------------------


def test_franchise_status_missing_campaign(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """franchise status prints error for unknown campaign_id."""
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit, match="1"):
        _franchise_status("nonexistent")
