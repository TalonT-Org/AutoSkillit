"""Tests: fleet CLI status command."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from autoskillit.cli.fleet import fleet_status as _fleet_status
from tests.cli._fleet_helpers import (
    DispatchDescriptor,
    _make_state,
    _make_state_with_tokens,
    _mock_batch_delete,
    _setup_campaign_with_sessions,
    _setup_campaign_with_status,
    _setup_campaign_with_tokens,
    _setup_existing_campaign_state,
)

pytestmark = [pytest.mark.layer("cli"), pytest.mark.medium, pytest.mark.feature("fleet")]


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


def test_fleet_status_missing_campaign(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """fleet status exits 3 for unknown campaign_id."""
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit, match="3"):
        _fleet_status("nonexistent")


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


def test_status_numeric_columns_right_aligned() -> None:
    """Numeric column definitions use align='>'."""
    from autoskillit.cli.fleet._fleet_display import _STATUS_COLUMNS

    numeric_labels = {"ELAPSED", "INPUT", "OUTPUT", "CACHE_RD", "CACHE_WR"}
    for col in _STATUS_COLUMNS:
        if col.label in numeric_labels:
            assert col.align == ">", f"{col.label} should be right-aligned"


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


def test_exit_code_all_success() -> None:
    """_compute_exit_code returns 0 when all dispatches succeed or skip."""
    from autoskillit.cli.fleet import _compute_exit_code

    state = _make_state(statuses=["success", "skipped", "success"])
    assert _compute_exit_code(state) == 0


@pytest.mark.parametrize("bad_status", ["failure", "interrupted", "refused", "released"])
def test_exit_code_any_failure(bad_status: str) -> None:
    """_compute_exit_code returns 1 when any dispatch is in a failure-class status."""
    from autoskillit.cli.fleet import _compute_exit_code

    state = _make_state(statuses=["success", bad_status])
    assert _compute_exit_code(state) == 1


@pytest.mark.parametrize("progress_status", ["running", "pending"])
def test_exit_code_in_progress(progress_status: str) -> None:
    """_compute_exit_code returns 2 when any dispatch is in-progress and none failed."""
    from autoskillit.cli.fleet import _compute_exit_code

    state = _make_state(statuses=["success", progress_status])
    assert _compute_exit_code(state) == 2


def test_exit_code_3_on_missing_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """fleet status exits 3 when state file is absent."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".autoskillit" / "temp" / "fleet" / "cid01").mkdir(parents=True)
    with pytest.raises(SystemExit) as exc_info:
        _fleet_status("cid01")
    assert exc_info.value.code == 3


def test_cross_check_warns_on_divergence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """_cross_check_tokens emits a stderr warning when divergence exceeds 5%."""
    from autoskillit.cli.fleet import _aggregate_totals, _cross_check_tokens

    (tmp_path / "sessions.jsonl").write_text("")
    state = _make_state_with_tokens(input_total=10000)
    state_totals = _aggregate_totals(state)

    monkeypatch.setattr("autoskillit.execution.resolve_log_dir", lambda *a: tmp_path)
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


def test_watch_exits_on_terminal_campaign(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """--watch detects all-terminal campaign and exits with code 0."""
    _setup_campaign_with_status(tmp_path, "cid01", "test", status="success")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    with pytest.raises(SystemExit) as exc_info:
        _fleet_status("cid01", watch=True)
    assert exc_info.value.code == 0


def test_cleanup_per_dispatch_session(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """--cleanup calls cleanup_session for each dispatch l3_session_id."""
    _setup_campaign_with_sessions(
        tmp_path,
        "cid01",
        "test",
        dispatches=[
            DispatchDescriptor("d1", "success", "sid-1"),
            DispatchDescriptor("d2", "failure", "sid-2"),
        ],
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


def test_exit_code_failure_over_in_progress() -> None:
    """_compute_exit_code returns 1 (failure) when mixed with in-progress."""
    from autoskillit.cli.fleet import _compute_exit_code

    state = _make_state(statuses=["failure", "running"])
    assert _compute_exit_code(state) == 1


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
    assert "100" in out
    assert "50" in out


def test_fleet_status_exits_when_disabled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """fleet_status exits 1 when fleet feature is disabled via feature gate."""
    monkeypatch.chdir(tmp_path)
    checked_features: list[str] = []
    monkeypatch.setattr(
        "autoskillit.cli.fleet.is_feature_enabled",
        lambda name, features, *, experimental_enabled=False: (
            checked_features.append(name) or False
        ),
    )
    monkeypatch.setattr(
        "autoskillit.config.load_config",
        lambda path: type("C", (), {"features": {}, "experimental_enabled": False})(),
    )
    with pytest.raises(SystemExit) as exc_info:
        _fleet_status(None)
    assert exc_info.value.code == 1
    assert "fleet" in checked_features
