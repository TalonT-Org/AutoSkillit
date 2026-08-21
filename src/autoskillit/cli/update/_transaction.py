"""Success-gated update transaction shared by explicit and automatic callers."""

from __future__ import annotations

import importlib.metadata
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from enum import IntEnum, StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any, assert_never

from packaging.version import Version

from autoskillit.cli.install._install_contract import (
    MAINTENANCE_EXTRAS,
    InstallFailureKind,
    InstallMode,
    InstallOutcome,
    InstallProcessStatus,
    InstallRequest,
    InstallResult,
    MaintenanceSubprocessInvocation,
    result_from_process_status,
)
from autoskillit.cli.install._install_info import (
    InstallInfo,
    detect_install,
    resolve_autoskillit_entrypoint,
    upgrade_command,
)
from autoskillit.cli.install._installed_plugins import InstalledPluginsFile
from autoskillit.core import (
    _AUTOSKILLIT_INSTALL_ROOT_KEY,
    _AUTOSKILLIT_PLUGIN_KEY,
    InfrastructureFaultError,
    _installed_plugins_path,
    _InstallLock,
    build_maintenance_env,
    generation_artifact_root,
    generation_staging_root,
    get_logger,
    installed_plugin_semantic_key,
    is_git_main_checkout,
    is_git_worktree,
    new_plugin_artifact_incarnation_id,
    write_entrypoint_shim,
)
from autoskillit.workspace import (
    PublicationObligation,
    clear_obligation,
    publish_install_root_generation,
    update_obligation_expected_version,
    write_obligation,
)

__all__ = [
    "IRREVERSIBLE_PIVOT_PHASE",
    "UPDATE_TRANSACTION_PHASES",
    "UpdateProcessStatus",
    "UpdateTransactionOutcome",
    "UpdateTransactionPhase",
    "UpdateTransactionResult",
    "process_status_for_update_outcome",
    "run_update_transaction",
]

logger = get_logger(__name__)

_ProcessRunner = Callable[..., subprocess.CompletedProcess[Any]]
_VersionReader = Callable[[str], str]
_VersionProber = Callable[[InstallInfo, Mapping[str, str], _ProcessRunner], str]


def _install_root_entrypoint(root: Path) -> Path:
    if sys.platform == "win32":
        return root / "autoskillit" / "Scripts" / "autoskillit.exe"
    return root / "autoskillit" / "bin" / "autoskillit"


class UpdateTransactionPhase(StrEnum):
    """Ordered coordinator phases, independent of caller presentation policy."""

    CALLER_ENV_CAPTURE = "caller-env-capture"
    PRE_UPDATE_EVIDENCE_CAPTURE = "pre-update-evidence-capture"
    PLUGIN_OBLIGATION_DERIVATION = "plugin-obligation-derivation"
    SAFETY_CAPABILITY_PREFLIGHT = "safety-capability-preflight"
    MAINTENANCE_CONTEXT_CONSTRUCTION = "maintenance-context-construction"
    UPGRADE_SUBPROCESS_GATE = "upgrade-subprocess-gate"
    IRREVERSIBLE_PIVOT = "irreversible-pivot"
    FRESH_VERSION_METADATA_GATE = "fresh-version-metadata-gate"
    INSTALL_ROOT_GENERATION_PUBLICATION = "install-root-generation-publication"
    INSTALL_CHILD_INVOCATION = "install-child-invocation"
    INSTALL_STATUS_RECONSTRUCTION = "install-status-reconstruction"
    POST_UPDATE_ARTIFACT_VERIFICATION = "post-update-artifact-verification"
    RESULT_FINALIZATION = "result-finalization"


UPDATE_TRANSACTION_PHASES: tuple[UpdateTransactionPhase, ...] = tuple(UpdateTransactionPhase)
IRREVERSIBLE_PIVOT_PHASE = UpdateTransactionPhase.IRREVERSIBLE_PIVOT


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


class UpdateProcessStatus(IntEnum):
    """Stable public statuses for the explicit update process boundary."""

    SUCCESS = int(InstallProcessStatus.SUCCESS)
    DECLINED = int(InstallProcessStatus.DECLINED)
    DEFERRED = int(InstallProcessStatus.DEFERRED)
    FAILED_UPGRADE = int(InstallProcessStatus.FAILED_PREFLIGHT)
    FAILED_INSTALL = int(InstallProcessStatus.FAILED_CHILD)
    FAILED_POSTCONDITION = int(InstallProcessStatus.FAILED_POSTCONDITION)
    RECOVERY_REQUIRED = int(InstallProcessStatus.RECOVERY_REQUIRED)
    INDETERMINATE = int(InstallProcessStatus.INDETERMINATE)


