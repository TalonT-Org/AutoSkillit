"""Managed fixed-batch binding models and authorized result storage."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from pathlib import Path

from autoskillit.core import (
    ManagedWorkerPermit,
    SkillContractError,
    SkillSemanticAdaptationResult,
    WriteBehaviorSpec,
    read_versioned_json,
    write_canonical_versioned_json,
)
from autoskillit.hooks import OUTCOME_SUCCESS, LoadedSkillEntry, is_terminal_outcome
from autoskillit.server._misc import AgentSkillDocument
from autoskillit.server.tools.tools_execution._managed_leaf import (
    ManagedLeafAssignmentInput,
    ManagedLeafPreparedLaunch,
    ManagedLeafProjection,
    _canonical,
    _digest,
)


@dataclass(frozen=True, slots=True)
class ManagedLaunchBinding:
    """Immutable trusted launch facts resolved before a managed batch opens."""

    request_session_id: str
    managed_parent_id: str
    parent_session_id: str
    caller_key: str
    attestation_epoch: int
    recovery_ready: bool
    selected_source: LoadedSkillEntry

    def __post_init__(self) -> None:
        for name in (
            "request_session_id",
            "managed_parent_id",
            "parent_session_id",
            "caller_key",
        ):
            if not isinstance(getattr(self, name), str) or not getattr(self, name):
                raise SkillContractError(f"managed launch binding {name} must be non-empty")
        if type(self.attestation_epoch) is not int or self.attestation_epoch < 0:
            raise SkillContractError("managed launch binding attestation_epoch is invalid")
        if not isinstance(self.selected_source, LoadedSkillEntry):
            raise SkillContractError("managed launch binding requires selected source evidence")


@dataclass(frozen=True, slots=True)
class ManagedLeafLaunchResult:
    """Bounded terminal facts produced by the leaf launch adapter."""

    outcome: str = OUTCOME_SUCCESS
    backend_session_id: str = ""
    result_reference: str | None = None
    result_digest: str | None = None
    result_payload: object | None = None

    def __post_init__(self) -> None:
        if not is_terminal_outcome(self.outcome):
            raise ValueError(f"unsupported managed leaf terminal outcome {self.outcome!r}")


@dataclass(frozen=True, slots=True)
class ManagedFixedBatchLaunchBinding:
    """All server-resolved facts needed to run one complete fixed assignment set."""

    launch: ManagedLaunchBinding
    flag_dir: Path
    source_document: AgentSkillDocument
    adaptation: SkillSemanticAdaptationResult
    assignments: tuple[ManagedLeafAssignmentInput, ...]
    default_model: str
    write_behavior: WriteBehaviorSpec
    read_only: bool
    launch_leaf: ManagedLeafLauncher

    def __post_init__(self) -> None:
        if not self.launch.recovery_ready:
            raise SkillContractError("managed launch binding is blocked by recovery")
        if not isinstance(self.flag_dir, Path):
            raise SkillContractError("managed fixed batch requires a channel directory")
        if not isinstance(self.source_document, AgentSkillDocument):
            raise SkillContractError("managed fixed batch requires a projected source document")
        if not isinstance(self.adaptation, SkillSemanticAdaptationResult):
            raise SkillContractError("managed fixed batch requires a semantic adaptation result")
        if not self.assignments:
            raise SkillContractError("managed fixed batch requires at least one assignment")
        if not isinstance(self.default_model, str) or not self.default_model:
            raise SkillContractError("managed fixed batch default_model must be non-empty")


@dataclass(frozen=True, slots=True)
class ManagedFixedBatchResult:
    """Replay-safe batch identity and aggregate state returned by the supervisor."""

    batch_id: str
    wave_outcome: str
    replayed: bool
    result_reference: str | None = None
    result_digest: str | None = None


def _result_reference(batch_id: str, assignment_id: str) -> str:
    component = assignment_id or "aggregate"
    return f"{_RESULT_REFERENCE_PREFIX}{batch_id}-{component}"


def _write_fixed_batch_result(path: Path, payload: dict[str, object]) -> None:
    """Atomically register one immutable, relocatable result record."""
    write_canonical_versioned_json(
        path,
        payload,
        _RESULT_SCHEMA_VERSION,
        exclusive=True,
    )


class ManagedFixedBatchResultStore:
    """Persist opaque fixed-batch result bytes with scope-bound reads."""

    def __init__(self, state_root: Path) -> None:
        self._result_dir = state_root / "results"

    def publish(
        self,
        *,
        launch: ManagedLaunchBinding,
        batch_id: str,
        assignment_id: str,
        payload: object,
    ) -> tuple[str, str]:
        reference = _result_reference(batch_id, assignment_id)
        digest = _digest(payload)
        path = self._path(reference)
        record = {
            "result_reference": reference,
            "result_digest": digest,
            "request_session_id": launch.request_session_id,
            "managed_parent_id": launch.managed_parent_id,
            "source_artifact_digest": launch.selected_source.source_artifact_digest,
            "source_artifact_incarnation_id": (
                launch.selected_source.source_artifact_incarnation_id
            ),
            "batch_id": batch_id,
            "assignment_id": assignment_id,
            "payload": payload,
        }
        self._result_dir.mkdir(parents=True, exist_ok=True)
        try:
            _write_fixed_batch_result(path, record)
        except FileExistsError:
            existing = self._load(reference)
            if existing is None or _canonical(existing) != _canonical(
                {**record, "schema_version": _RESULT_SCHEMA_VERSION}
            ):
                raise SkillContractError("managed fixed-batch result reference conflicts")
        return reference, digest

    def read(
        self,
        *,
        reference: str,
        launch: ManagedLaunchBinding,
        batch_id: str,
        assignment_id: str,
    ) -> object:
        record = self._load(reference)
        if record is None:
            raise SkillContractError("managed fixed-batch result is unavailable")
        expected = {
            "result_reference": reference,
            "request_session_id": launch.request_session_id,
            "managed_parent_id": launch.managed_parent_id,
            "source_artifact_digest": launch.selected_source.source_artifact_digest,
            "source_artifact_incarnation_id": (
                launch.selected_source.source_artifact_incarnation_id
            ),
            "batch_id": batch_id,
            "assignment_id": assignment_id,
        }
        if any(record.get(key) != value for key, value in expected.items()):
            raise SkillContractError("managed fixed-batch result authorization failed")
        payload = record.get("payload")
        if _digest(payload) != record.get("result_digest"):
            raise SkillContractError("managed fixed-batch result digest is invalid")
        return payload

    def _load(self, reference: str) -> dict[str, object] | None:
        if not reference.startswith(_RESULT_REFERENCE_PREFIX):
            return None
        record = read_versioned_json(
            self._path(reference),
            _RESULT_SCHEMA_VERSION,
            raise_io_errors=True,
        )
        return record if isinstance(record, dict) else None

    def _path(self, reference: str) -> Path:
        return self._result_dir / f"{hashlib.sha256(reference.encode()).hexdigest()}.json"


_RESULT_SCHEMA_VERSION = 1


_RESULT_REFERENCE_PREFIX = "fixed-batch-result-"


ManagedLeafLauncher = Callable[
    [ManagedLeafProjection, ManagedWorkerPermit],
    AbstractAsyncContextManager[ManagedLeafPreparedLaunch[ManagedLeafLaunchResult]],
]
