"""Repair and verify pending publication obligations.

``attempt_obligation_repair`` defers (``ObligationRepairOutcome.DEFERRED``)
whenever ``CLAUDECODE`` is set in the environment: it spawns an
``autoskillit install`` child process, and installation mutates global
install state (the installed-plugin cache root, the publication manifest,
hook registration) which is unsafe to mutate from inside a live session
that may itself be reading that state.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from packaging.version import InvalidVersion, Version

from autoskillit.cli.install._install_contract import MaintenanceSubprocessInvocation
from autoskillit.core import (
    _AUTOSKILLIT_INSTALL_ROOT_KEY,
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


def _installed_branch_identity_key(home: Path, version: str) -> str | None:
    """Read the published install-root generation's branch identity."""
    from autoskillit.core import (
        ReleaseChannel,
        ReleaseIdentity,
        parse_direct_url,
        resolve_current_generation,
    )

    # This must use the Python install-root store, not the disjoint projected-
    # plugin store verified later: only the install root contains direct_url.json.
    generation_root = resolve_current_generation(
        home,
        _AUTOSKILLIT_INSTALL_ROOT_KEY,
        version,
    )
    if generation_root is None:
        return None
    direct_url = parse_direct_url(generation_root)
    commit = direct_url["commit_id"]
    ref = direct_url["requested_revision"]
    if commit is None or ref is None:
        return None
    return ReleaseIdentity(
        ReleaseChannel.BRANCH,
        version=version,
        commit=commit,
        ref=ref,
    ).key()


def _resolve_repair_entrypoint(environment: Mapping[str, str]) -> Path | None:
    """Resolve the executable while the current interpreter is still valid."""
    from autoskillit.cli.install._install_info import (
        detect_install,
        resolve_autoskillit_entrypoint,
    )

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

    # A scratch, non-repo cwd for both maintenance children below — the same
    # hazard _transaction.py's is_git_worktree/is_git_main_checkout check
    # exists to prevent, applied here since this function's caller cwd may
    # be an arbitrary project directory (possibly a git worktree).
    maintenance_parent = home / ".autoskillit"
    try:
        maintenance_parent.mkdir(parents=True, exist_ok=True)
        working_dir = Path(tempfile.mkdtemp(prefix="obligation-repair-", dir=maintenance_parent))
    except OSError as exc:
        return ObligationRepairResult(
            outcome=ObligationRepairOutcome.FAILED,
            findings=(f"Could not create the obligation repair working directory: {exc}",),
        )

    try:
        # A maintenance install does not change the distribution version, so probe once.
        try:
            probe_invocation = MaintenanceSubprocessInvocation.for_version_probe(
                repair_entrypoint, environment=child_env, cwd=working_dir
            )
        except ValueError as exc:
            return ObligationRepairResult(
                outcome=ObligationRepairOutcome.FAILED,
                findings=(f"obligation_repair_invocation_invalid: {exc}",),
            )
        try:
            version_check = runner(
                list(probe_invocation.argv),
                check=False,
                env=probe_invocation.env,
                cwd=probe_invocation.cwd,
                capture_output=probe_invocation.capture_output,
                text=True,
            )
        except OSError as exc:
            logger.warning("obligation_repair_probe_failed", exc_info=True)
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

        # Prefer the exact branch identity when schema v2 and the published
        # install-root metadata make it available. Degraded records retain the
        # existing exact-version fallback used by the install child.
        persisted_version = _valid_version_or_unknown(obligation.expected_version)
        expected_identity_key = obligation.expected_identity_key
        observed_identity_key = (
            _installed_branch_identity_key(home, probed_version)
            if expected_identity_key is not None
            else None
        )
        expected_staleness_value = expected_identity_key
        observed_staleness_value = observed_identity_key
        if observed_staleness_value is None:
            expected_staleness_value = persisted_version
            observed_staleness_value = probed_version
        if (
            expected_staleness_value is not None
            and expected_staleness_value != observed_staleness_value
        ):
            return ObligationRepairResult(
                outcome=ObligationRepairOutcome.MISSING_EXPECTED_VERSION,
                findings=(
                    "obligation_stale: expected "
                    f"{expected_staleness_value}, observed {observed_staleness_value}",
                ),
            )

        # obligation is not None here (checked at function entry) — the sole
        # reason this repair runs is to complete a registered-plugin
        # publication obligation, so the install child must always ask for
        # republication, not silently fall back to InstallOutcome.NOT_REQUIRED.
        try:
            install_invocation = MaintenanceSubprocessInvocation.for_install(
                repair_entrypoint,
                probed_version,
                environment=child_env,
                cwd=working_dir,
                require_registered_plugin=True,
            )
        except ValueError as exc:
            return ObligationRepairResult(
                outcome=ObligationRepairOutcome.FAILED,
                findings=(f"obligation_repair_invocation_invalid: {exc}",),
            )

        try:
            install_result = runner(
                list(install_invocation.argv),
                check=False,
                env=install_invocation.env,
                cwd=install_invocation.cwd,
                capture_output=install_invocation.capture_output,
            )
        except OSError as exc:
            logger.warning("obligation_repair_install_launch_failed", exc_info=True)
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
    finally:
        shutil.rmtree(working_dir, ignore_errors=True)

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
