"""First-class update command for autoskillit CLI.

Provides ``run_update_command()`` which is invoked by the ``autoskillit update``
subcommand.  Install-type-aware: uses the same ``upgrade_command()`` policy as
the startup update check so the two paths are never out of sync.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def run_update_command(home: Path | None = None) -> None:
    """Upgrade autoskillit to the latest version on the install's branch.

    Reads the install classification from ``direct_url.json``, runs the
    appropriate upgrade command, then runs ``autoskillit install`` to sync hooks
    and plugin state.  Clears any active dismissal state on success.
    """
    from autoskillit.cli._install_info import InstallType, detect_install, upgrade_command
    from autoskillit.cli._terminal import terminal_guard
    from autoskillit.cli._update_checks import (
        _read_dismiss_state,
        _verify_update_result,
        _write_dismiss_state,
    )

    _home = home or Path.home()
    _skip_env: dict[str, str] = {
        **os.environ,
        "AUTOSKILLIT_SKIP_STALE_CHECK": "1",
        "AUTOSKILLIT_SKIP_SOURCE_DRIFT_CHECK": "1",
    }

    info = detect_install()
    cmd = upgrade_command(info)
    if cmd is None:
        print(
            "Unknown install type. "
            "Reinstall via install.sh (stable) or 'task install-dev' (integration).",
            flush=True,
        )
        raise SystemExit(2)

    import autoskillit as _pkg

    current: str = getattr(_pkg, "__version__", "0.0.0")

    with terminal_guard():
        subprocess.run(cmd, check=False, env=_skip_env)
        subprocess.run(["autoskillit", "install"], check=False, env=_skip_env)

    state = _read_dismiss_state(_home)
    succeeded = _verify_update_result(current, current, _home, state)

    if succeeded:
        # Clear any active dismissal state so prompts are fresh
        state.pop("update_prompt", None)
        state.pop("update_snoozed", None)
        _write_dismiss_state(_home, state)
        print("AutoSkillit updated successfully.", flush=True)
