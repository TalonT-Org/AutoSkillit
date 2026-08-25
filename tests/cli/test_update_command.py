"""Tests for the explicit update presentation adapter."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from autoskillit.cli.update._transaction import (
    UpdateProcessStatus,
    UpdateTransactionOutcome,
    UpdateTransactionResult,
    process_status_for_update_outcome,
)
from autoskillit.core import ReleaseChannel, ReleaseIdentity
from tests.conftest import production_interpreter_env

pytestmark = [pytest.mark.layer("cli"), pytest.mark.medium]

_EXIT_STATUS_CASES = (
    (UpdateTransactionOutcome.COMPLETED, 0),
    (UpdateTransactionOutcome.DECLINED, 10),
    (UpdateTransactionOutcome.DEFERRED, 11),
    (UpdateTransactionOutcome.FAILED_UPGRADE, 20),
    (UpdateTransactionOutcome.FAILED_INSTALL, 21),
    (UpdateTransactionOutcome.FAILED_POSTCONDITION, 22),
    (UpdateTransactionOutcome.RECOVERY_REQUIRED, 23),
    (UpdateTransactionOutcome.INDETERMINATE, 24),
)


class _TerminalGuard:
    def __enter__(self) -> _TerminalGuard:
        return self

    def __exit__(self, *args: object) -> None:
        return None


def _patch_result(
    monkeypatch: pytest.MonkeyPatch,
    result: UpdateTransactionResult,
) -> None:
    monkeypatch.setattr(
        "autoskillit.cli.update._update.run_update_transaction",
        lambda **kwargs: result,
    )
    monkeypatch.setattr(
        "autoskillit.cli.update._update.terminal_guard",
        _TerminalGuard,
    )


def test_update_subcommand_registered_in_help() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "autoskillit", "update", "--help"],
        capture_output=True,
        text=True,
        env=production_interpreter_env(),
    )
    assert result.returncode == 0, f"update --help failed: {result.stderr}"


@pytest.mark.parametrize(
    ("outcome", "expected_status"),
    _EXIT_STATUS_CASES,
)
def test_every_update_outcome_has_one_stable_public_status(
    outcome: UpdateTransactionOutcome,
    expected_status: int,
) -> None:
    status = process_status_for_update_outcome(outcome)

    assert isinstance(status, UpdateProcessStatus)
    assert int(status) == expected_status


def test_noncompleted_update_statuses_are_distinct_and_nonzero() -> None:
    noncompleted_statuses = [int(status) for _, status in _EXIT_STATUS_CASES[1:]]

    assert all(noncompleted_statuses)
    assert len(set(noncompleted_statuses)) == len(noncompleted_statuses)


@pytest.mark.parametrize(
    ("outcome", "expected_status"),
    _EXIT_STATUS_CASES[1:],
)
def test_registered_explicit_update_exits_with_exact_status_without_success_effects(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    outcome: UpdateTransactionOutcome,
    expected_status: int,
) -> None:
    from autoskillit.cli.app import app

    state_file = tmp_path / ".autoskillit" / "update_check.json"
    state_file.parent.mkdir(parents=True)
    original = '{"update_prompt": {"conditions": ["binary"]}}'
    state_file.write_text(original, encoding="utf-8")
    _patch_result(
        monkeypatch,
        UpdateTransactionResult(outcome=outcome, findings=("transaction failed",)),
    )
    effects: list[str] = []
    monkeypatch.setattr(
        "autoskillit.cli.update._update_checks._write_dismiss_state",
        lambda *_args: effects.append("write"),
    )
    monkeypatch.setattr(
        "autoskillit.cli.update._update_checks.invalidate_fetch_cache",
        lambda *_args: effects.append("invalidate"),
    )
    monkeypatch.setattr(
        "autoskillit.cli.update._update.perform_restart",
        lambda: effects.append("restart"),
    )
    monkeypatch.setattr(
        "autoskillit.cli.update._update.Path.home",
        lambda: tmp_path,
    )

    with pytest.raises(SystemExit) as exc_info:
        app(["update"])

    assert exc_info.value.code == expected_status
    assert effects == []
    assert state_file.read_text(encoding="utf-8") == original
    assert "updated successfully" not in capsys.readouterr().out


def test_explicit_completed_clears_state_invalidates_prints_and_restarts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from autoskillit.cli.update._update import run_update_command
    from autoskillit.cli.update._update_checks import (
        _read_dismiss_state,
        _write_dismiss_state,
    )

    _write_dismiss_state(
        tmp_path,
        {
            "update_prompt": {"conditions": ["binary"]},
            "binary_snoozed": True,
            "preserved": "value",
        },
    )
    _patch_result(
        monkeypatch,
        UpdateTransactionResult(
            outcome=UpdateTransactionOutcome.COMPLETED,
            expected_version="1.1.0",
        ),
    )
    effects: list[str] = []
    monkeypatch.setattr(
        "autoskillit.cli.update._update_checks.invalidate_fetch_cache",
        lambda *_args: effects.append("invalidate"),
    )
    monkeypatch.setattr(
        "autoskillit.cli.update._update.perform_restart",
        lambda: effects.append("restart"),
    )

    run_update_command(home=tmp_path)

    state = _read_dismiss_state(tmp_path)
    assert state == {"preserved": "value"}
    assert effects == ["invalidate", "restart"]
    assert "updated successfully" in capsys.readouterr().out


def test_explicit_passes_home_and_fresh_process_runner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from autoskillit.cli.update import _update

    captured: list[dict[str, object]] = []

    def transaction(**kwargs: object) -> UpdateTransactionResult:
        captured.append(kwargs)
        return UpdateTransactionResult(
            outcome=UpdateTransactionOutcome.FAILED_UPGRADE,
        )

    monkeypatch.setattr(
        _update,
        "run_update_transaction",
        transaction,
    )
    monkeypatch.setattr(_update, "terminal_guard", _TerminalGuard)

    with pytest.raises(SystemExit):
        _update.run_update_command(home=tmp_path)

    assert captured[0]["home"] == tmp_path
    assert captured[0]["process_runner"] is _update.subprocess.run


def test_explicit_update_runs_the_transaction_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A stale-TTY explicit update runs one transaction end to end (#4597, A-7)."""
    import select as _select_mod
    from unittest.mock import MagicMock

    from autoskillit.cli.app import main as app_main
    from autoskillit.cli.update import _update, _update_checks

    from ._update_checks_helpers import _make_stable_info

    # Simulate a stale interactive TTY install: both stdio streams report a
    # TTY, a "binary" staleness signal fires, and the operator answers "y".
    fake_stdin = MagicMock()
    fake_stdin.isatty.return_value = True
    fake_stdout = MagicMock()
    fake_stdout.isatty.return_value = True
    monkeypatch.setattr(sys, "stdin", fake_stdin)
    monkeypatch.setattr(sys, "stdout", fake_stdout)
    monkeypatch.setattr(
        _select_mod, "select", lambda rlist, wlist, xlist, timeout=None: (rlist, [], [])
    )
    monkeypatch.delenv("CI", raising=False)
    # CLAUDECODE / AUTOSKILLIT_SKIP_STALE_CHECK / AUTOSKILLIT_SKIP_UPDATE_CHECK
    # are already scrubbed unconditionally by the autouse _scrub_ambient_env
    # fixture in tests/conftest.py — no ad-hoc delenv needed for them here.

    monkeypatch.setattr(_update_checks, "detect_install", lambda: _make_stable_info())
    monkeypatch.setattr(
        _update_checks,
        "resolve_target_identity",
        lambda info, home: ReleaseIdentity(
            ReleaseChannel.RELEASED,
            version="9.9.9",
        ),
    )
    monkeypatch.setattr(
        _update_checks,
        "_binary_signal",
        lambda installed, target, available: _update_checks.Signal(
            "binary", "New release: 9.9.9 (you have 0.0.0)"
        ),
    )
    monkeypatch.setattr(_update_checks, "_hooks_signal", lambda settings_path: None)
    monkeypatch.setattr(
        _update_checks,
        "_source_drift_signal",
        lambda installed, target, available: None,
    )
    monkeypatch.setattr(
        _update_checks,
        "_claude_settings_path",
        lambda scope, **_kwargs: tmp_path / "settings.json",
    )
    monkeypatch.setattr("builtins.input", lambda _="": "y")

    printed: list[str] = []
    monkeypatch.setattr(
        "builtins.print", lambda *args, **kw: printed.append(" ".join(str(a) for a in args))
    )

    # The first call mirrors a real successful upgrade; any further call
    # mirrors what the real transaction returns when re-run against an
    # install that is already current -- exactly the corrupted second call
    # this test guards against.
    call_count = 0

    def counting_transaction(**kwargs: object) -> UpdateTransactionResult:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return UpdateTransactionResult(
                outcome=UpdateTransactionOutcome.COMPLETED,
                expected_version="1.1.0",
            )
        return UpdateTransactionResult(
            outcome=UpdateTransactionOutcome.FAILED_UPGRADE,
            findings=("install already at target version",),
        )

    # Both call sites resolve run_update_transaction independently; patch
    # each module's bound name so a reintroduced double-call is counted
    # regardless of which path it comes through.
    monkeypatch.setattr(_update, "run_update_transaction", counting_transaction)
    monkeypatch.setattr(_update_checks, "run_update_transaction", counting_transaction)
    monkeypatch.setattr(_update, "terminal_guard", _TerminalGuard)
    monkeypatch.setattr(_update_checks, "terminal_guard", _TerminalGuard)
    monkeypatch.setattr(_update, "perform_restart", lambda: None)
    monkeypatch.setattr(_update_checks, "perform_restart", lambda: None)

    monkeypatch.setattr(
        "autoskillit.cli._init_helpers.evict_direct_mcp_entry", lambda *a, **kw: None
    )
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(sys, "argv", ["autoskillit", "update"])

    # cyclopts always calls sys.exit(0) after a command completes normally;
    # only a nonzero code signals the failure path (the "already at target
    # version" FAILED_UPGRADE result a spurious second call would produce).
    try:
        app_main()
    except SystemExit as exc:
        if exc.code:
            pytest.fail(
                f"main() exited with SystemExit({exc.code}) instead of completing "
                f"cleanly — indicates run_update_transaction was invoked more than "
                f"once (call_count={call_count})"
            )

    assert call_count == 1, f"run_update_transaction invoked {call_count} time(s), expected 1"
    combined = " ".join(printed)
    assert "updated successfully" in combined
    assert "did not complete" not in combined


