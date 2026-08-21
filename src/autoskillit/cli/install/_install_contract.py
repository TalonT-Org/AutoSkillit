"""Typed install boundary shared by CLI callers and install implementations.

This module owns the CLI maintenance-child invocation boundary. Call sites that spawn the child
subprocess must use ``MaintenanceSubprocessInvocation.for_install()`` or
``.for_version_probe()`` to construct the complete argv + env + cwd + stdio
invocation — never hand-build any one of those four independently.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import IntEnum, StrEnum
from pathlib import Path

from autoskillit.core import (
    InstallMode,
    MaintenanceInstallArgv,
    build_maintenance_env,
    is_git_main_checkout,
    is_git_worktree,
)

__all__ = [
    "InstallFailureKind",
    "InstallMode",
    "InstallOutcome",
    "InstallProcessStatus",
    "InstallRequest",
    "InstallResult",
    "MAINTENANCE_EXTRAS",
    "MaintenanceInstallArgv",
    "MaintenanceSubprocessInvocation",
    "process_status_for_result",
    "result_from_process_status",
]

MAINTENANCE_EXTRAS: Mapping[str, str] = {
    "AUTOSKILLIT_SKIP_STALE_CHECK": "1",
    "AUTOSKILLIT_SKIP_UPDATE_CHECK": "1",
}


def _validated_maintenance_cwd(cwd: Path) -> Path:
    if not cwd.is_absolute():
        raise ValueError(f"Maintenance subprocess cwd must be absolute: {cwd}")
    if is_git_worktree(cwd) or is_git_main_checkout(cwd):
        raise ValueError(
            f"Refusing to build a maintenance subprocess invocation with cwd "
            f"inside a git repository: {cwd}"
        )
    return cwd


@dataclass(frozen=True, slots=True)
class MaintenanceSubprocessInvocation:
    """Complete maintenance-child argv, environment, cwd, and stdio policy."""

    argv: tuple[str, ...]
    env: Mapping[str, str]
    cwd: Path
    capture_output: bool = True

    @classmethod
    def for_version_probe(
        cls,
        entrypoint: Path,
        *,
        environment: Mapping[str, str],
        cwd: Path,
    ) -> MaintenanceSubprocessInvocation:
        return cls(
            argv=(str(entrypoint), "--version"),
            env=build_maintenance_env(environment, MAINTENANCE_EXTRAS),
            cwd=_validated_maintenance_cwd(cwd),
        )

    @classmethod
    def for_install(
        cls,
        entrypoint: Path,
        expected_version: str,
        *,
        environment: Mapping[str, str],
        cwd: Path,
        require_registered_plugin: bool = False,
    ) -> MaintenanceSubprocessInvocation:
        argv = MaintenanceInstallArgv(
            entrypoint=entrypoint,
            expected_version=expected_version,
        ).to_argv(require_registered_plugin=require_registered_plugin)
        return cls(
            argv=tuple(argv),
            env=build_maintenance_env(environment, MAINTENANCE_EXTRAS),
            cwd=_validated_maintenance_cwd(cwd),
        )


class InstallOutcome(StrEnum):
    """Semantic outcome of an install transaction."""

    COMPLETED = "completed"
    NOT_REQUIRED = "not-required"
    DECLINED = "declined"
    DEFERRED = "deferred"
    FAILED = "failed"
    RECOVERY_REQUIRED = "recovery-required"
    INDETERMINATE = "indeterminate"


class InstallFailureKind(StrEnum):
    """Failure stage for a failed or rollback-incomplete transaction."""

    PREFLIGHT = "preflight"
    CHILD = "child"
    POSTCONDITION = "postcondition"
    ROLLBACK = "rollback"


class InstallProcessStatus(IntEnum):
    """Stable process statuses for the public install boundary."""

    SUCCESS = 0
    DECLINED = 10
    DEFERRED = 11
    FAILED_PREFLIGHT = 20
    FAILED_CHILD = 21
    FAILED_POSTCONDITION = 22
    RECOVERY_REQUIRED = 23
    INDETERMINATE = 24


_FAILED_STATUS_BY_KIND: dict[InstallFailureKind, InstallProcessStatus] = {
    InstallFailureKind.PREFLIGHT: InstallProcessStatus.FAILED_PREFLIGHT,
    InstallFailureKind.CHILD: InstallProcessStatus.FAILED_CHILD,
    InstallFailureKind.POSTCONDITION: InstallProcessStatus.FAILED_POSTCONDITION,
}


@dataclass(frozen=True, slots=True)
class InstallRequest:
    """Install obligation supplied by a direct or maintenance caller."""

    scope: str
    mode: InstallMode
    require_registered_plugin: bool
    expected_version: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.scope, str):
            raise TypeError("scope must be a string")
        if not isinstance(self.mode, InstallMode):
            raise TypeError("mode must be an InstallMode")
        if type(self.require_registered_plugin) is not bool:
            raise TypeError("require_registered_plugin must be a boolean")
        if self.expected_version is not None and not isinstance(self.expected_version, str):
            raise TypeError("expected_version must be a string or None")


@dataclass(frozen=True, slots=True)
class InstallResult:
    """Immutable semantic result, including verification evidence."""

    outcome: InstallOutcome
    failure_kind: InstallFailureKind | None = None
    verified_identity: str | None = None
    findings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.outcome is InstallOutcome.FAILED:
            if self.failure_kind not in _FAILED_STATUS_BY_KIND:
                raise ValueError(
                    "FAILED requires a preflight, child, or postcondition failure kind"
                )
        elif self.outcome is InstallOutcome.RECOVERY_REQUIRED:
            if self.failure_kind is not InstallFailureKind.ROLLBACK:
                raise ValueError("RECOVERY_REQUIRED requires the rollback failure kind")
        elif self.failure_kind is not None:
            raise ValueError(f"{self.outcome.value} cannot carry a failure kind")

        if not isinstance(self.findings, tuple) or not all(
            isinstance(finding, str) for finding in self.findings
        ):
            raise TypeError("findings must be a tuple of strings")


_OUTCOME_BY_STATUS: dict[
    InstallProcessStatus,
    tuple[InstallOutcome, InstallFailureKind | None],
] = {
    InstallProcessStatus.DECLINED: (InstallOutcome.DECLINED, None),
    InstallProcessStatus.DEFERRED: (InstallOutcome.DEFERRED, None),
    **{
        status: (InstallOutcome.FAILED, failure_kind)
        for failure_kind, status in _FAILED_STATUS_BY_KIND.items()
    },
    InstallProcessStatus.RECOVERY_REQUIRED: (
        InstallOutcome.RECOVERY_REQUIRED,
        InstallFailureKind.ROLLBACK,
    ),
    InstallProcessStatus.INDETERMINATE: (InstallOutcome.INDETERMINATE, None),
}


def process_status_for_result(result: InstallResult) -> InstallProcessStatus:
    """Map a semantic install result to its stable process status."""

    if result.outcome in {InstallOutcome.COMPLETED, InstallOutcome.NOT_REQUIRED}:
        return InstallProcessStatus.SUCCESS
    if result.outcome is InstallOutcome.DECLINED:
        return InstallProcessStatus.DECLINED
    if result.outcome is InstallOutcome.DEFERRED:
        return InstallProcessStatus.DEFERRED
    if result.outcome is InstallOutcome.FAILED:
        assert result.failure_kind is not None
        return _FAILED_STATUS_BY_KIND[result.failure_kind]
    if result.outcome is InstallOutcome.RECOVERY_REQUIRED:
        return InstallProcessStatus.RECOVERY_REQUIRED
    return InstallProcessStatus.INDETERMINATE


def result_from_process_status(
    status: int,
    request: InstallRequest,
    *,
    verified_identity: str | None = None,
    findings: tuple[str, ...] = (),
) -> InstallResult:
    """Reconstruct a semantic result from a child process status and request."""

    try:
        process_status = InstallProcessStatus(status)
    except ValueError:
        outcome, failure_kind = InstallOutcome.INDETERMINATE, None
    else:
        if process_status is InstallProcessStatus.SUCCESS:
            if (
                request.mode is InstallMode.MAINTENANCE_UPDATE
                and not request.require_registered_plugin
            ):
                outcome = InstallOutcome.NOT_REQUIRED
            else:
                outcome = InstallOutcome.COMPLETED
            failure_kind = None
        else:
            outcome, failure_kind = _OUTCOME_BY_STATUS[process_status]

    return InstallResult(
        outcome=outcome,
        failure_kind=failure_kind,
        verified_identity=verified_identity,
        findings=findings,
    )
