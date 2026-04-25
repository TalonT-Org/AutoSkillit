"""Tests for the fleet CLI sub-app (run, list, status)."""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from cyclopts import App

if TYPE_CHECKING:
    from autoskillit.fleet import CampaignState

from autoskillit.cli._fleet import fleet_campaign as _fleet_campaign
from autoskillit.cli._fleet import fleet_dispatch as _fleet_dispatch
from autoskillit.cli._fleet import fleet_list as _fleet_list
from autoskillit.cli._fleet import fleet_status as _fleet_status

pytestmark = [pytest.mark.layer("cli"), pytest.mark.medium, pytest.mark.feature("fleet")]


@pytest.fixture(autouse=True)
def _fleet_config(tmp_path: Path) -> None:
    """Ensure .autoskillit/config.yaml enables fleet so _require_fleet passes."""
    cfg_dir = tmp_path / ".autoskillit"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg_file = cfg_dir / "config.yaml"
    if not cfg_file.exists():
        cfg_file.write_text("features:\n  fleet: true\n")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _stub_guards(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub all fleet_run guard conditions to pass."""
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
    from autoskillit.fleet import DispatchRecord, write_initial_state

    state_dir = tmp_path / ".autoskillit" / "temp" / "fleet" / campaign_id
    state_dir.mkdir(parents=True)
    dispatches = [DispatchRecord(name="dispatch-1")]
    write_initial_state(
        state_dir / "state.json", campaign_id, campaign_name, "manifest.yaml", dispatches
    )


def _setup_campaign_with_tokens(
    tmp_path: Path,
    campaign_id: str,
    campaign_name: str,
    *,
    token_usage: dict | None = None,
) -> None:
    """Create a state.json with a succeeded dispatch and token usage."""
    from autoskillit.fleet import DispatchRecord, DispatchStatus, write_initial_state

    state_dir = tmp_path / ".autoskillit" / "temp" / "fleet" / campaign_id
    state_dir.mkdir(parents=True)
    if token_usage is None:
        token_usage = {}
    now = time.time()
    dispatches = [
        DispatchRecord(
            name="dispatch-1",
            status=DispatchStatus.SUCCESS,
            token_usage=token_usage,
            l2_session_log_dir="/tmp/test-session-log",
            started_at=now - 60.0,
            ended_at=now,
        )
    ]
    write_initial_state(
        state_dir / "state.json", campaign_id, campaign_name, "manifest.yaml", dispatches
    )


def _setup_campaign_with_status(
    tmp_path: Path, campaign_id: str, campaign_name: str, *, status: str
) -> None:
    """Create a state.json with a single dispatch at a given status."""
    from autoskillit.fleet import DispatchRecord, DispatchStatus, write_initial_state

    state_dir = tmp_path / ".autoskillit" / "temp" / "fleet" / campaign_id
    state_dir.mkdir(parents=True)
    dispatches = [DispatchRecord(name="dispatch-1", status=DispatchStatus(status))]
    write_initial_state(
        state_dir / "state.json", campaign_id, campaign_name, "manifest.yaml", dispatches
    )


def _setup_campaign_with_sessions(
    tmp_path: Path,
    campaign_id: str,
    campaign_name: str,
    *,
    dispatches: list[tuple[str, str, str]],
) -> None:
    """Create a state.json with dispatches that have l2_session_id set."""
    from autoskillit.fleet import DispatchRecord, DispatchStatus, write_initial_state

    state_dir = tmp_path / ".autoskillit" / "temp" / "fleet" / campaign_id
    state_dir.mkdir(parents=True)
    records = [
        DispatchRecord(name=name, status=DispatchStatus(status), l2_session_id=session_id)
        for name, status, session_id in dispatches
    ]
    write_initial_state(
        state_dir / "state.json", campaign_id, campaign_name, "manifest.yaml", records
    )


def _make_state(*, statuses: list[str]) -> CampaignState:
    """Build an in-memory CampaignState for unit-testing _compute_exit_code."""
    from autoskillit.fleet import CampaignState, DispatchRecord, DispatchStatus

    dispatches = [
        DispatchRecord(name=f"d{i}", status=DispatchStatus(s)) for i, s in enumerate(statuses)
    ]
    return CampaignState(
        schema_version=2,
        campaign_id="test-id",
        campaign_name="test",
        manifest_path="manifest.yaml",
        started_at=0.0,
        dispatches=dispatches,
    )


def _make_state_with_tokens(*, input_total: int) -> CampaignState:
    """Build an in-memory CampaignState with known token totals."""
    from autoskillit.fleet import CampaignState, DispatchRecord, DispatchStatus

    dispatches = [
        DispatchRecord(
            name="dispatch-1",
            status=DispatchStatus.SUCCESS,
            token_usage={"input_tokens": input_total},
        )
    ]
    return CampaignState(
        schema_version=2,
        campaign_id="test-id",
        campaign_name="test",
        manifest_path="manifest.yaml",
        started_at=0.0,
        dispatches=dispatches,
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
# T1. fleet run — CLAUDECODE guard
# ---------------------------------------------------------------------------


def test_fleet_dispatch_rejects_inside_claude_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """fleet dispatch exits 1 when CLAUDECODE env is set."""
    monkeypatch.setenv("CLAUDECODE", "1")
    with pytest.raises(SystemExit, match="1"):
        _fleet_dispatch()


# ---------------------------------------------------------------------------
# T2. fleet run — SESSION_TYPE=leaf guard
# ---------------------------------------------------------------------------


def test_fleet_dispatch_rejects_leaf_session_type(monkeypatch: pytest.MonkeyPatch) -> None:
    """fleet dispatch exits 1 when ambient SESSION_TYPE is leaf."""
    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "leaf")
    monkeypatch.delenv("CLAUDECODE", raising=False)
    with pytest.raises(SystemExit, match="1"):
        _fleet_dispatch()


# ---------------------------------------------------------------------------
# T3. fleet run — claude not on PATH
# ---------------------------------------------------------------------------


def test_fleet_dispatch_exits_when_claude_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """fleet dispatch exits 1 when claude is not on PATH."""
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_SESSION_TYPE", raising=False)
    monkeypatch.setattr(shutil, "which", lambda _: None)
    with pytest.raises(SystemExit, match="1"):
        _fleet_dispatch()


# ---------------------------------------------------------------------------
# T4. fleet run — campaign not found
# ---------------------------------------------------------------------------


def test_fleet_run_exits_when_campaign_not_found(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """fleet run exits 1 when campaign name doesn't resolve."""
    monkeypatch.chdir(tmp_path)
    _stub_guards(monkeypatch)
    with pytest.raises(SystemExit, match="1"):
        _fleet_campaign("nonexistent-campaign")


# ---------------------------------------------------------------------------
# T5. fleet run — subprocess receives correct env
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# T6. fleet run — state.json written before launch
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# T7. fleet run — resume loads existing state
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# T8. fleet list — empty output
# ---------------------------------------------------------------------------


def test_fleet_list_no_campaigns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """fleet list prints 'No campaigns found' when directory is empty."""
    monkeypatch.chdir(tmp_path)
    _fleet_list()
    assert "No campaigns found" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# T9. fleet list — tabular output
# ---------------------------------------------------------------------------


def test_fleet_list_shows_campaigns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """fleet list shows tabular campaign listing."""
    _setup_campaign_recipes(tmp_path, ["alpha", "beta"])
    monkeypatch.chdir(tmp_path)
    _fleet_list()
    output = capsys.readouterr().out
    assert "alpha" in output
    assert "beta" in output


# ---------------------------------------------------------------------------
# T10 (existing). fleet status — reads state.json (updated for exit code)
# ---------------------------------------------------------------------------


def test_fleet_status_shows_dispatches(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """fleet status shows dispatch table from state.json."""
    _setup_existing_campaign_state(tmp_path, "abc123", "test-campaign")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit):
        _fleet_status("abc123")
    output = capsys.readouterr().out
    assert "test-campaign" in output


# ---------------------------------------------------------------------------
# T11 (existing). fleet status — JSON output (updated for exit code)
# ---------------------------------------------------------------------------


def test_fleet_status_json_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """--json produces valid JSON output with campaign_id."""
    _setup_existing_campaign_state(tmp_path, "abc123", "test-campaign")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit):
        _fleet_status("abc123", json_output=True)
    output = capsys.readouterr().out
    data = json.loads(output)
    assert data["campaign_id"] == "abc123"