def test_missing_expected_version_prints_warning_at_update_command(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Pair to test_missing_expected_version_logs_warning_at_main: when
    attempt_obligation_repair returns MISSING_EXPECTED_VERSION, the
    _update.run_update_command caller emits the finding via print()
    (this caller uses print() instead of structured logger).

    Pins run_update_command's exclusion-based classification of
    MISSING_EXPECTED_VERSION.
    """
    from autoskillit.cli.update import _update
    from autoskillit.cli.update._obligation_repair import (
        ObligationRepairOutcome,
        ObligationRepairResult,
    )
    from autoskillit.cli.update._transaction import UpdateTransactionResult

    _patch_result(
        monkeypatch,
        UpdateTransactionResult(
            outcome=UpdateTransactionOutcome.FAILED_UPGRADE,
            findings=("transaction failed",),
        ),
    )

    def fake_repair(_home: Path) -> ObligationRepairResult:
        return ObligationRepairResult(
            outcome=ObligationRepairOutcome.MISSING_EXPECTED_VERSION,
            findings=("obligation_stale: expected 0.9.0, observed 1.1.0",),
        )

    monkeypatch.setattr(
        "autoskillit.cli.update._obligation_repair.attempt_obligation_repair",
        fake_repair,
    )

    with pytest.raises(SystemExit):
        _update.run_update_command(home=tmp_path)

    captured = capsys.readouterr()
    assert "obligation_stale: expected 0.9.0, observed 1.1.0" in captured.out
