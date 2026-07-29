"""Shell-capture lifecycle result and internal signal types."""

from __future__ import annotations

import sys
from dataclasses import dataclass

_THIS_MODULE = sys.modules[__name__]
for _alias in ("_capture._types", "autoskillit.hooks._capture._types"):
    _existing = sys.modules.setdefault(_alias, _THIS_MODULE)
    if _existing is not _THIS_MODULE:
        raise RuntimeError("conflicting shell-capture types module identity")

__all__ = ["CaptureCleanupOutcome"]


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
