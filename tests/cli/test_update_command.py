"""Tests for the explicit update presentation adapter."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.cli.update._transaction import (
    UpdateTransactionOutcome,
    UpdateTransactionResult,
)

pytestmark = [pytest.mark.layer("cli"), pytest.mark.medium]


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
    "outcome",
    [
        outcome
        for outcome in UpdateTransactionOutcome
        if outcome is not UpdateTransactionOutcome.COMPLETED
    ],
)
def test_explicit_noncompleted_outcomes_exit_nonzero_without_success_effects(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    outcome: UpdateTransactionOutcome,
) -> None:
    from autoskillit.cli.update._update import run_update_command

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

    with pytest.raises(SystemExit) as exc_info:
        run_update_command(home=tmp_path)

    assert exc_info.value.code != 0
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
