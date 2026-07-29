"""Transport adapter from verified snapshots to shell-capture V2 fields."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from autoskillit.hooks._capture_contract import CaptureV2Fields
elif __package__ == "_capture":
    from _capture_contract import CaptureV2Fields
else:
    from .._capture_contract import CaptureV2Fields

_THIS_MODULE = sys.modules[__name__]
for _alias in ("_capture._snapshot_v2", "autoskillit.hooks._capture._snapshot_v2"):
    _existing = sys.modules.setdefault(_alias, _THIS_MODULE)
    if _existing is not _THIS_MODULE:
        raise RuntimeError("conflicting shell-capture snapshot V2 module identity")


class _OutcomeKind(Protocol):
    @property
    def value(self) -> str: ...


class _CommandOutcome(Protocol):
    @property
    def kind(self) -> _OutcomeKind: ...

    @property
    def value(self) -> int: ...

    @property
    def shell_returncode(self) -> int: ...


class _Manifest(Protocol):
    @property
    def capture_id(self) -> str: ...

    @property
    def finalized_at_revision(self) -> int: ...

    @property
    def total_bytes(self) -> int: ...

    @property
    def sha256(self) -> str: ...

    @property
    def command_outcome(self) -> _CommandOutcome: ...


class _Snapshot(Protocol):
    @property
    def manifest(self) -> _Manifest: ...


def capture_v2_fields(
    snapshot: _Snapshot,
    *,
    reference_status: str,
    reference: str | None,
    unavailable_reason: str | None,
) -> CaptureV2Fields:
    manifest = snapshot.manifest
    return CaptureV2Fields(
        capture_id=manifest.capture_id,
        finalized_at_revision=manifest.finalized_at_revision,
        total_bytes=manifest.total_bytes,
        sha256=manifest.sha256,
        command_outcome_kind=manifest.command_outcome.kind.value,
        command_outcome_value=manifest.command_outcome.value,
        shell_returncode=manifest.command_outcome.shell_returncode,
        reference_status=reference_status,
        reference=reference,
        unavailable_reason=unavailable_reason,
    )