_PROCESS_STATUS_BY_OUTCOME: Mapping[UpdateTransactionOutcome, UpdateProcessStatus] = (
    MappingProxyType(
        {
            UpdateTransactionOutcome.COMPLETED: UpdateProcessStatus.SUCCESS,
            UpdateTransactionOutcome.DECLINED: UpdateProcessStatus.DECLINED,
            UpdateTransactionOutcome.DEFERRED: UpdateProcessStatus.DEFERRED,
            UpdateTransactionOutcome.FAILED_UPGRADE: UpdateProcessStatus.FAILED_UPGRADE,
            UpdateTransactionOutcome.FAILED_INSTALL: UpdateProcessStatus.FAILED_INSTALL,
            UpdateTransactionOutcome.FAILED_POSTCONDITION: (
                UpdateProcessStatus.FAILED_POSTCONDITION
            ),
            UpdateTransactionOutcome.RECOVERY_REQUIRED: (UpdateProcessStatus.RECOVERY_REQUIRED),
            UpdateTransactionOutcome.INDETERMINATE: UpdateProcessStatus.INDETERMINATE,
        }
    )
)


def process_status_for_update_outcome(
    outcome: UpdateTransactionOutcome,
) -> UpdateProcessStatus:
    """Return the stable explicit-update process status for ``outcome``."""

    return _PROCESS_STATUS_BY_OUTCOME[outcome]


@dataclass(frozen=True, slots=True)
class UpdateTransactionResult:
    """Immutable update result and the evidence needed to present it."""

    outcome: UpdateTransactionOutcome
    expected_version: str | None = None
    install_result: InstallResult | None = None
    verified_identity: str | None = None
    findings: tuple[str, ...] = ()
    phase_history: tuple[UpdateTransactionPhase, ...] = ()
    irreversible_pivot_crossed: bool = False


class _TransactionProgress:
    """Enforce the phase prefix and the single terminal finalization transition."""

    __slots__ = ("_history", "_pivot_crossed")

    def __init__(self) -> None:
        self._history: list[UpdateTransactionPhase] = []
        self._pivot_crossed = False

    def enter(self, phase: UpdateTransactionPhase) -> None:
        if phase is UpdateTransactionPhase.RESULT_FINALIZATION:
            if self._history and self._history[-1] is phase:
                raise RuntimeError("Update transaction was finalized more than once")
        else:
            expected = UPDATE_TRANSACTION_PHASES[len(self._history)]
            if phase is not expected:
                raise RuntimeError(
                    f"Invalid update phase transition: expected {expected}, observed {phase}"
                )
        self._history.append(phase)
        if phase is IRREVERSIBLE_PIVOT_PHASE:
            self._pivot_crossed = True

    def finish(
        self,
        outcome: UpdateTransactionOutcome,
        *,
        expected_version: str | None = None,
        install_result: InstallResult | None = None,
        verified_identity: str | None = None,
        findings: tuple[str, ...] = (),
    ) -> UpdateTransactionResult:
        self.enter(UpdateTransactionPhase.RESULT_FINALIZATION)
        return UpdateTransactionResult(
            outcome=outcome,
            expected_version=expected_version,
            install_result=install_result,
            verified_identity=verified_identity,
            findings=findings,
            phase_history=tuple(self._history),
            irreversible_pivot_crossed=self._pivot_crossed,
        )


def _upgrade_failure(
    progress: _TransactionProgress,
    message: str,
) -> UpdateTransactionResult:
    return progress.finish(
        UpdateTransactionOutcome.FAILED_UPGRADE,
        findings=(message,),
    )


def _process_finding(stage: str, returncode: int) -> str:
    if returncode < 0:
        return f"{stage} was terminated by signal {-returncode}"
    return f"{stage} exited with status {returncode}"


