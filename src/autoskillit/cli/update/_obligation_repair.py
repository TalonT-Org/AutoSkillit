"""Repair and verify pending publication obligations."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from packaging.version import InvalidVersion, Version

from autoskillit.cli._install_contract import MaintenanceInstallArgv
from autoskillit.core import (
    _AUTOSKILLIT_PLUGIN_KEY,
    get_logger,
    installed_plugin_cache_dir,
)
from autoskillit.hook_registry import validate_plugin_cache_hooks
from autoskillit.workspace import (
    clear_obligation,
    read_obligation,
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
    """Closed outcomes for one publication-obligation repair attempt.

    Members:
    - NO_OBLIGATION: no on-disk obligation journal; nothing to repair.
    - DEFERRED: deferral condition (e.g., CLAUDECODE); repair not attempted.
    - MISSING_EXPECTED_VERSION: pre-launch probe failed (no version from
      ``--version``) or the persisted obligation's expected_version is
      stale relative to the live distribution version; the install subprocess
      was never spawned. Callers must treat this as a repairable obligation
      that warrants a warning emission.
    - FAILED: an explicit failure (subprocess non-zero exit, OSError on
      spawn, broken-hook detection, identity mismatch, etc.).
    - CLEARED: obligation verified and cleared.
    """

    NO_OBLIGATION = "no_obligation"
    DEFERRED = "deferred"
    MISSING_EXPECTED_VERSION = "missing_expected_version"
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
    """Repair an obligation and clear it only after installed-state verification.

    The live version is probed before child launch and must match any valid
    persisted expectation. The maintenance install uses the typed argv contract.
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

    # A maintenance install does not change the distribution version, so probe once.
    try:
        version_check = runner(
            [str(repair_entrypoint), "--version"],
            check=False,
            env=child_env,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        return ObligationRepairResult(
            outcome=ObligationRepairOutcome.FAILED,
            findings=(f"obligation_repair_probe_failed: {exc}",),
        )
    if version_check.returncode != 0:
        return ObligationRepairResult(
            outcome=ObligationRepairOutcome.FAILED,
            findings=(
                "obligation_repair_probe_failed: --version returned "
                f"{version_check.returncode}: {version_check.stderr.strip()}",
            ),
        )
    probed_version = (version_check.stdout or "").strip()
    if not probed_version or _valid_version_or_unknown(probed_version) is None:
        return ObligationRepairResult(
            outcome=ObligationRepairOutcome.MISSING_EXPECTED_VERSION,
            findings=(f"obligation_repair_probe_unparseable: {probed_version!r}",),
        )

    # A stale persisted version would be rejected by the install child.
    persisted_version = _valid_version_or_unknown(obligation.expected_version)
    if persisted_version is not None and persisted_version != probed_version:
        return ObligationRepairResult(
            outcome=ObligationRepairOutcome.MISSING_EXPECTED_VERSION,
            findings=(
                f"obligation_stale: expected {persisted_version}, observed {probed_version}",
            ),
        )

    install_argv = MaintenanceInstallArgv(
        entrypoint=repair_entrypoint,
        expected_version=probed_version,
    ).to_argv()

    try:
        install_result = runner(
            install_argv,
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

    expected_version = probed_version
    try:
        from autoskillit.core import (
            installed_plugin_artifact_manifest_path,
            installed_plugin_semantic_key,
            read_installed_plugin_artifact_identity,
            resolve_current_generation,
        )

        gen_root = resolve_current_generation(home, _AUTOSKILLIT_PLUGIN_KEY, expected_version)
        if gen_root is None:
            return ObligationRepairResult(
                outcome=ObligationRepairOutcome.FAILED,
                findings=("No current generation found after install",),
            )
        read_installed_plugin_artifact_identity(
            gen_root,
            expected_semantic_key=installed_plugin_semantic_key(
                _AUTOSKILLIT_PLUGIN_KEY,
                expected_version,
            ),
            manifest_path=installed_plugin_artifact_manifest_path(gen_root),
        )
    except Exception as exc:
        logger.warning("publication_obligation_verification_failed", exc_info=True)
        return ObligationRepairResult(
            outcome=ObligationRepairOutcome.FAILED,
            findings=(f"Installed plugin verification could not run: {exc}",),
        )
    if not clear_obligation(home, expected=obligation):
        return ObligationRepairResult(
            outcome=ObligationRepairOutcome.FAILED,
            findings=("Publication succeeded but its obligation could not be cleared.",),
        )
    return ObligationRepairResult(outcome=ObligationRepairOutcome.CLEARED)