# ---------------------------------------------------------------------------
# T12 (existing). fleet status — cleanup (updated for exit code)
# ---------------------------------------------------------------------------


def test_fleet_status_cleanup_calls_batch_delete(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """--cleanup invokes clone_registry.batch_delete with owner filter."""
    _setup_existing_campaign_state(tmp_path, "abc123", "test-campaign")
    monkeypatch.chdir(tmp_path)
    mock_delete = _mock_batch_delete(monkeypatch)
    with pytest.raises(SystemExit):
        _fleet_status("abc123", cleanup=True)
    assert mock_delete.called
    assert mock_delete.call_args.args[0] == ""
    assert mock_delete.call_args.kwargs.get("owner") == "abc123"


# ---------------------------------------------------------------------------
# T16. fleet run — exit codes
# ---------------------------------------------------------------------------


def test_fleet_run_exit_code_passthrough(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """fleet run propagates subprocess exit code."""
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


# ---------------------------------------------------------------------------
# T17 (existing). fleet status — no state file (exit code updated to 3)
# ---------------------------------------------------------------------------


def test_fleet_status_missing_campaign(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """fleet status exits 3 for unknown campaign_id."""
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit, match="3"):
        _fleet_status("nonexistent")


# ---------------------------------------------------------------------------
# New T1. Table columns match 8-column specification
# ---------------------------------------------------------------------------


def test_fleet_status_table_columns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """fleet status output contains all 8 header labels in order."""
    _setup_campaign_with_tokens(tmp_path, "cid01", "my-campaign")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit):
        _fleet_status("cid01")
    out = capsys.readouterr().out
    assert out.index("NAME") < out.index("STATUS") < out.index("ELAPSED")
    assert out.index("INPUT") < out.index("OUTPUT") < out.index("CACHE_RD")
    assert "SESSION_LOG" in out
    assert "CACHE_WR" in out


# ---------------------------------------------------------------------------
# New T2. Numeric columns right-aligned
# ---------------------------------------------------------------------------


def test_status_numeric_columns_right_aligned() -> None:
    """Numeric column definitions use align='>'."""
    from autoskillit.cli._fleet import _STATUS_COLUMNS

    numeric_labels = {"ELAPSED", "INPUT", "OUTPUT", "CACHE_RD", "CACHE_WR"}
    for col in _STATUS_COLUMNS:
        if col.label in numeric_labels:
            assert col.align == ">", f"{col.label} should be right-aligned"


# ---------------------------------------------------------------------------
# New T3. --json includes totals dict
# ---------------------------------------------------------------------------


def test_fleet_status_json_includes_totals(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """--json output has totals key with summed token counts."""
    _setup_campaign_with_tokens(
        tmp_path,
        "cid01",
        "test",
        token_usage={
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_creation_input_tokens": 10,
            "cache_read_input_tokens": 20,
        },
    )
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit):
        _fleet_status("cid01", json_output=True)
    data = json.loads(capsys.readouterr().out)
    assert data["totals"]["input_tokens"] == 100
    assert data["totals"]["output_tokens"] == 50
    assert data["totals"]["cache_read"] == 20
    assert data["totals"]["cache_creation"] == 10


# ---------------------------------------------------------------------------
# New T4. --json output has no ANSI escapes
# ---------------------------------------------------------------------------


def test_fleet_status_json_no_ansi(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """--json output must not contain ANSI escape sequences."""
    _setup_existing_campaign_state(tmp_path, "cid01", "test")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit):
        _fleet_status("cid01", json_output=True)
    out = capsys.readouterr().out
    assert "\x1b[" not in out


# ---------------------------------------------------------------------------
# New T5. Exit code 0: all dispatches succeeded
# ---------------------------------------------------------------------------


def test_exit_code_all_success() -> None:
    """_compute_exit_code returns 0 when all dispatches succeed or skip."""
    from autoskillit.cli._fleet import _compute_exit_code

    state = _make_state(statuses=["success", "skipped", "success"])
    assert _compute_exit_code(state) == 0


# ---------------------------------------------------------------------------
# New T6. Exit code 1: any dispatch failed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_status", ["failure", "interrupted", "refused", "released"])
def test_exit_code_any_failure(bad_status: str) -> None:
    """_compute_exit_code returns 1 when any dispatch is in a failure-class status."""
    from autoskillit.cli._fleet import _compute_exit_code

    state = _make_state(statuses=["success", bad_status])
    assert _compute_exit_code(state) == 1


# ---------------------------------------------------------------------------
# New T7. Exit code 2: any dispatch in-progress (no failures)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("progress_status", ["running", "pending"])
def test_exit_code_in_progress(progress_status: str) -> None:
    """_compute_exit_code returns 2 when any dispatch is in-progress and none failed."""
    from autoskillit.cli._fleet import _compute_exit_code

    state = _make_state(statuses=["success", progress_status])
    assert _compute_exit_code(state) == 2


# ---------------------------------------------------------------------------
# New T8. Exit code 3: state file missing/corrupt
# ---------------------------------------------------------------------------


def test_exit_code_3_on_missing_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """fleet status exits 3 when state file is absent."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".autoskillit" / "temp" / "fleet" / "cid01").mkdir(parents=True)
    with pytest.raises(SystemExit) as exc_info:
        _fleet_status("cid01")
    assert exc_info.value.code == 3


# ---------------------------------------------------------------------------
# New T9. Token cross-check warns on >5% divergence
# ---------------------------------------------------------------------------


def test_cross_check_warns_on_divergence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """_cross_check_tokens emits a stderr warning when divergence exceeds 5%."""
    from autoskillit.cli._fleet import _aggregate_totals, _cross_check_tokens

    (tmp_path / "sessions.jsonl").write_text("")
    state = _make_state_with_tokens(input_total=10000)
    state_totals = _aggregate_totals(state)

    monkeypatch.setattr(
        "autoskillit.execution.resolve_log_dir",
        lambda *a: tmp_path,
    )
    monkeypatch.setattr(
        "autoskillit.pipeline.tokens.DefaultTokenLog.load_from_log_dir",
        lambda self, *a, **kw: 1,
    )
    monkeypatch.setattr(
        "autoskillit.pipeline.tokens.DefaultTokenLog.compute_total",
        lambda self, **kw: {
            "input_tokens": 8000,
            "output_tokens": 0,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "total_elapsed_seconds": 0.0,
        },
    )
    _cross_check_tokens(state, state_totals)
    assert "diverge" in capsys.readouterr().err.lower()


# ---------------------------------------------------------------------------
# New T10. --watch exits on terminal campaign
# ---------------------------------------------------------------------------


def test_watch_exits_on_terminal_campaign(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """--watch detects all-terminal campaign and exits with code 0."""
    _setup_campaign_with_status(tmp_path, "cid01", "test", status="success")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    with pytest.raises(SystemExit) as exc_info:
        _fleet_status("cid01", watch=True)
    assert exc_info.value.code == 0


# ---------------------------------------------------------------------------
# New T11. --cleanup calls cleanup_session per terminal dispatch
# ---------------------------------------------------------------------------


def test_cleanup_per_dispatch_session(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """--cleanup calls cleanup_session for each dispatch l2_session_id."""
    _setup_campaign_with_sessions(
        tmp_path,
        "cid01",
        "test",
        dispatches=[("d1", "success", "sid-1"), ("d2", "failure", "sid-2")],
    )
    monkeypatch.chdir(tmp_path)
    _mock_batch_delete(monkeypatch)
    cleanup_calls: list[str] = []
    monkeypatch.setattr(
        "autoskillit.workspace.DefaultSessionSkillManager.cleanup_session",
        lambda self, sid: cleanup_calls.append(sid) or True,
    )
    with pytest.raises(SystemExit):
        _fleet_status("cid01", cleanup=True)
    assert "sid-1" in cleanup_calls
    assert "sid-2" in cleanup_calls


# ---------------------------------------------------------------------------
# New T12. Color-aware table respects NO_COLOR
# ---------------------------------------------------------------------------


def test_status_table_no_ansi_when_no_color(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """fleet status table contains no ANSI codes when NO_COLOR is set."""
    _setup_existing_campaign_state(tmp_path, "cid01", "test")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("NO_COLOR", "1")
    with pytest.raises(SystemExit):
        _fleet_status("cid01")
    out = capsys.readouterr().out
    assert "\x1b[" not in out


# ---------------------------------------------------------------------------
# New T13. Exit code priority: failure overrides in-progress
# ---------------------------------------------------------------------------


def test_exit_code_failure_over_in_progress() -> None:
    """_compute_exit_code returns 1 (failure) when mixed with in-progress."""
    from autoskillit.cli._fleet import _compute_exit_code

    state = _make_state(statuses=["failure", "running"])
    assert _compute_exit_code(state) == 1


# ---------------------------------------------------------------------------
# New T14. Totals row appears in table output
# ---------------------------------------------------------------------------


def test_status_table_shows_totals_row(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """fleet status table includes a TOTAL row."""
    _setup_campaign_with_tokens(
        tmp_path,
        "cid01",
        "test",
        token_usage={"input_tokens": 100, "output_tokens": 50},
    )
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit):
        _fleet_status("cid01")
    out = capsys.readouterr().out
    assert "TOTAL" in out.upper()
    # Verify the humanized token values actually appear in the TOTAL row
    assert "100" in out  # input_tokens=100 → "100"
    assert "50" in out  # output_tokens=50 → "50"


# ---------------------------------------------------------------------------
# CLI registration tests (T25–T27)
# ---------------------------------------------------------------------------


def _get_app() -> App:
    from autoskillit.cli.app import app

    return app


def _subcommand_names(app: App) -> set[str]:
    """Extract registered subcommand names from a cyclopts App."""
    names: set[str] = set()
    for meta in app._commands.values():  # type: ignore[attr-defined]
        name = meta.name
        if isinstance(name, tuple):
            names.update(name)
        elif isinstance(name, str):
            names.add(name)
    return names


def _find_command(app: App, name: str) -> object:
    """Find a command meta by command key (the registered name string)."""
    return app._commands.get(name)  # type: ignore[attr-defined]


class TestFleetCLIRegistration:
    def test_fleet_subcommand_registered(self) -> None:
        app = _get_app()
        names = _subcommand_names(app)
        assert "fleet" in names

    def test_fleet_status_accepts_reap_flag(self) -> None:
        from autoskillit.cli._fleet import fleet_app

        status_cmd = _find_command(fleet_app, "status")
        assert status_cmd is not None, "fleet status command not found"

    def test_fleet_status_accepts_dry_run_flag(self) -> None:
        import inspect

        from autoskillit.cli._fleet import fleet_status

        sig = inspect.signature(fleet_status)
        assert "dry_run" in sig.parameters
        assert "reap" in sig.parameters

    def test_fleet_dispatch_command_registered(self) -> None:
        from autoskillit.cli._fleet import fleet_app

        assert "dispatch" in _subcommand_names(fleet_app)

    def test_fleet_campaign_command_registered(self) -> None:
        from autoskillit.cli._fleet import fleet_app

        assert "campaign" in _subcommand_names(fleet_app)

    def test_fleet_run_command_not_registered(self) -> None:
        from autoskillit.cli._fleet import fleet_app

        assert "run" not in _subcommand_names(fleet_app)


# ---------------------------------------------------------------------------
# T_GUARD_1: fleet_run exits 1 when fleet feature disabled
# ---------------------------------------------------------------------------


def test_fleet_dispatch_exits_when_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """fleet_dispatch exits 1 when fleet feature is disabled via feature gate."""
    monkeypatch.chdir(tmp_path)
    _stub_guards(monkeypatch)
    checked_features: list[str] = []
    monkeypatch.setattr(
        "autoskillit.cli._fleet.is_feature_enabled",
        lambda name, features: checked_features.append(name) or False,
    )
    monkeypatch.setattr(
        "autoskillit.config.load_config", lambda path: type("C", (), {"features": {}})()
    )
    with pytest.raises(SystemExit) as exc_info:
        _fleet_dispatch()
    assert exc_info.value.code == 1
    assert "fleet" in checked_features


def test_fleet_campaign_exits_when_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """fleet_campaign exits 1 when fleet feature is disabled via feature gate."""
    monkeypatch.chdir(tmp_path)
    _stub_guards(monkeypatch)
    checked_features: list[str] = []
    monkeypatch.setattr(
        "autoskillit.cli._fleet.is_feature_enabled",
        lambda name, features: checked_features.append(name) or False,
    )
    monkeypatch.setattr(
        "autoskillit.config.load_config", lambda path: type("C", (), {"features": {}})()
    )
    with pytest.raises(SystemExit) as exc_info:
        _fleet_campaign("any-campaign")
    assert exc_info.value.code == 1
    assert "fleet" in checked_features


# ---------------------------------------------------------------------------
# T_GUARD_2: fleet_list exits 1 when fleet feature disabled
# ---------------------------------------------------------------------------


def test_fleet_list_exits_when_disabled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """fleet_list exits 1 when fleet feature is disabled via feature gate."""
    monkeypatch.chdir(tmp_path)
    checked_features: list[str] = []
    monkeypatch.setattr(
        "autoskillit.cli._fleet.is_feature_enabled",
        lambda name, features: checked_features.append(name) or False,
    )
    monkeypatch.setattr(
        "autoskillit.config.load_config", lambda path: type("C", (), {"features": {}})()
    )
    with pytest.raises(SystemExit) as exc_info:
        _fleet_list()
    assert exc_info.value.code == 1
    assert "fleet" in checked_features


# ---------------------------------------------------------------------------
# T_GUARD_3: fleet_status exits 1 when fleet feature disabled
# ---------------------------------------------------------------------------


def test_fleet_status_exits_when_disabled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """fleet_status exits 1 when fleet feature is disabled via feature gate."""
    monkeypatch.chdir(tmp_path)
    checked_features: list[str] = []
    monkeypatch.setattr(
        "autoskillit.cli._fleet.is_feature_enabled",
        lambda name, features: checked_features.append(name) or False,
    )
    monkeypatch.setattr(
        "autoskillit.config.load_config", lambda path: type("C", (), {"features": {}})()
    )
    with pytest.raises(SystemExit) as exc_info:
        _fleet_status(None)
    assert exc_info.value.code == 1
    assert "fleet" in checked_features


# ---------------------------------------------------------------------------
# T_GUARD_4: fleet_run proceeds normally when fleet enabled
# ---------------------------------------------------------------------------


def test_fleet_dispatch_proceeds_when_enabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """fleet_run passes the feature guard and proceeds to campaign resolution."""
    _stub_guards(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("autoskillit.cli._fleet.is_feature_enabled", lambda name, features: True)
    monkeypatch.setattr(
        "autoskillit.config.load_config", lambda path: type("C", (), {"features": {}})()
    )
    with pytest.raises(SystemExit) as exc_info:
        _fleet_dispatch()
    assert exc_info.value.code == 1
    # Guard passed — exit must come from campaign resolution, not the feature gate
    captured = capsys.readouterr()
    assert "not enabled" not in captured.err


# ---------------------------------------------------------------------------
# T_ADHOC. Ad-hoc fleet dispatch mode (campaign_name=None)
# ---------------------------------------------------------------------------


def test_fleet_dispatch_sets_fleet_mode_dispatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """fleet_run(None) must launch an interactive session, not exit 1."""
    _stub_guards(monkeypatch)
    monkeypatch.chdir(tmp_path)
    captured = _capture_subprocess(monkeypatch)
    _fleet_dispatch()
    assert captured["env"].get("AUTOSKILLIT_FLEET_MODE") == "dispatch"


def test_fleet_dispatch_writes_no_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Ad-hoc fleet run must not create a state.json under .autoskillit/temp/fleet/."""
    _stub_guards(monkeypatch)
    monkeypatch.chdir(tmp_path)
    _capture_subprocess(monkeypatch)
    _fleet_dispatch()
    fleet_dir = tmp_path / ".autoskillit" / "temp" / "fleet"
    assert not fleet_dir.exists() or not any(fleet_dir.rglob("state.json"))


def test_fleet_dispatch_no_campaign_env_vars(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ad-hoc session must not set AUTOSKILLIT_CAMPAIGN_ID or AUTOSKILLIT_CAMPAIGN_STATE_PATH."""
    _stub_guards(monkeypatch)
    monkeypatch.chdir(tmp_path)
    captured = _capture_subprocess(monkeypatch)
    _fleet_dispatch()
    env = captured["env"]
    assert "AUTOSKILLIT_CAMPAIGN_ID" not in env
    assert "AUTOSKILLIT_CAMPAIGN_STATE_PATH" not in env


def test_build_fleet_open_prompt_instructs_open_kitchen() -> None:
    from autoskillit.cli._mcp_names import DIRECT_PREFIX
    from autoskillit.cli._prompts import _build_fleet_open_prompt

    prompt = _build_fleet_open_prompt(DIRECT_PREFIX)
    assert "open_kitchen" in prompt


def test_build_fleet_open_prompt_references_dispatch_tool() -> None:
    from autoskillit.cli._mcp_names import DIRECT_PREFIX
    from autoskillit.cli._prompts import _build_fleet_open_prompt

    prompt = _build_fleet_open_prompt(DIRECT_PREFIX)
    assert "dispatch_food_truck" in prompt


def test_build_fleet_open_prompt_no_campaign_manifest() -> None:
    from autoskillit.cli._mcp_names import DIRECT_PREFIX
    from autoskillit.cli._prompts import _build_fleet_open_prompt

    prompt = _build_fleet_open_prompt(DIRECT_PREFIX)
    assert "DISPATCH MANIFEST" not in prompt
    assert "CAMPAIGN OVERVIEW" not in prompt
    assert "CAMPAIGN DISCIPLINE" not in prompt


def test_build_fleet_open_prompt_accepts_marketplace_prefix() -> None:
    from autoskillit.cli._mcp_names import MARKETPLACE_PREFIX
    from autoskillit.cli._prompts import _build_fleet_open_prompt

    prompt = _build_fleet_open_prompt(MARKETPLACE_PREFIX)
    assert MARKETPLACE_PREFIX + "open_kitchen" in prompt
    assert MARKETPLACE_PREFIX + "dispatch_food_truck" in prompt


def test_fleet_dispatch_rejects_claudecode(monkeypatch: pytest.MonkeyPatch) -> None:
    """CLAUDECODE guard fires even when campaign_name is None."""
    monkeypatch.setenv("CLAUDECODE", "1")
    with pytest.raises(SystemExit, match="1"):
        _fleet_dispatch()