def _report_post_pivot_failure(message: str) -> None:
    """Log a post-pivot failure without ever raising past this call.

    Must be called from within an active ``except:`` block — relies on
    ``sys.exc_info()`` via ``exc_info=True`` to attach the traceback. If the
    structured logger itself fails, emits one plain stderr line. Reporting
    never masks the original post-pivot failure.
    """
    try:
        logger.warning(message, exc_info=True)
    except Exception:
        try:
            sys.stderr.write(f"{message}\n")
        except Exception:
            pass


def _default_fresh_version_prober(
    info: InstallInfo,
    maintenance_env: Mapping[str, str],
    runner: _ProcessRunner,
    *,
    cwd: Path,
) -> str:
    """Read the post-pivot version from a newly launched CLI process."""
    entrypoint = resolve_autoskillit_entrypoint(
        info.entrypoint,
        search_path=maintenance_env.get("PATH"),
    )
    if entrypoint is None:
        raise RuntimeError(
            "Could not resolve an autoskillit entrypoint to probe the "
            "post-upgrade version (neither the pre-pivot ambient PATH nor "
            "the maintenance environment's PATH could locate one)."
        )
    invocation = MaintenanceSubprocessInvocation.for_version_probe(
        entrypoint, environment=maintenance_env, cwd=cwd
    )
    result = runner(
        list(invocation.argv),
        check=False,
        env=invocation.env,
        cwd=invocation.cwd,
        capture_output=invocation.capture_output,
        text=True,
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").strip() or "(no stderr)"
        raise RuntimeError(
            f"autoskillit --version exited with status {result.returncode}: {stderr}"
        )
    probed_version = (result.stdout or "").strip()
    if not probed_version:
        raise RuntimeError("autoskillit --version produced no output")
    return probed_version


def _resolve_fresh_version(
    *,
    info: InstallInfo,
    maintenance_env: Mapping[str, str],
    runner: _ProcessRunner,
    fresh_version_prober: _VersionProber | None,
    cwd: Path,
) -> str:
    """Resolve post-pivot version truth through the configured subprocess probe."""
    if fresh_version_prober is not None:
        return fresh_version_prober(info, maintenance_env, runner)
    return _default_fresh_version_prober(info, maintenance_env, runner, cwd=cwd)


def _map_install_result(
    progress: _TransactionProgress,
    install_result: InstallResult,
    *,
    expected_version: str,
    extra_findings: tuple[str, ...] = (),
) -> UpdateTransactionResult | None:
    findings = install_result.findings + extra_findings
    install_outcome = install_result.outcome
    if install_outcome is InstallOutcome.COMPLETED:
        return None
    if install_outcome is InstallOutcome.NOT_REQUIRED:
        return None
    if install_outcome is InstallOutcome.DECLINED:
        outcome = UpdateTransactionOutcome.DECLINED
    elif install_outcome is InstallOutcome.DEFERRED:
        outcome = UpdateTransactionOutcome.DEFERRED
    elif install_outcome is InstallOutcome.FAILED:
        outcome = (
            UpdateTransactionOutcome.FAILED_POSTCONDITION
            if install_result.failure_kind is InstallFailureKind.POSTCONDITION
            else UpdateTransactionOutcome.FAILED_INSTALL
        )
    elif install_outcome is InstallOutcome.RECOVERY_REQUIRED:
        outcome = UpdateTransactionOutcome.RECOVERY_REQUIRED
    elif install_outcome is InstallOutcome.INDETERMINATE:
        outcome = UpdateTransactionOutcome.INDETERMINATE
    else:
        assert_never(install_outcome)
    return progress.finish(
        outcome,
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
    fresh_version_prober: _VersionProber | None = None,
) -> UpdateTransactionResult:
    """Upgrade, run a fresh maintenance install, and verify every obligation.

    All pre-update evidence is captured before the first mutation.  In
    particular, an existing Claude plugin registration creates an immutable
    post-update publication obligation even when the caller's ambient backend
    is Codex.

    ``version_reader`` is consulted for the PRE-pivot read only —
    ``fresh_version_prober`` is the sole sanctioned source of post-pivot
    version truth (defaulting to ``_default_fresh_version_prober``: an
    out-of-process subprocess probe). In-process metadata reads are a
    pre-pivot-only API in production, because the parent's own import
    machinery is invalid past the pivot by construction.
    """

    progress = _TransactionProgress()
    progress.enter(UpdateTransactionPhase.CALLER_ENV_CAPTURE)
    resolved_home = (home or Path.home()).expanduser().absolute()
    runner = process_runner or subprocess.run
    read_version = version_reader or importlib.metadata.version
    environment = MappingProxyType(dict(os.environ if base_env is None else base_env))

    progress.enter(UpdateTransactionPhase.PRE_UPDATE_EVIDENCE_CAPTURE)
    try:
        current_version = read_version("autoskillit")
    except Exception as exc:
        logger.warning("update_preflight_metadata_failed", exc_info=True)
        return _upgrade_failure(
            progress,
            f"Could not read pre-update autoskillit metadata: {exc}",
        )

    registry = InstalledPluginsFile(_installed_plugins_path(resolved_home))
    registration_snapshot = registry.contains(_AUTOSKILLIT_PLUGIN_KEY)

    progress.enter(UpdateTransactionPhase.PLUGIN_OBLIGATION_DERIVATION)
    require_registered_plugin = registration_snapshot
    request = InstallRequest(
        scope="user",
        mode=InstallMode.MAINTENANCE_UPDATE,
        require_registered_plugin=require_registered_plugin,
        expected_version=None,
    )

    progress.enter(UpdateTransactionPhase.SAFETY_CAPABILITY_PREFLIGHT)
    info = detect_install()
    install_root_incarnation_id = new_plugin_artifact_incarnation_id()
    install_root_staging = (
        generation_staging_root(resolved_home, _AUTOSKILLIT_INSTALL_ROOT_KEY)
        / install_root_incarnation_id
    )
    command = upgrade_command(info, install_root_destination=install_root_staging)
    if command is None:
        return _upgrade_failure(
            progress,
            "Unknown install type. Reinstall via install.sh (stable) or "
            "'task install-dev' (develop).",
        )
    if environment.get("CLAUDECODE") and require_registered_plugin:
        install_result = InstallResult(outcome=InstallOutcome.DEFERRED)
        return progress.finish(
            UpdateTransactionOutcome.DEFERRED,
            install_result=install_result,
            findings=(
                "Update deferred because a registered Claude plugin cannot be "
                "safely replaced from inside CLAUDECODE.",
            ),
        )

    progress.enter(UpdateTransactionPhase.MAINTENANCE_CONTEXT_CONSTRUCTION)
    try:
        maintenance_env = build_maintenance_env(environment, MAINTENANCE_EXTRAS)
    except (TypeError, ValueError) as exc:
        return _upgrade_failure(
            progress,
            f"Could not build the sealed update environment: {exc}",
        )

    maintenance_parent = resolved_home / ".autoskillit"
    try:
        maintenance_parent.mkdir(parents=True, exist_ok=True)
        working_dir = Path(tempfile.mkdtemp(prefix="update-maintenance-", dir=maintenance_parent))
    except OSError as exc:
        return _upgrade_failure(
            progress,
            f"Could not create the update working directory: {exc}",
        )

    try:
        if is_git_worktree(working_dir) or is_git_main_checkout(working_dir):
            return _upgrade_failure(
                progress,
                f"Refusing to run update maintenance inside a git repository: {working_dir}",
            )
        progress.enter(UpdateTransactionPhase.UPGRADE_SUBPROCESS_GATE)
        obligation: PublicationObligation | None = None
        if require_registered_plugin:
            # Written only here — every failure/deferral strictly before this
            # point mutates nothing and must leave no obligation; every
            # outcome at or after this point legitimately leaves one
            # pending. A write failure aborts before the irreversible
            # subprocess launches: nothing is yet mutated, so refusing to
            # proceed without the breadcrumb on disk is safe.
            try:
                obligation = write_obligation(
                    resolved_home,
                    previous_version=current_version,
                    originating_phase=UpdateTransactionPhase.UPGRADE_SUBPROCESS_GATE.value,
                )
            except Exception as exc:
                # Pre-pivot: the parent's own import machinery is still
                # intact here (nothing has been mutated yet), so a direct
                # logger call is safe — unlike the post-pivot except
                # handlers below, this one predates _report_post_pivot_failure's
                # crash-proofing concern entirely.
                logger.warning("update_obligation_write_failed", exc_info=True)
                return _upgrade_failure(
                    progress,
                    f"Could not record the publication obligation: {exc}",
                )
        if command.env:
            try:
                install_root_staging.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                return _upgrade_failure(
                    progress,
                    f"Could not create the install-root staging directory "
                    f"{install_root_staging}: {exc}",
                )
        try:
            upgrade_result = runner(
                list(command.argv),
                check=False,
                env={**maintenance_env, **command.env},
                cwd=working_dir,
            )
        except OSError as exc:
            return _upgrade_failure(
                progress,
                f"Could not start the upgrade command: {exc}",
            )
        if upgrade_result.returncode != 0:
            return _upgrade_failure(
                progress, _process_finding("autoskillit upgrade", upgrade_result.returncode)
            )

        progress.enter(UpdateTransactionPhase.IRREVERSIBLE_PIVOT)
        progress.enter(UpdateTransactionPhase.FRESH_VERSION_METADATA_GATE)
        try:
            # A retargeted install landed at install_root_staging, not at any
            # path the ambient PATH or the pre-pivot entrypoint resolves to —
            # probe that binary directly rather than the default ambient
            # resolution _default_fresh_version_prober would otherwise use.
            probe_info = (
                replace(
                    info,
                    entrypoint=_install_root_entrypoint(install_root_staging),
                )
                if command.env
                else info
            )
            expected_version = _resolve_fresh_version(
                info=probe_info,
                maintenance_env=maintenance_env,
                runner=runner,
                fresh_version_prober=fresh_version_prober,
                cwd=working_dir,
            )
            if Version(expected_version) <= Version(current_version):
                return _upgrade_failure(
                    progress,
                    "Upgrade completed without advancing autoskillit metadata "
                    f"beyond {current_version}; observed {expected_version}.",
                )
        except Exception as exc:
            _report_post_pivot_failure("update_post_upgrade_metadata_failed")
            return _upgrade_failure(
                progress,
                f"Could not verify post-upgrade autoskillit metadata: {exc}",
            )

        progress.enter(UpdateTransactionPhase.INSTALL_ROOT_GENERATION_PUBLICATION)
        if command.env:
            # Only the retargeted GIT_VCS dev track staged content via
            # UV_TOOL_DIR — the STABLE/LOCAL_EDITABLE branches upgraded the
            # shared uv-managed root in place and have nothing to finalize.
            #
            # install_root_staging is a disposable probe copy, not the
            # published artifact: uv/venv console scripts bake an absolute
            # shebang path at creation time, so a tree once written cannot be
            # relocated afterward. The install above ran only to learn
            # expected_version. A second, near-free install (uv's local
            # cache makes a repeat install of the same resolved commit a
            # cache hit — verified by spike) writes the real, permanent copy
            # directly at its final version+incarnation-keyed path.
            generation_root = generation_artifact_root(
                resolved_home,
                _AUTOSKILLIT_INSTALL_ROOT_KEY,
                expected_version,
                install_root_incarnation_id,
            )
            try:
                generation_root.parent.mkdir(parents=True, exist_ok=True)
                final_bin_dir = generation_root.parent / f".{generation_root.name}-bin"
                final_result = runner(
                    list(command.argv),
                    check=False,
                    env={
                        **maintenance_env,
                        "UV_TOOL_DIR": str(generation_root),
                        "UV_TOOL_BIN_DIR": str(final_bin_dir),
                    },
                    cwd=working_dir,
                )
            except OSError as exc:
                return _upgrade_failure(
                    progress,
                    f"Could not start the install-root generation install at "
                    f"{generation_root}: {exc}",
                )
            if final_result.returncode != 0:
                return _upgrade_failure(
                    progress,
                    _process_finding("install-root generation install", final_result.returncode),
                )
            try:
                with _InstallLock():
                    publish_install_root_generation(
                        home=resolved_home,
                        install_ref=_AUTOSKILLIT_INSTALL_ROOT_KEY,
                        version=expected_version,
                        semantic_key=installed_plugin_semantic_key(
                            _AUTOSKILLIT_INSTALL_ROOT_KEY,
                            expected_version,
                        ),
                        incarnation_id=install_root_incarnation_id,
                        generation_root=generation_root,
                    )
            except InfrastructureFaultError:
                raise
            except Exception as exc:
                _report_post_pivot_failure("update_install_root_generation_publish_failed")
                return _upgrade_failure(
                    progress,
                    f"Could not publish the install-root generation: {exc}",
                )
            try:
                write_entrypoint_shim(resolved_home)
            except Exception as exc:
                _report_post_pivot_failure("update_entrypoint_shim_write_failed")
                return _upgrade_failure(
                    progress,
                    f"Could not write the entrypoint shim: {exc}",
                )

        if require_registered_plugin:
            # Post-pivot journal touch; update_obligation_expected_version()
            # never raises — a failed backfill leaves expected_version None,
            # which downstream repair code already treats as "unknown".
            assert obligation is not None
            updated_obligation = update_obligation_expected_version(
                resolved_home,
                expected=obligation,
                expected_version=expected_version,
            )
            if updated_obligation is not None:
                obligation = updated_obligation

        request = InstallRequest(
            scope=request.scope,
            mode=request.mode,
            require_registered_plugin=request.require_registered_plugin,
            expected_version=expected_version,
        )
        install_invocation = MaintenanceSubprocessInvocation.for_install(
            Path("autoskillit"),
            expected_version,
            environment=maintenance_env,
            cwd=working_dir,
            require_registered_plugin=require_registered_plugin,
        )
        progress.enter(UpdateTransactionPhase.INSTALL_CHILD_INVOCATION)
        try:
            install_process = runner(
                list(install_invocation.argv),
                check=False,
                env=install_invocation.env,
                cwd=install_invocation.cwd,
                capture_output=install_invocation.capture_output,
            )
        except OSError as exc:
            _report_post_pivot_failure("update_install_child_launch_failed")
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

        progress.enter(UpdateTransactionPhase.INSTALL_STATUS_RECONSTRUCTION)
        mapped = _map_install_result(
            progress,
            install_result,
            expected_version=expected_version,
        )
        if mapped is not None:
            return mapped

        progress.enter(UpdateTransactionPhase.POST_UPDATE_ARTIFACT_VERIFICATION)
        if not require_registered_plugin:
            # This transaction created no obligation and therefore has no
            # authority to clear debt left by an earlier registered update.
            return progress.finish(
                UpdateTransactionOutcome.COMPLETED,
                expected_version=expected_version,
                install_result=install_result,
            )

        try:
            from autoskillit.core import (
                installed_plugin_artifact_manifest_path,
                read_installed_plugin_artifact_identity,
                resolve_current_generation,
            )

            gen_root = resolve_current_generation(
                resolved_home,
                _AUTOSKILLIT_PLUGIN_KEY,
                expected_version,
            )
            verified_identity: str | None
            verification_findings: tuple[str, ...]
            if gen_root is None:
                verified_identity = None
                verification_findings = ("No current generation found after install",)
            else:
                gen_identity = read_installed_plugin_artifact_identity(
                    gen_root,
                    expected_semantic_key=installed_plugin_semantic_key(
                        _AUTOSKILLIT_PLUGIN_KEY,
                        expected_version,
                    ),
                    manifest_path=installed_plugin_artifact_manifest_path(gen_root),
                )
                verified_identity = gen_identity.semantic_key
                verification_findings = ()
        except Exception as exc:
            _report_post_pivot_failure("update_artifact_verification_failed")
            return progress.finish(
                UpdateTransactionOutcome.FAILED_POSTCONDITION,
                expected_version=expected_version,
                install_result=install_result,
                findings=(f"Installed plugin verification failed: {exc}",),
            )

        has_error = bool(verification_findings)
        if has_error or verified_identity is None:
            if verified_identity is None and not verification_findings:
                verification_findings = (
                    "Installed plugin verification returned no exact identity.",
                )
            return progress.finish(
                UpdateTransactionOutcome.FAILED_POSTCONDITION,
                expected_version=expected_version,
                install_result=install_result,
                verified_identity=verified_identity,
                findings=install_result.findings + verification_findings,
            )
        assert obligation is not None
        if not clear_obligation(resolved_home, expected=obligation):
            return progress.finish(
                UpdateTransactionOutcome.FAILED_POSTCONDITION,
                expected_version=expected_version,
                install_result=install_result,
                verified_identity=verified_identity,
                findings=install_result.findings
                + verification_findings
                + ("Publication succeeded but its obligation could not be cleared.",),
            )
        return progress.finish(
            UpdateTransactionOutcome.COMPLETED,
            expected_version=expected_version,
            install_result=install_result,
            verified_identity=verified_identity,
            findings=install_result.findings + verification_findings,
        )
    finally:
        shutil.rmtree(working_dir, ignore_errors=True)
        # A leftover here means the disposable probe failed before cleanup.
        shutil.rmtree(install_root_staging, ignore_errors=True)
