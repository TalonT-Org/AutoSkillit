"""Closed launch-control and managed headless lineage contracts.

This module is an IL-0 contract boundary.  It intentionally contains no
filesystem implementation; the concrete durable store lives in
``autoskillit.execution.session``.
"""

from __future__ import annotations

import re
import secrets
from collections.abc import MutableMapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol, runtime_checkable

__all__ = [
    "MANAGED_HEADLESS_SESSION_LINEAGE_SCHEMA_VERSION",
    "ManagedHeadlessSessionKind",
    "ManagedHeadlessSessionLineage",
    "ManagedHeadlessSessionLineageRef",
    "ManagedHeadlessSessionLineageStatus",
    "ManagedHeadlessSessionLineageStore",
    "ManagedHeadlessSessionTerminalState",
    "NativeShellCaptureDecision",
    "NativeShellCaptureMode",
    "NativeShellCaptureObservation",
    "NativeShellCaptureReason",
    "NativeShellCaptureStatus",
    "new_managed_attempt_id",
    "new_managed_launch_id",
    "pop_native_shell_capture_decision",
    "resolve_native_shell_capture_decision",
]


MANAGED_HEADLESS_SESSION_LINEAGE_SCHEMA_VERSION = 1
_IDENTITY_RE = re.compile(r"^[0-9a-f]{32}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_IDENTITY_TEXT = 512


class NativeShellCaptureMode(StrEnum):
    """Closed launch-requested shell I/O mode."""

    CAPTURE = "capture"
    DIRECT = "direct"


class NativeShellCaptureReason(StrEnum):
    """Closed reason vocabulary shared by launch and effective-mode diagnostics."""

    FRESH_DEFAULT = "fresh_default"
    EXPLICIT_ARGUMENT = "explicit_argument"
    ENVIRONMENT = "environment"
    RESUME_INHERITED = "resume_inherited"
    RESUME_OVERRIDE_REJECTED = "resume_override_rejected"
    INVALID_ENVIRONMENT = "invalid_environment"
    INVALID_LINEAGE = "invalid_lineage"
    CAPTURE_ENABLED = "capture_enabled"
    LAUNCH_AUTHORIZED_DIRECT = "launch_authorized_direct"
    PROJECT_POLICY_DISABLED = "project_policy_disabled"


class ManagedHeadlessSessionLineageStatus(StrEnum):
    """Closed status of lineage authority used to resolve a launch decision."""

    FRESH = "fresh"
    VALID = "valid"
    MISSING = "missing"
    CORRUPT = "corrupt"
    UNSUPPORTED = "unsupported"
    IDENTITY_MISMATCH = "identity_mismatch"
    LAUNCH_MISMATCH = "launch_mismatch"
    DISPATCH_MISMATCH = "dispatch_mismatch"
    NATIVE_SESSION_MISMATCH = "native_session_mismatch"
    OVERRIDE_REJECTED = "override_rejected"


# A concise public spelling for consumers that only handle capture diagnostics.
NativeShellCaptureStatus = ManagedHeadlessSessionLineageStatus


class ManagedHeadlessSessionKind(StrEnum):
    """Managed launch family recorded in durable lineage."""

    SKILL = "skill"
    FOOD_TRUCK = "food_truck"


class ManagedHeadlessSessionTerminalState(StrEnum):
    """Closed lifecycle state retained after managed execution."""

    ACTIVE = "active"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class NativeShellCaptureDecision:
    """Immutable launch-requested mode and its authority provenance."""

    mode: NativeShellCaptureMode
    reason: NativeShellCaptureReason
    lineage_status: ManagedHeadlessSessionLineageStatus

    def __post_init__(self) -> None:
        if not isinstance(self.mode, NativeShellCaptureMode):
            raise TypeError("mode must be a NativeShellCaptureMode")
        if not isinstance(self.reason, NativeShellCaptureReason):
            raise TypeError("reason must be a NativeShellCaptureReason")
        if not isinstance(self.lineage_status, ManagedHeadlessSessionLineageStatus):
            raise TypeError("lineage_status must be a ManagedHeadlessSessionLineageStatus")

    def to_dict(self) -> dict[str, str]:
        """Return the canonical JSON-compatible projection."""
        return {
            "mode": self.mode.value,
            "reason": self.reason.value,
            "lineage_status": self.lineage_status.value,
        }

    @classmethod
    def from_dict(cls, value: object) -> NativeShellCaptureDecision:
        """Parse the exact serialized decision shape."""
        if not isinstance(value, dict) or set(value) != {
            "mode",
            "reason",
            "lineage_status",
        }:
            raise ValueError("Invalid native shell capture decision")
        try:
            mode = NativeShellCaptureMode(_require_str(value["mode"], "mode"))
            reason = NativeShellCaptureReason(_require_str(value["reason"], "reason"))
            lineage_status = ManagedHeadlessSessionLineageStatus(
                _require_str(value["lineage_status"], "lineage_status")
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("Invalid native shell capture decision") from exc
        return cls(mode=mode, reason=reason, lineage_status=lineage_status)


@dataclass(frozen=True, slots=True)
class ManagedHeadlessSessionLineageRef:
    """Bounded protected reference to one pre-created managed lineage."""

    launch_id: str
    lineage_digest: str
    lineage_anchor: str
    anchor_device: int
    anchor_inode: int
    schema_version: int = MANAGED_HEADLESS_SESSION_LINEAGE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _validate_identity(self.launch_id, "launch_id")
        _validate_digest(self.lineage_digest, "lineage_digest")
        _validate_anchor(self.lineage_anchor)
        _validate_nonnegative_int(self.anchor_device, "anchor_device")
        _validate_nonnegative_int(self.anchor_inode, "anchor_inode")
        if self.schema_version != MANAGED_HEADLESS_SESSION_LINEAGE_SCHEMA_VERSION:
            raise ValueError("Unsupported managed lineage reference schema")

    def to_dict(self) -> dict[str, object]:
        """Return the exact JSON-compatible protected reference."""
        return {
            "schema_version": self.schema_version,
            "launch_id": self.launch_id,
            "lineage_digest": self.lineage_digest,
            "lineage_anchor": self.lineage_anchor,
            "anchor_device": self.anchor_device,
            "anchor_inode": self.anchor_inode,
        }

    @classmethod
    def from_dict(cls, value: object) -> ManagedHeadlessSessionLineageRef:
        """Parse an exact reference shape, rejecting bool-as-int values."""
        expected = {
            "schema_version",
            "launch_id",
            "lineage_digest",
            "lineage_anchor",
            "anchor_device",
            "anchor_inode",
        }
        if not isinstance(value, dict) or set(value) != expected:
            raise ValueError("Invalid managed lineage reference")
        try:
            return cls(
                schema_version=_require_int(value["schema_version"], "schema_version"),
                launch_id=_require_str(value["launch_id"], "launch_id"),
                lineage_digest=_require_str(value["lineage_digest"], "lineage_digest"),
                lineage_anchor=_require_str(value["lineage_anchor"], "lineage_anchor"),
                anchor_device=_require_int(value["anchor_device"], "anchor_device"),
                anchor_inode=_require_int(value["anchor_inode"], "anchor_inode"),
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("Invalid managed lineage reference") from exc


@dataclass(frozen=True, slots=True)
class NativeShellCaptureObservation:
    """One closed, idempotent effective-mode observation from the runner."""

    attempt_id: str
    effective_mode: NativeShellCaptureMode
    reason: NativeShellCaptureReason
    project_policy_disabled: bool = False

    def __post_init__(self) -> None:
        _validate_identity(self.attempt_id, "attempt_id")
        if not isinstance(self.effective_mode, NativeShellCaptureMode):
            raise TypeError("effective_mode must be a NativeShellCaptureMode")
        if not isinstance(self.reason, NativeShellCaptureReason):
            raise TypeError("reason must be a NativeShellCaptureReason")
        if not isinstance(self.project_policy_disabled, bool):
            raise TypeError("project_policy_disabled must be bool")

    def to_dict(self) -> dict[str, object]:
        """Return the exact JSON-compatible observation."""
        return {
            "attempt_id": self.attempt_id,
            "effective_mode": self.effective_mode.value,
            "reason": self.reason.value,
            "project_policy_disabled": self.project_policy_disabled,
        }

    @classmethod
    def from_dict(cls, value: object) -> NativeShellCaptureObservation:
        """Parse the exact serialized observation shape."""
        expected = {
            "attempt_id",
            "effective_mode",
            "reason",
            "project_policy_disabled",
        }
        if not isinstance(value, dict) or set(value) != expected:
            raise ValueError("Invalid native shell capture observation")
        policy_disabled = value["project_policy_disabled"]
        if not isinstance(policy_disabled, bool):
            raise ValueError("Invalid native shell capture observation")
        try:
            return cls(
                attempt_id=_require_str(value["attempt_id"], "attempt_id"),
                effective_mode=NativeShellCaptureMode(
                    _require_str(value["effective_mode"], "effective_mode")
                ),
                reason=NativeShellCaptureReason(_require_str(value["reason"], "reason")),
                project_policy_disabled=policy_disabled,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("Invalid native shell capture observation") from exc


@dataclass(frozen=True, slots=True)
class ManagedHeadlessSessionLineage:
    """Validated immutable value returned by the durable lineage store."""

    launch_id: str
    decision: NativeShellCaptureDecision
    backend: str
    session_kind: ManagedHeadlessSessionKind
    lineage_anchor: str
    anchor_device: int
    anchor_inode: int
    lineage_digest: str
    generation: int
    record_digest: str
    attempt_ids: tuple[str, ...] = ()
    candidate_native_session_ids: tuple[str, ...] = ()
    final_native_session_id: str | None = None
    dispatch_id: str | None = None
    terminal_state: ManagedHeadlessSessionTerminalState = (
        ManagedHeadlessSessionTerminalState.ACTIVE
    )
    observations: tuple[NativeShellCaptureObservation, ...] = ()
    dropped_observation_count: int = 0
    schema_version: int = MANAGED_HEADLESS_SESSION_LINEAGE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _validate_identity(self.launch_id, "launch_id")
        if not isinstance(self.decision, NativeShellCaptureDecision):
            raise TypeError("decision must be a NativeShellCaptureDecision")
        _validate_bounded_text(self.backend, "backend")
        if not isinstance(self.session_kind, ManagedHeadlessSessionKind):
            raise TypeError("session_kind must be a ManagedHeadlessSessionKind")
        _validate_anchor(self.lineage_anchor)
        _validate_nonnegative_int(self.anchor_device, "anchor_device")
        _validate_nonnegative_int(self.anchor_inode, "anchor_inode")
        _validate_digest(self.lineage_digest, "lineage_digest")
        _validate_nonnegative_int(self.generation, "generation")
        _validate_digest(self.record_digest, "record_digest")
        if len(set(self.attempt_ids)) != len(self.attempt_ids):
            raise ValueError("attempt_ids must be unique")
        for attempt_id in self.attempt_ids:
            _validate_identity(attempt_id, "attempt_id")
        _validate_unique_texts(
            self.candidate_native_session_ids,
            "candidate_native_session_ids",
        )
        if self.final_native_session_id is not None:
            _validate_bounded_text(
                self.final_native_session_id,
                "final_native_session_id",
            )
        if self.dispatch_id is not None:
            _validate_bounded_text(self.dispatch_id, "dispatch_id")
        if not isinstance(self.terminal_state, ManagedHeadlessSessionTerminalState):
            raise TypeError("terminal_state must be a ManagedHeadlessSessionTerminalState")
        if not all(isinstance(item, NativeShellCaptureObservation) for item in self.observations):
            raise TypeError("observations must contain NativeShellCaptureObservation values")
        if len(set(self.observations)) != len(self.observations):
            raise ValueError("observations must be unique")
        _validate_nonnegative_int(
            self.dropped_observation_count,
            "dropped_observation_count",
        )
        if self.schema_version != MANAGED_HEADLESS_SESSION_LINEAGE_SCHEMA_VERSION:
            raise ValueError("Unsupported managed lineage schema")

    @property
    def reference(self) -> ManagedHeadlessSessionLineageRef:
        """Return the stable reference transported to managed children."""
        return ManagedHeadlessSessionLineageRef(
            launch_id=self.launch_id,
            lineage_digest=self.lineage_digest,
            lineage_anchor=self.lineage_anchor,
            anchor_device=self.anchor_device,
            anchor_inode=self.anchor_inode,
        )


@runtime_checkable
class ManagedHeadlessSessionLineageStore(Protocol):
    """IL-0 persistence protocol implemented only at the execution composition edge."""

    def create(
        self,
        *,
        lineage_anchor: Path,
        launch_id: str,
        decision: NativeShellCaptureDecision,
        backend: str,
        session_kind: ManagedHeadlessSessionKind,
        dispatch_id: str | None = None,
    ) -> ManagedHeadlessSessionLineage: ...

    def load(
        self,
        *,
        lineage_anchor: Path,
        launch_id: str,
    ) -> ManagedHeadlessSessionLineage: ...

    def load_reference(
        self,
        reference: ManagedHeadlessSessionLineageRef,
    ) -> ManagedHeadlessSessionLineage: ...

    def find_by_final_native_session_id(
        self,
        *,
        lineage_anchor: Path,
        session_id: str,
    ) -> ManagedHeadlessSessionLineage: ...

    def find_by_dispatch_id(
        self,
        *,
        lineage_anchor: Path,
        dispatch_id: str,
    ) -> ManagedHeadlessSessionLineage: ...

    def append_attempt(
        self,
        *,
        lineage_anchor: Path,
        launch_id: str,
        attempt_id: str,
        expected_generation: int,
        expected_record_digest: str,
    ) -> ManagedHeadlessSessionLineage: ...

    def bind_candidate_native_session_id(
        self,
        *,
        lineage_anchor: Path,
        launch_id: str,
        session_id: str,
        expected_generation: int,
        expected_record_digest: str,
    ) -> ManagedHeadlessSessionLineage: ...

    def bind_final_native_session_id(
        self,
        *,
        lineage_anchor: Path,
        launch_id: str,
        session_id: str,
        expected_generation: int,
        expected_record_digest: str,
    ) -> ManagedHeadlessSessionLineage: ...

    def bind_dispatch_id(
        self,
        *,
        lineage_anchor: Path,
        launch_id: str,
        dispatch_id: str,
        expected_generation: int,
        expected_record_digest: str,
    ) -> ManagedHeadlessSessionLineage: ...

    def set_terminal_state(
        self,
        *,
        lineage_anchor: Path,
        launch_id: str,
        terminal_state: ManagedHeadlessSessionTerminalState,
        expected_generation: int,
        expected_record_digest: str,
    ) -> ManagedHeadlessSessionLineage: ...

    def record_observation(
        self,
        *,
        lineage_anchor: Path,
        launch_id: str,
        observation: NativeShellCaptureObservation,
        expected_generation: int,
        expected_record_digest: str,
    ) -> ManagedHeadlessSessionLineage: ...


def new_managed_launch_id() -> str:
    """Return a fresh bounded managed launch identity."""
    return secrets.token_hex(16)


def new_managed_attempt_id() -> str:
    """Return a fresh bounded physical-attempt identity."""
    return secrets.token_hex(16)


def resolve_native_shell_capture_decision(
    value: NativeShellCaptureMode | str | None,
) -> NativeShellCaptureDecision:
    """Resolve one fresh typed launch value; omission always defaults to capture."""
    if value is None:
        return NativeShellCaptureDecision(
            mode=NativeShellCaptureMode.CAPTURE,
            reason=NativeShellCaptureReason.FRESH_DEFAULT,
            lineage_status=ManagedHeadlessSessionLineageStatus.FRESH,
        )
    try:
        mode = (
            value if isinstance(value, NativeShellCaptureMode) else NativeShellCaptureMode(value)
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("native shell capture mode must be 'capture' or 'direct'") from exc
    return NativeShellCaptureDecision(
        mode=mode,
        reason=NativeShellCaptureReason.EXPLICIT_ARGUMENT,
        lineage_status=ManagedHeadlessSessionLineageStatus.FRESH,
    )


def pop_native_shell_capture_decision(
    environ: MutableMapping[str, str],
    *,
    env_var: str = "AUTOSKILLIT_NATIVE_SHELL_CAPTURE_MODE",
) -> NativeShellCaptureDecision:
    """Consume the one-shot fleet CLI environment value exactly once.

    Invalid input fails closed to capture and remains observable through the
    closed reason/status vocabulary.
    """
    raw = environ.pop(env_var, None)
    if raw is None:
        return resolve_native_shell_capture_decision(None)
    try:
        mode = NativeShellCaptureMode(raw)
    except (TypeError, ValueError):
        return NativeShellCaptureDecision(
            mode=NativeShellCaptureMode.CAPTURE,
            reason=NativeShellCaptureReason.INVALID_ENVIRONMENT,
            lineage_status=ManagedHeadlessSessionLineageStatus.CORRUPT,
        )
    return NativeShellCaptureDecision(
        mode=mode,
        reason=NativeShellCaptureReason.ENVIRONMENT,
        lineage_status=ManagedHeadlessSessionLineageStatus.FRESH,
    )


def _require_str(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be str")
    return value


def _require_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} must be int")
    return value


def _validate_nonnegative_int(value: object, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative int")


def _validate_identity(value: object, field_name: str) -> None:
    if not isinstance(value, str) or _IDENTITY_RE.fullmatch(value) is None:
        raise ValueError(f"Invalid {field_name}")


def _validate_digest(value: object, field_name: str) -> None:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"Invalid {field_name}")


def _validate_bounded_text(value: object, field_name: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or "\x00" in value
        or len(value.encode("utf-8")) > _MAX_IDENTITY_TEXT
    ):
        raise ValueError(f"Invalid {field_name}")


def _validate_unique_texts(values: tuple[str, ...], field_name: str) -> None:
    if not isinstance(values, tuple) or len(set(values)) != len(values):
        raise ValueError(f"{field_name} must be a unique tuple")
    for value in values:
        _validate_bounded_text(value, field_name)


def _validate_anchor(value: object) -> None:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError("Invalid lineage_anchor")
    if len(value.encode("utf-8")) > 4096 or not Path(value).is_absolute():
        raise ValueError("lineage_anchor must be a bounded absolute path")
