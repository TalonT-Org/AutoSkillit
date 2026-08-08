"""Typed install boundary shared by CLI callers and install implementations.

This module is intentionally a dependency leaf: it performs no I/O and imports
only from the standard library. It exposes typed requests/results and an argv
builder for the canonical ``autoskillit install --maintenance-update`` child
invocation — call sites that spawn the child subprocess must use
``MaintenanceInstallArgv.to_argv()`` to construct their argv.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum, StrEnum
from pathlib import Path
from typing import Literal

__all__ = [
    "InstallFailureKind",
    "InstallMode",
    "InstallOutcome",
    "InstallProcessStatus",
    "InstallRequest",
    "InstallResult",
    "MaintenanceInstallArgv",
    "process_status_for_result",
    "result_from_process_status",
]


class InstallMode(StrEnum):
    """The reason an install transaction was requested."""

    DIRECT = "direct"
    MAINTENANCE_UPDATE = "maintenance-update"


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


_MaintenanceArgvElement = Literal[
    "install",
    "--maintenance-update",
    "--expected-version",
    "--require-registered-plugin",
]


@dataclass(frozen=True, slots=True)
class MaintenanceInstallArgv:
    """Validated child argv for ``autoskillit install --maintenance-update``.

    Construction enforces ``mode=MAINTENANCE_UPDATE`` and a non-empty
    ``expected_version`` string. The ``.to_argv()`` method produces the
    canonical argv for the install --maintenance-update child process. Use
    this type for ALL subprocess self-invocation of the autoskillit install
    child with ``--maintenance-update``; hand-built argv literals bypass the
    type contract and were the root cause of issue #4485.
    """

    entrypoint: Path
    expected_version: str
    mode: InstallMode = InstallMode.MAINTENANCE_UPDATE

    def __post_init__(self) -> None:
        if not isinstance(self.entrypoint, Path):
            raise TypeError(f"entrypoint must be Path, got {type(self.entrypoint).__name__}")
        if not isinstance(self.expected_version, str) or not self.expected_version.strip():
            raise ValueError("maintenance update requires a non-empty expected_version string")
        if self.mode is not InstallMode.MAINTENANCE_UPDATE:
            raise ValueError("MaintenanceInstallArgv requires mode=MAINTENANCE_UPDATE")

    def to_argv(
        self,
        *,
        require_registered_plugin: bool = False,
    ) -> list[_MaintenanceArgvElement | str]:
        """Build the canonical child argv for ``install --maintenance-update``.

        ``require_registered_plugin`` controls whether the
        ``--require-registered-plugin`` flag is appended; it is False by
        default because the obligation-repair helper and the cross-interpreter
        smoke utility do not register plugins during their maintenance
        install. Pass True from the explicit update transaction.
        """
        argv: list[_MaintenanceArgvElement | str] = [
            str(self.entrypoint),
            "install",
            "--maintenance-update",
            "--expected-version",
            self.expected_version,
        ]
        if require_registered_plugin:
            argv.append("--require-registered-plugin")
        return argv


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
