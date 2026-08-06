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
import shutil
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from autoskillit.core import (
    _AUTOSKILLIT_PLUGIN_KEY,
    Severity,
    get_logger,
    installed_plugin_cache_dir,
)
from autoskillit.hook_registry import validate_plugin_cache_hooks
from autoskillit.workspace import (
    InstallStateLeaseMode,
    InstallStateSpec,
    clear_obligation,
    read_obligation,
    verify_installed_plugin_artifact,
)

__all__ = ["ObligationRepairOutcome", "ObligationRepairResult", "attempt_obligation_repair"]

logger = get_logger(__name__)

_ProcessRunner = Callable[..., "subprocess.CompletedProcess[Any]"]


def _resolve_repair_entrypoint(environment: Mapping[str, str]) -> Path | None:
    """Resolve the executable while the current interpreter is still valid."""
    from autoskillit.cli._install_info import detect_install

    entrypoint = detect_install().entrypoint
    if entrypoint is not None:
        return entrypoint
    resolved = shutil.which("autoskillit", path=environment.get("PATH"))
    return Path(resolved) if resolved is not None else None


class ObligationRepairOutcome(StrEnum):
    """Closed outcomes for one publication-obligation repair attempt."""

    NO_OBLIGATION = "no_obligation"
    DEFERRED = "deferred"
    FAILED = "failed"
    CLEARED = "cleared"


@dataclass(frozen=True, slots=True)
class ObligationRepairResult:
    """Outcome of one repair attempt against a pending obligation."""

    outcome: ObligationRepairOutcome

    findings: tuple[str, ...] = ()


def attempt_obligation_repair(
    home: Path,
    *,
    environment: Mapping[str, str] | None = None,
    process_runner: _ProcessRunner | None = None,
    entrypoint: Path | None = None,
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

    When the obligation lacks an expected version, a fresh version probe
    supplies one. Both branches then perform the same exact installed-state
    verification before compare-and-clearing the obligation.
    """
    env = environment if environment is not None else os.environ
    obligation = read_obligation(home)
    if obligation is None:
        return ObligationRepairResult(outcome=ObligationRepairOutcome.NO_OBLIGATION)

    if env.get("CLAUDECODE"):
        return ObligationRepairResult(
            outcome=ObligationRepairOutcome.DEFERRED,
            findings=(
                "Publication is owed but cannot be safely completed from "
                "inside CLAUDECODE. Run `autoskillit install` from an "
                "external terminal.",
            ),
        )

    runner = process_runner or subprocess.run
    repair_entrypoint = entrypoint or _resolve_repair_entrypoint(env)
    if repair_entrypoint is None:
        return ObligationRepairResult(
            outcome=ObligationRepairOutcome.FAILED,
            findings=("Could not resolve the autoskillit entrypoint for obligation repair.",),
        )
    child_env = dict(env)
    child_env["HOME"] = str(home)
    try:
        install_result = runner(
            [str(repair_entrypoint), "install", "--maintenance-update"],
            check=False,
            env=child_env,
        )
    except OSError as exc:
        return ObligationRepairResult(
            outcome=ObligationRepairOutcome.FAILED,
            findings=(f"Could not launch obligation repair install: {exc}",),
        )
    if install_result.returncode != 0:
        return ObligationRepairResult(
            outcome=ObligationRepairOutcome.FAILED,
            findings=(
                "autoskillit install --maintenance-update exited with "
                f"status {install_result.returncode}",
            ),
        )

    cache_dir = installed_plugin_cache_dir(home, "autoskillit")
    broken = validate_plugin_cache_hooks(cache_dir=cache_dir)
    if broken:
        return ObligationRepairResult(
            outcome=ObligationRepairOutcome.FAILED,
            findings=(f"{len(broken)} broken hook command(s) remain after repair install",),
        )

    expected_version = obligation.expected_version
    if expected_version is None:
        try:
            version_check = runner(
                [str(repair_entrypoint), "--version"],
                check=False,
                capture_output=True,
                env=child_env,
                text=True,
            )
        except OSError as exc:
            return ObligationRepairResult(
                outcome=ObligationRepairOutcome.FAILED,
                findings=(f"Could not launch autoskillit --version after repair: {exc}",),
            )
        if version_check.returncode != 0:
            return ObligationRepairResult(
                outcome=ObligationRepairOutcome.FAILED,
                findings=(
                    "autoskillit --version failed after repair install "
                    "(expected version unknown — probe never succeeded pre-repair)",
                ),
            )
        expected_version = (version_check.stdout or "").strip()
        if not expected_version:
            return ObligationRepairResult(
                outcome=ObligationRepairOutcome.FAILED,
                findings=("autoskillit --version produced no output after repair install",),
            )

    try:
        verification = verify_installed_plugin_artifact(
            InstallStateSpec(
                home=home,
                plugin_ref=_AUTOSKILLIT_PLUGIN_KEY,
                expected_version=expected_version,
                require_registered_plugin=True,
                lease_mode=InstallStateLeaseMode.SHARED,
            )
        )
    except Exception as exc:
        logger.warning("publication_obligation_verification_failed", exc_info=True)
        return ObligationRepairResult(
            outcome=ObligationRepairOutcome.FAILED,
            findings=(f"Installed plugin verification could not run: {exc}",),
        )
    try:
        has_error = any(f.severity is Severity.ERROR for f in verification.findings)
        if has_error or verification.identity is None:
            return ObligationRepairResult(
                outcome=ObligationRepairOutcome.FAILED,
                findings=tuple(f"{f.check}: {f.message}" for f in verification.findings)
                or ("Installed plugin verification returned no exact identity.",),
            )
        if not clear_obligation(home, expected=obligation):
            return ObligationRepairResult(
                outcome=ObligationRepairOutcome.FAILED,
                findings=("Publication succeeded but its obligation could not be cleared.",),
            )
        return ObligationRepairResult(outcome=ObligationRepairOutcome.CLEARED)
    finally:
        if verification.lease is not None:
            verification.lease.close()
