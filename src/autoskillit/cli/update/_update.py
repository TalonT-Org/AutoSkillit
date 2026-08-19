"""Presentation adapter for the explicit ``autoskillit update`` command."""

from __future__ import annotations

import subprocess
from pathlib import Path

from autoskillit.cli.ui._terminal import terminal_guard
from autoskillit.cli.update._restart import perform_restart
from autoskillit.cli.update._transaction import (
    UpdateProcessStatus,
    process_status_for_update_outcome,
    run_update_transaction,
)


def run_update_command(home: Path | None = None) -> None:
    """Upgrade autoskillit to the latest version on the install's branch.

    Only a fully completed update clears prompt state or restarts the process.
    Incomplete updates attempt to repair any pending publication obligation.
    """
    from autoskillit.cli.update._update_checks import (
        _read_dismiss_state,
        _write_dismiss_state,
        invalidate_fetch_cache,
    )

    _home = home or Path.home()
    with terminal_guard():
        result = run_update_transaction(
            home=_home,
            process_runner=subprocess.run,
        )

    process_status = process_status_for_update_outcome(result.outcome)
    if process_status is not UpdateProcessStatus.SUCCESS:
        for finding in result.findings:
            print(finding, flush=True)
        print(
            f"AutoSkillit update did not complete ({result.outcome.value}).",
            flush=True,
        )
        from autoskillit.cli.update._obligation_repair import (
            ObligationRepairOutcome,
            attempt_obligation_repair,
        )

        repair_result = attempt_obligation_repair(_home)
        if repair_result.outcome not in {
            ObligationRepairOutcome.NO_OBLIGATION,
            ObligationRepairOutcome.CLEARED,
        }:
            for finding in repair_result.findings:
                print(finding, flush=True)
        raise SystemExit(int(process_status))

    state = _read_dismiss_state(_home)
    state.pop("update_prompt", None)
    state.pop("binary_snoozed", None)
    _write_dismiss_state(_home, state)
    invalidate_fetch_cache(_home)
    print("AutoSkillit updated successfully.", flush=True)
    perform_restart()
