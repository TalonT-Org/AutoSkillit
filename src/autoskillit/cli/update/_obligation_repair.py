"""CLI repair and verified clearing of pending publication obligations."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from packaging.version import InvalidVersion, Version

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
    from autoskillit.cli._install_info import detect_install, resolve_autoskillit_entrypoint

    return resolve_autoskillit_entrypoint(
        detect_install().entrypoint,
        search_path=environment.get("PATH"),
    )


def _valid_version_or_unknown(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        Version(value)
    except InvalidVersion:
        return None
    return value


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
    """Repair and verify a pending publication obligation before clearing it."""
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
    try:
        broken = validate_plugin_cache_hooks(cache_dir=cache_dir)
    except Exception as exc:
        logger.warning("publication_obligation_hook_validation_failed", exc_info=True)
        return ObligationRepairResult(
            outcome=ObligationRepairOutcome.FAILED,
            findings=(f"Installed hook validation could not run: {exc}",),
        )
    if broken:
        return ObligationRepairResult(
            outcome=ObligationRepairOutcome.FAILED,
            findings=(f"{len(broken)} broken hook command(s) remain after repair install",),
        )

    expected_version = _valid_version_or_unknown(obligation.expected_version)
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
        if not expected_version or _valid_version_or_unknown(expected_version) is None:
            return ObligationRepairResult(
                outcome=ObligationRepairOutcome.FAILED,
                findings=("autoskillit --version produced no valid version after repair install",),
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
