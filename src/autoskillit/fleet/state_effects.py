"""Fleet dispatch effect provenance authority.

Retry-relevant effect enums, immutable snapshots, and the request-scoped
tracker. Decomposed from ``state_types`` (#4856); new consumers should
import from this module rather than the legacy facade.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from threading import Lock
from typing import Any
from uuid import uuid4

from autoskillit.core import ProcessCleanupResult


class DispatchEffectName(StrEnum):
    """Stable vocabulary for retry-relevant fleet dispatch effects."""

    CAMPAIGN_PATH_CAPTURE = "campaign_path_capture"
    CALLER_IDENTITY = "caller_identity"
    DISPATCH_ALLOCATION = "dispatch_allocation"
    PRIOR_DISPATCH_BINDING = "prior_dispatch_binding"
    REQUESTED_RESUME_BINDING = "requested_resume_binding"
    EFFECTIVE_RESUME_BINDING = "effective_resume_binding"
    CHILD_DISCOVERY = "child_discovery"
    PROCESS_SPAWN = "process_spawn"
    COMMIT = "commit"
    CAMPAIGN_STATE_WRITE = "campaign_state_write"
    LOCAL_PROCESS_CLEANUP = "local_process_cleanup"
    STATE_CLEANUP = "state_cleanup"
    LABEL_CLEANUP = "label_cleanup"


class DispatchEffectPhase(StrEnum):
    """Lifecycle of one dispatch effect."""

    NOT_STARTED = "not_started"
    STARTED = "started"
    CONFIRMED = "confirmed"


class DispatchAggregatePhase(StrEnum):
    """Conservative aggregate of all retry-relevant dispatch effects."""

    NOT_STARTED = "not_started"
    STARTED = "started"
    COMMITTED = "committed"
    UNKNOWN = "unknown"


class DispatchRetryDisposition(StrEnum):
    """Safe caller action derived from effect provenance."""

    FRESH_DISPATCH_ALLOWED = "fresh_dispatch_allowed"
    RESUME_BY_IDENTITY = "resume_by_identity"
    RECONCILE_REQUIRED = "reconcile_required"


@dataclass(frozen=True, slots=True)
class DispatchEffectRecord:
    """Immutable checkpoint for one externally observable dispatch effect."""

    name: DispatchEffectName
    phase: DispatchEffectPhase
    effect_id: str
    retry_relevant: bool = True
    confirmation_receipt: str = ""
    known_downstream_identities: tuple[tuple[str, str], ...] = ()
    ambiguity: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name.value,
            "phase": self.phase.value,
            "effect_id": self.effect_id,
            "retry_relevant": self.retry_relevant,
            "confirmation_receipt": self.confirmation_receipt,
            "known_downstream_identities": dict(self.known_downstream_identities),
            "ambiguity": self.ambiguity,
        }


@dataclass(frozen=True, slots=True)
class DispatchEffectProvenance:
    """Immutable request snapshot used by domain, wire, and formatter layers."""

    operation_id: str
    effects: tuple[DispatchEffectRecord, ...] = ()
    cancel_requested: bool = False
    local_cleanup: ProcessCleanupResult | None = None
    state_cleanup_confirmed: bool = False
    labels_cleanup_confirmed: bool = False

    @property
    def aggregate_phase(self) -> DispatchAggregatePhase:
        relevant = tuple(effect for effect in self.effects if effect.retry_relevant)
        if any(
            effect.phase == DispatchEffectPhase.STARTED or effect.ambiguity for effect in relevant
        ):
            return DispatchAggregatePhase.UNKNOWN
        if any(
            effect.name == DispatchEffectName.COMMIT
            and effect.phase == DispatchEffectPhase.CONFIRMED
            for effect in relevant
        ):
            return DispatchAggregatePhase.COMMITTED
        if any(effect.phase == DispatchEffectPhase.CONFIRMED for effect in relevant):
            return DispatchAggregatePhase.STARTED
        return DispatchAggregatePhase.NOT_STARTED

    @property
    def retry_disposition(self) -> DispatchRetryDisposition:
        aggregate = self.aggregate_phase
        if aggregate == DispatchAggregatePhase.NOT_STARTED:
            return DispatchRetryDisposition.FRESH_DISPATCH_ALLOWED
        if aggregate == DispatchAggregatePhase.UNKNOWN:
            return DispatchRetryDisposition.RECONCILE_REQUIRED
        return DispatchRetryDisposition.RESUME_BY_IDENTITY

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "aggregate_phase": self.aggregate_phase.value,
            "retry_disposition": self.retry_disposition.value,
            "effects": [effect.to_dict() for effect in self.effects],
            "cancel_requested": self.cancel_requested,
            "local_cleanup": (
                self.local_cleanup.to_dict() if self.local_cleanup is not None else None
            ),
            "state_cleanup_confirmed": self.state_cleanup_confirmed,
            "labels_cleanup_confirmed": self.labels_cleanup_confirmed,
        }


class DispatchProvenanceTracker:
    """Request-scoped mutable journal that publishes immutable snapshots."""

    def __init__(self, operation_id: str | None = None) -> None:
        self.operation_id = operation_id or uuid4().hex
        self._effects: dict[DispatchEffectName, DispatchEffectRecord] = {}
        self._cancel_requested = False
        self._local_cleanup: ProcessCleanupResult | None = None
        self._state_cleanup_confirmed = False
        self._labels_cleanup_confirmed = False
        self._lock = Lock()

    def _effect_id(self, name: DispatchEffectName) -> str:
        return f"{self.operation_id}:{name.value}"

    @staticmethod
    def _identities(values: Mapping[str, object] | None) -> tuple[tuple[str, str], ...]:
        if not values:
            return ()
        return tuple(sorted((key, str(value)) for key, value in values.items() if value != ""))

    def start(
        self,
        name: DispatchEffectName,
        *,
        retry_relevant: bool = True,
        identities: dict[str, object] | None = None,
    ) -> None:
        with self._lock:
            existing = self._effects.get(name)
            if existing is not None:
                return
            self._effects[name] = DispatchEffectRecord(
                name=name,
                phase=DispatchEffectPhase.STARTED,
                effect_id=self._effect_id(name),
                retry_relevant=retry_relevant,
                known_downstream_identities=self._identities(identities),
            )

    def confirm(
        self,
        name: DispatchEffectName,
        *,
        receipt: str,
        retry_relevant: bool = True,
        identities: dict[str, object] | None = None,
    ) -> None:
        with self._lock:
            existing = self._effects.get(name)
            if existing is None:
                raise ValueError(f"dispatch effect {name.value!r} was not started")
            if existing.retry_relevant is not retry_relevant:
                raise ValueError(
                    f"dispatch effect {name.value!r} changed retry relevance after start"
                )
            merged_identities = dict(existing.known_downstream_identities)
            if identities:
                merged_identities.update(
                    {key: str(value) for key, value in identities.items() if value != ""}
                )
            self._effects[name] = DispatchEffectRecord(
                name=name,
                phase=DispatchEffectPhase.CONFIRMED,
                effect_id=existing.effect_id,
                retry_relevant=existing.retry_relevant,
                confirmation_receipt=receipt,
                known_downstream_identities=self._identities(merged_identities),
            )

    def mark_ambiguous(
        self,
        name: DispatchEffectName,
        *,
        evidence: str,
        identities: dict[str, object] | None = None,
    ) -> None:
        with self._lock:
            existing = self._effects.get(name)
            merged_identities = dict(
                existing.known_downstream_identities if existing is not None else ()
            )
            if identities:
                merged_identities.update(
                    {key: str(value) for key, value in identities.items() if value != ""}
                )
            self._effects[name] = DispatchEffectRecord(
                name=name,
                phase=DispatchEffectPhase.STARTED,
                effect_id=existing.effect_id if existing is not None else self._effect_id(name),
                retry_relevant=(existing.retry_relevant if existing is not None else True),
                known_downstream_identities=self._identities(merged_identities),
                ambiguity=evidence,
            )

    def request_cancel(self) -> None:
        with self._lock:
            self._cancel_requested = True

    def record_local_cleanup(self, result: ProcessCleanupResult) -> None:
        with self._lock:
            self._local_cleanup = result

    def record_state_cleanup(self, *, confirmed: bool) -> None:
        with self._lock:
            self._state_cleanup_confirmed = confirmed

    def record_labels_cleanup(self, *, confirmed: bool) -> None:
        with self._lock:
            self._labels_cleanup_confirmed = confirmed

    def snapshot(self) -> DispatchEffectProvenance:
        with self._lock:
            return DispatchEffectProvenance(
                operation_id=self.operation_id,
                effects=tuple(
                    self._effects[name]
                    for name in sorted(self._effects, key=lambda item: item.value)
                ),
                cancel_requested=self._cancel_requested,
                local_cleanup=self._local_cleanup,
                state_cleanup_confirmed=self._state_cleanup_confirmed,
                labels_cleanup_confirmed=self._labels_cleanup_confirmed,
            )


__all__ = [
    "DispatchAggregatePhase",
    "DispatchEffectName",
    "DispatchEffectPhase",
    "DispatchEffectProvenance",
    "DispatchEffectRecord",
    "DispatchProvenanceTracker",
    "DispatchRetryDisposition",
]
