"""Update transaction phases, outcomes, and result construction."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import IntEnum, StrEnum
from types import MappingProxyType

from autoskillit.cli.install._install_contract import (
    InstallProcessStatus,
    InstallResult,
)


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
