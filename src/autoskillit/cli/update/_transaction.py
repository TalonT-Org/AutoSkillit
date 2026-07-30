"""Success-gated update transaction shared by explicit and automatic callers."""

from __future__ import annotations

import importlib.metadata
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from packaging.version import Version

from autoskillit.cli._install_contract import (
    InstallFailureKind,
    InstallMode,
    InstallOutcome,
    InstallProcessStatus,
    InstallRequest,
    InstallResult,
    result_from_process_status,
)
from autoskillit.cli._install_info import detect_install, upgrade_command
from autoskillit.cli._installed_plugins import InstalledPluginsFile
from autoskillit.core import (
    Severity,
    build_maintenance_env,
    get_logger,
    is_git_main_checkout,
    is_git_worktree,
)
from autoskillit.workspace import InstallStateSpec, verify_installed_plugin_artifact

__all__ = [
    "UpdateTransactionOutcome",
    "UpdateTransactionResult",
    "run_update_transaction",
]

_PLUGIN_REF = "autoskillit@autoskillit-local"
_MAINTENANCE_EXTRAS = {
    "AUTOSKILLIT_SKIP_STALE_CHECK": "1",
    "AUTOSKILLIT_SKIP_UPDATE_CHECK": "1",
}
logger = get_logger(__name__)

_ProcessRunner = Callable[..., subprocess.CompletedProcess[Any]]
_VersionReader = Callable[[str], str]


class UpdateTransactionOutcome(StrEnum):
    """Public semantic outcomes of the complete update saga."""

    COMPLETED = "completed"
    FAILED_UPGRADE = "failed-upgrade"
    FAILED_INSTALL = "failed-install"
    FAILED_POSTCONDITION = "failed-postcondition"
    DECLINED = "declined"
    DEFERRED = "deferred"
    RECOVERY_REQUIRED = "recovery-required"
    INDETERMINATE = "indeterminate"


@dataclass(frozen=True, slots=True)
class UpdateTransactionResult:
    """Immutable update result and the evidence needed to present it."""

    outcome: UpdateTransactionOutcome
    expected_version: str | None = None
    install_result: InstallResult | None = None
    verified_identity: str | None = None
    findings: tuple[str, ...] = ()


def _upgrade_failure(message: str) -> UpdateTransactionResult:
    return UpdateTransactionResult(
        outcome=UpdateTransactionOutcome.FAILED_UPGRADE,
        findings=(message,),
    )


def _process_finding(stage: str, returncode: int) -> str:
    if returncode < 0:
        return f"{stage} was terminated by signal {-returncode}"
    return f"{stage} exited with status {returncode}"


def _map_install_result(
    install_result: InstallResult,
    *,
    expected_version: str,
    extra_findings: tuple[str, ...] = (),
) -> UpdateTransactionResult | None:
    findings = install_result.findings + extra_findings
    if install_result.outcome in {InstallOutcome.COMPLETED, InstallOutcome.NOT_REQUIRED}:
        return None
    if install_result.outcome is InstallOutcome.DECLINED:
        outcome = UpdateTransactionOutcome.DECLINED
    elif install_result.outcome is InstallOutcome.DEFERRED:
        outcome = UpdateTransactionOutcome.DEFERRED
    elif install_result.outcome is InstallOutcome.FAILED:
        outcome = (
            UpdateTransactionOutcome.FAILED_POSTCONDITION
            if install_result.failure_kind is InstallFailureKind.POSTCONDITION
            else UpdateTransactionOutcome.FAILED_INSTALL
        )
    elif install_result.outcome is InstallOutcome.RECOVERY_REQUIRED:
        outcome = UpdateTransactionOutcome.RECOVERY_REQUIRED
    else:
        outcome = UpdateTransactionOutcome.INDETERMINATE
    return UpdateTransactionResult(
        outcome=outcome,
        expected_version=expected_version,
        install_result=install_result,
        verified_identity=install_result.verified_identity,
        findings=findings,
    )


