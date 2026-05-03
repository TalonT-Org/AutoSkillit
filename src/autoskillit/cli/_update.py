"""First-class update command for autoskillit CLI.

Provides ``run_update_command()`` which is invoked by the ``autoskillit update``
subcommand.  Install-type-aware: uses the same ``upgrade_command()`` policy as
the startup update check so the two paths are never out of sync.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from autoskillit.cli._install_info import comparison_branch, detect_install, upgrade_command
from autoskillit.cli._restart import perform_restart
from autoskillit.cli.ui._terminal import terminal_guard


def run_update_command(home: Path | None = None) -> None:
    """Upgrade autoskillit to the latest version on the install's branch.

    Reads the install classification from ``direct_url.json``, runs the
    appropriate upgrade command, then runs ``autoskillit install`` to sync hooks
    and plugin state.  Clears any active dismissal state on success.
    """
    from autoskillit.cli._update_checks import (
        _fetch_latest_version,
        _read_dismiss_state,
        _verify_update_result,
        _write_dismiss_state,
        invalidate_fetch_cache,
    )

    _home = home or Path.home()
    _skip_env: dict[str, str] = {
        **os.environ,
        "AUTOSKILLIT_SKIP_STALE_CHECK": "1",
        "AUTOSKILLIT_SKIP_UPDATE_CHECK": "1",
        "AUTOSKILLIT_SKIP_SOURCE_DRIFT_CHECK": "1",
    }

    from autoskillit.core import any_kitchen_open

    if any_kitchen_open(project_path=str(Path.cwd())):
        print(
            "A kitchen is currently open for this project. "
            "Close it or wait for the pipeline to finish.",
        )
        raise SystemExit(1)

    info = detect_install()
    cmd = upgrade_command(info)
    if cmd is None:
        print(
            "Unknown install type. "
            "Reinstall via install.sh (stable) or 'task install-dev' (develop).",
            flush=True,
        )
        raise SystemExit(2)

    import autoskillit as _pkg

    current: str = getattr(_pkg, "__version__", "0.0.0")

    install_result: subprocess.CompletedProcess[bytes] = subprocess.CompletedProcess(
        args=["autoskillit", "install"], returncode=0
    )
    with terminal_guard():
        subprocess.run(cmd, check=False, env=_skip_env)
        install_result = subprocess.run(["autoskillit", "install"], check=False, env=_skip_env)
    if install_result.returncode != 0:
        print(
            "\nautoskillit install exited with an error. "
            "Hooks and plugin cache may be stale. "
            "Run 'autoskillit install' manually to fix.",
            flush=True,
        )

    state = _read_dismiss_state(_home)
    target_branch = comparison_branch(info)
    latest: str = (
        _fetch_latest_version(target_branch, _home) or current
        if target_branch is not None
        else current
    )
    succeeded = _verify_update_result(info, current, latest, _home, state)

    if succeeded:
        # Clear any active dismissal state so prompts are fresh
        state.pop("update_prompt", None)
        state.pop("binary_snoozed", None)
        _write_dismiss_state(_home, state)
        invalidate_fetch_cache(_home)
        print("AutoSkillit updated successfully.", flush=True)
        perform_restart()
