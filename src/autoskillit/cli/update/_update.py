"""Presentation adapter for the explicit ``autoskillit update`` command."""

from __future__ import annotations

import subprocess
from pathlib import Path

from autoskillit.cli._restart import perform_restart
from autoskillit.cli.ui._terminal import terminal_guard
from autoskillit.cli.update._transaction import (
    UpdateTransactionOutcome,
    run_update_transaction,
)


def run_update_command(home: Path | None = None) -> None:
    """Upgrade autoskillit to the latest version on the install's branch.

    Only a fully completed update clears prompt state or restarts the process.
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

    if result.outcome is not UpdateTransactionOutcome.COMPLETED:
        for finding in result.findings:
            print(finding, flush=True)
        print(
            f"AutoSkillit update did not complete ({result.outcome.value}).",
            flush=True,
        )
        raise SystemExit(1)

    state = _read_dismiss_state(_home)
    state.pop("update_prompt", None)
    state.pop("binary_snoozed", None)
    _write_dismiss_state(_home, state)
    invalidate_fetch_cache(_home)
    print("AutoSkillit updated successfully.", flush=True)
    perform_restart()
