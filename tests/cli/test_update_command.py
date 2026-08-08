"""Tests for the explicit update presentation adapter."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.cli.update._transaction import (
    UpdateProcessStatus,
    UpdateTransactionOutcome,
    UpdateTransactionResult,
    process_status_for_update_outcome,
)

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
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "autoskillit", "update", "--help"],
        capture_output=True,
        text=True,
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


def test_missing_expected_version_prints_warning_at_update_command(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Pair to test_missing_expected_version_logs_warning_at_main: when
    attempt_obligation_repair returns MISSING_EXPECTED_VERSION, the
    _update.run_update_command caller emits the finding via print()
    (this caller uses print() instead of structured logger).

    Pins the second caller's exclusion-based outcome classification for
    MISSING_EXPECTED_VERSION at cli/update/_update.py:43-55.
    """
    from autoskillit.cli.update import _update
    from autoskillit.cli.update._obligation_repair import ObligationRepairOutcome
    from autoskillit.cli.update._transaction import UpdateTransactionResult

    _patch_result(
        monkeypatch,
        UpdateTransactionResult(
            outcome=UpdateTransactionOutcome.FAILED_UPGRADE,
            findings=("transaction failed",),
        ),
    )

    def fake_repair(_home: Path) -> object:
        from dataclasses import dataclass

        @dataclass(frozen=True, slots=True)
        class _Result:
            outcome: ObligationRepairOutcome
            findings: tuple[str, ...]

        return _Result(
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
