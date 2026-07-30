"""Shell-capture lifecycle result and internal signal types."""

from __future__ import annotations

from dataclasses import dataclass

from . import _descriptor, _failure_policy
from ._module_identity import register_module_aliases

register_module_aliases(__name__)

__all__ = [
    "CaptureCleanupOutcome",
    "CaptureFailureEvidence",
    "LegacyCleanupOnly",
]


@dataclass(frozen=True, slots=True)
class CaptureCleanupOutcome:
    examined: int = 0
    deleted: int = 0
    deleted_bytes: int = 0
    carrier_lease_live: int = 0
    not_due: int = 0
    tampered: int = 0
    errors: int = 0
    retry_count: int = 0
    remaining_due: int = 0
    duration: float = 0.0


@dataclass(frozen=True, slots=True)
class CaptureFailureEvidence:
    stage: str
    detail: str
    settlement_returncode: int | None = None

    def __post_init__(self) -> None:
        if (
            not _failure_policy.valid_failure_stage(self.stage)
            or not _failure_policy.valid_failure_detail(self.detail)
            or (
                self.settlement_returncode is not None
                and (type(self.settlement_returncode) is not int)
            )
        ):
            raise _descriptor.CaptureAuthorityError("invalid capture failure evidence")


@dataclass(frozen=True, slots=True)
class LegacyCleanupOnly:
    """Bounded legacy deletion evidence that carries no snapshot authority."""

    observed_size: int

    def __post_init__(self) -> None:
        if type(self.observed_size) is not int or self.observed_size < 0:
            raise _descriptor.CaptureAuthorityError("invalid legacy cleanup observation")


@dataclass(frozen=True, slots=True)
class ObservedArtifact:
    fd: int
    identity: tuple[int, int]
    nlink: int
    size: int


class CarrierLeaseLive(Exception):
    pass


class LockContended(Exception):
    pass


class Tampered(Exception):
    pass