def run_update_transaction(
    home: Path | None = None,
    *,
    process_runner: _ProcessRunner | None = None,
    base_env: Mapping[str, str] | None = None,
    version_reader: _VersionReader | None = None,
) -> UpdateTransactionResult:
    """Upgrade, run a fresh maintenance install, and verify every obligation.

    All pre-update evidence is captured before the first mutation.  In
    particular, an existing Claude plugin registration creates an immutable
    post-update publication obligation even when the caller's ambient backend
    is Codex.
    """

    resolved_home = (home or Path.home()).expanduser().absolute()
    runner = process_runner or subprocess.run
    read_version = version_reader or importlib.metadata.version
    environment = os.environ if base_env is None else base_env

    info = detect_install()
    command = upgrade_command(info)
    if command is None:
        return _upgrade_failure(
            "Unknown install type. Reinstall via install.sh (stable) or "
            "'task install-dev' (develop)."
        )
    try:
        current_version = read_version("autoskillit")
    except Exception as exc:
        logger.warning("update_preflight_metadata_failed", exc_info=True)
        return _upgrade_failure(f"Could not read pre-update autoskillit metadata: {exc}")

    registry = InstalledPluginsFile(
        resolved_home / ".claude" / "plugins" / "installed_plugins.json"
    )
    require_registered_plugin = registry.contains(_PLUGIN_REF)
    request = InstallRequest(
        scope="user",
        mode=InstallMode.MAINTENANCE_UPDATE,
        require_registered_plugin=require_registered_plugin,
        expected_version=None,
    )

    if environment.get("CLAUDECODE") and require_registered_plugin:
        install_result = InstallResult(outcome=InstallOutcome.DEFERRED)
        return UpdateTransactionResult(
            outcome=UpdateTransactionOutcome.DEFERRED,
            install_result=install_result,
            findings=(
                "Update deferred because a registered Claude plugin cannot be "
                "safely replaced from inside CLAUDECODE.",
            ),
        )

    try:
        maintenance_env = build_maintenance_env(environment, _MAINTENANCE_EXTRAS)
    except (TypeError, ValueError) as exc:
        return _upgrade_failure(f"Could not build the sealed update environment: {exc}")

    maintenance_parent = resolved_home / ".autoskillit"
    try:
        maintenance_parent.mkdir(parents=True, exist_ok=True)
        working_dir = Path(tempfile.mkdtemp(prefix="update-maintenance-", dir=maintenance_parent))
    except OSError as exc:
        return _upgrade_failure(f"Could not create the update working directory: {exc}")

    try:
        if is_git_worktree(working_dir) or is_git_main_checkout(working_dir):
            return _upgrade_failure(
                f"Refusing to run update maintenance inside a git repository: {working_dir}"
            )
        try:
            upgrade_result = runner(
                command,
                check=False,
                env=maintenance_env,
                cwd=working_dir,
            )
        except OSError as exc:
            return _upgrade_failure(f"Could not start the upgrade command: {exc}")
        if upgrade_result.returncode != 0:
            return _upgrade_failure(
                _process_finding("autoskillit upgrade", upgrade_result.returncode)
            )

        try:
            expected_version = read_version("autoskillit")
            if Version(expected_version) <= Version(current_version):
                return _upgrade_failure(
                    "Upgrade completed without advancing autoskillit metadata "
                    f"beyond {current_version}; observed {expected_version}."
                )
        except Exception as exc:
            logger.warning("update_post_upgrade_metadata_failed", exc_info=True)
            return _upgrade_failure(f"Could not verify post-upgrade autoskillit metadata: {exc}")

        request = InstallRequest(
            scope=request.scope,
            mode=request.mode,
            require_registered_plugin=request.require_registered_plugin,
            expected_version=expected_version,
        )
        install_command = [
            "autoskillit",
            "install",
            "--maintenance-update",
            "--expected-version",
            expected_version,
        ]
        if require_registered_plugin:
            install_command.append("--require-registered-plugin")
        try:
            install_process = runner(
                install_command,
                check=False,
                env=maintenance_env,
                cwd=working_dir,
            )
        except OSError as exc:
            install_result = InstallResult(
                outcome=InstallOutcome.FAILED,
                failure_kind=InstallFailureKind.CHILD,
                findings=(f"Could not start autoskillit install: {exc}",),
            )
        else:
            install_result = result_from_process_status(install_process.returncode, request)
            if install_process.returncode not in {status.value for status in InstallProcessStatus}:
                install_result = InstallResult(
                    outcome=install_result.outcome,
                    failure_kind=install_result.failure_kind,
                    verified_identity=install_result.verified_identity,
                    findings=(
                        _process_finding(
                            "autoskillit maintenance install",
                            install_process.returncode,
                        ),
                    ),
                )

        mapped = _map_install_result(
            install_result,
            expected_version=expected_version,
        )
        if mapped is not None:
            return mapped

        if not require_registered_plugin:
            return UpdateTransactionResult(
                outcome=UpdateTransactionOutcome.COMPLETED,
                expected_version=expected_version,
                install_result=install_result,
            )

        try:
            verification = verify_installed_plugin_artifact(
                InstallStateSpec(
                    home=resolved_home,
                    plugin_ref=_PLUGIN_REF,
                    expected_version=expected_version,
                    require_registered_plugin=True,
                    require_shared_lease=True,
                )
            )
        except Exception as exc:
            logger.warning("update_artifact_verification_failed", exc_info=True)
            return UpdateTransactionResult(
                outcome=UpdateTransactionOutcome.FAILED_POSTCONDITION,
                expected_version=expected_version,
                install_result=install_result,
                findings=(f"Installed plugin verification failed: {exc}",),
            )

        verified_identity = (
            verification.identity.semantic_key if verification.identity is not None else None
        )
        verification_findings = tuple(
            f"{finding.check}: {finding.message}" for finding in verification.findings
        )
        try:
            has_error = any(
                finding.severity is Severity.ERROR for finding in verification.findings
            )
            if has_error or verified_identity is None:
                if verified_identity is None and not verification_findings:
                    verification_findings = (
                        "Installed plugin verification returned no exact identity.",
                    )
                return UpdateTransactionResult(
                    outcome=UpdateTransactionOutcome.FAILED_POSTCONDITION,
                    expected_version=expected_version,
                    install_result=install_result,
                    verified_identity=verified_identity,
                    findings=install_result.findings + verification_findings,
                )
            return UpdateTransactionResult(
                outcome=UpdateTransactionOutcome.COMPLETED,
                expected_version=expected_version,
                install_result=install_result,
                verified_identity=verified_identity,
                findings=install_result.findings + verification_findings,
            )
        finally:
            if verification.lease is not None:
                verification.lease.close()
    finally:
        shutil.rmtree(working_dir, ignore_errors=True)
