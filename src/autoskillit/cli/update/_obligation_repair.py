"""Shared CLI-triggered repair helper for a pending publication obligation.

The single owner of attempt/defer/clear policy for both CLI trigger sites
(the update-failure handler in ``_update.py`` and ``cli/app.py``'s
``main()``). Server startup (``server/_lifespan.py``) uses the lower-level,
in-process ``repair_broken_plugin_cache_hooks`` primitive directly instead —
the server must not shell out, per its existing design — and never clears
the obligation itself: an in-process hook-artifact repair cannot perform the
full publication the obligation may demand, so clear-authority stays here,
with the code that actually verified it.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from autoskillit.core import (
    _AUTOSKILLIT_PLUGIN_KEY,
    DIRECT_INSTALL_CACHE_SUBDIR,
    Severity,
    get_logger,
)
from autoskillit.hook_registry import validate_plugin_cache_hooks
from autoskillit.workspace import (
    InstallStateLeaseMode,
    InstallStateSpec,
    clear_obligation,
    read_obligation,
    verify_installed_plugin_artifact,
)

__all__ = ["ObligationRepairResult", "attempt_obligation_repair"]

logger = get_logger(__name__)

_ProcessRunner = Callable[..., "subprocess.CompletedProcess[Any]"]


@dataclass(frozen=True, slots=True)
class ObligationRepairResult:
    """Outcome of one repair attempt against a pending obligation."""

    outcome: str
    """One of: "no_obligation", "deferred", "failed", "cleared"."""

    findings: tuple[str, ...] = ()


def attempt_obligation_repair(
    home: Path,
    *,
    environment: Mapping[str, str] | None = None,
    process_runner: _ProcessRunner | None = None,
) -> ObligationRepairResult:
    """Attempt to satisfy a pending publication obligation, or defer/report.

    No-op (``"no_obligation"``) when nothing is pending. Under
    ``CLAUDECODE`` a registered plugin cannot be safely replaced from inside
    a headless session (mirrors the same policy the update transaction
    itself applies pre-pivot) — defers with an instruction finding, leaving
    the obligation untouched. Otherwise spawns one
    ``autoskillit install --maintenance-update`` subprocess (the same child
    shape the update transaction's own ``INSTALL_CHILD_INVOCATION`` uses);
    on success, verifies health and ONLY THEN clears the obligation — the
    second and final clear-authority named in the journal's contract. On
    child failure or verification failure, reports and leaves the
    obligation pending.

    Verification branches on ``obligation.expected_version``: when it is a
    known string, verifies via token-aware ``validate_plugin_cache_hooks``
    (zero broken) plus ``verify_installed_plugin_artifact`` — whose
    ``InstallStateSpec.expected_version`` is a REQUIRED field, raising on
    empty. When it is ``None`` (the post-pivot probe never succeeded, or the
    backfill itself failed), that full spec can never be constructed
    safely — verifies instead via token-aware ``validate_plugin_cache_hooks``
    plus a fresh ``autoskillit --version`` subprocess succeeding.
    """
    env = environment if environment is not None else os.environ
    obligation = read_obligation(home)
    if obligation is None:
        return ObligationRepairResult(outcome="no_obligation")

    if env.get("CLAUDECODE"):
        return ObligationRepairResult(
            outcome="deferred",
            findings=(
                "Publication is owed but cannot be safely completed from "
                "inside CLAUDECODE. Run `autoskillit install` from an "
                "external terminal.",
            ),
        )

    runner = process_runner or subprocess.run
    install_result = runner(
        ["autoskillit", "install", "--maintenance-update"],
        check=False,
    )
    if install_result.returncode != 0:
        return ObligationRepairResult(
            outcome="failed",
            findings=(
                "autoskillit install --maintenance-update exited with "
                f"status {install_result.returncode}",
            ),
        )

    cache_dir = (
        home / ".claude" / "plugins" / "cache" / DIRECT_INSTALL_CACHE_SUBDIR / "autoskillit"
    )
    broken = validate_plugin_cache_hooks(cache_dir=cache_dir)
    if broken:
        return ObligationRepairResult(
            outcome="failed",
            findings=(f"{len(broken)} broken hook command(s) remain after repair install",),
        )

    if obligation.expected_version is not None:
        verification = verify_installed_plugin_artifact(
            InstallStateSpec(
                home=home,
                plugin_ref=_AUTOSKILLIT_PLUGIN_KEY,
                expected_version=obligation.expected_version,
                require_registered_plugin=True,
                lease_mode=InstallStateLeaseMode.SHARED,
            )
        )
        try:
            has_error = any(f.severity is Severity.ERROR for f in verification.findings)
            if has_error or verification.identity is None:
                return ObligationRepairResult(
                    outcome="failed",
                    findings=tuple(f"{f.check}: {f.message}" for f in verification.findings)
                    or ("Installed plugin verification returned no exact identity.",),
                )
        finally:
            if verification.lease is not None:
                verification.lease.close()
    else:
        version_check = runner(["autoskillit", "--version"], check=False)
        if version_check.returncode != 0:
            return ObligationRepairResult(
                outcome="failed",
                findings=(
                    "autoskillit --version failed after repair install "
                    "(expected version unknown — probe never succeeded "
                    "pre-repair)",
                ),
            )

    clear_obligation(home)
    return ObligationRepairResult(outcome="cleared")
