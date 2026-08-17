"""Crash-durable managed headless session lineage persistence."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from autoskillit.core import (
    ManagedHeadlessSessionKind,
    ManagedHeadlessSessionLineage,
    ManagedHeadlessSessionLineageRef,
    ManagedHeadlessSessionTerminalState,
    NativeShellCaptureDecision,
    NativeShellCaptureObservation,
)
from autoskillit.execution.session._managed_headless_session_lineage_codec import (
    canonical_json as _canonical_json,
)

__all__ = [
    "DefaultManagedHeadlessSessionLineageStore",
    "ManagedHeadlessSessionLineageCASMismatch",
    "ManagedHeadlessSessionLineageConflictError",
]


_NAMESPACE = Path(".autoskillit") / "managed-headless-session-lineage"
_RECORDS_DIR = "records"
_INDEXES_DIR = "indexes"
_RUNNER_OBSERVATIONS_DIR = "runner-observations"
_FINAL_NATIVE_INDEX = "final-native-session"
_DISPATCH_INDEX = "dispatch"
_LOCK_FILENAME = ".lock"
_MAX_RECORD_BYTES = 256 * 1024
_MAX_ATTEMPTS = 256
_MAX_CANDIDATE_SESSION_IDS = 64
_MAX_OBSERVATIONS = 64
_MAX_OBSERVATION_BYTES = 16 * 1024
_MAX_RUNNER_MARKER_BYTES = 8 * 1024
_MAX_RUNNER_MARKERS = 256


class ManagedHeadlessSessionLineageConflictError(RuntimeError):
    """A stable managed identity was reused for incompatible state."""


class ManagedHeadlessSessionLineageCASMismatch(RuntimeError):
    """A caller attempted to overwrite a newer lineage generation."""


class DefaultManagedHeadlessSessionLineageStore:
    """Descriptor-identity-bound filesystem store with flock-serialized CAS."""

    def create(
        self,
        *,
        lineage_anchor: Path,
        launch_id: str,
        decision: NativeShellCaptureDecision,
        backend: str,
        session_kind: ManagedHeadlessSessionKind,
        dispatch_id: str | None = None,
    ) -> ManagedHeadlessSessionLineage:
        """Create one logical lineage or replay an identical prior creation."""
        anchor, device, inode = _resolve_anchor(lineage_anchor)
        root = _prepare_root(anchor)
        record_path = _record_path(root, launch_id)
        candidate = _new_lineage(
            launch_id=launch_id,
            decision=decision,
            backend=backend,
            session_kind=session_kind,
            lineage_anchor=anchor,
            anchor_device=device,
            anchor_inode=inode,
            dispatch_id=dispatch_id,
        )
        with _store_lock(root, exclusive=True):
            if record_path.exists():
                current = _read_record(record_path)
                if _creation_projection(current) != _creation_projection(candidate):
                    raise ManagedHeadlessSessionLineageConflictError(
                        f"Managed launch ID already exists with different parameters: {launch_id}"
                    )
                return current
            if dispatch_id is not None:
                _assert_index_available(
                    root,
                    _DISPATCH_INDEX,
                    dispatch_id,
                    launch_id,
                )
            _write_record(record_path, candidate)
            if dispatch_id is not None:
                _write_index(root, _DISPATCH_INDEX, dispatch_id, launch_id)
            return candidate

    def load(
        self,
        *,
        lineage_anchor: Path,
        launch_id: str,
    ) -> ManagedHeadlessSessionLineage:
        """Load and verify one launch record from its exact anchor."""
        anchor, device, inode = _resolve_anchor(lineage_anchor)
        root = _prepare_root(anchor)
        with _store_lock(root, exclusive=False):
            lineage = _read_record(_record_path(root, launch_id))
        _validate_anchor_identity(lineage, anchor, device, inode)
        return lineage

    def load_reference(
        self,
        reference: ManagedHeadlessSessionLineageRef,
    ) -> ManagedHeadlessSessionLineage:
        """Resolve and verify a protected stable lineage reference."""
        if not isinstance(reference, ManagedHeadlessSessionLineageRef):
            raise TypeError("reference must be a ManagedHeadlessSessionLineageRef")
        lineage = self.load(
            lineage_anchor=Path(reference.lineage_anchor),
            launch_id=reference.launch_id,
        )
        if lineage.reference != reference:
            raise ValueError("Managed lineage reference identity mismatch")
        return lineage

    def find_by_final_native_session_id(
        self,
        *,
        lineage_anchor: Path,
        session_id: str,
    ) -> ManagedHeadlessSessionLineage:
        """Resolve a final native session ID through its durable index."""
        return self._find_by_index(
            lineage_anchor=lineage_anchor,
            index_name=_FINAL_NATIVE_INDEX,
            key=session_id,
            predicate=lambda lineage: lineage.final_native_session_id == session_id,
        )

    def find_by_dispatch_id(
        self,
        *,
        lineage_anchor: Path,
        dispatch_id: str,
    ) -> ManagedHeadlessSessionLineage:
        """Resolve a fleet dispatch ID through its durable index."""
        return self._find_by_index(
            lineage_anchor=lineage_anchor,
            index_name=_DISPATCH_INDEX,
            key=dispatch_id,
            predicate=lambda lineage: lineage.dispatch_id == dispatch_id,
        )

    def append_attempt(
        self,
        *,
        lineage_anchor: Path,
        launch_id: str,
        attempt_id: str,
        expected_generation: int,
        expected_record_digest: str,
    ) -> ManagedHeadlessSessionLineage:
        """Append one fresh physical-attempt identity."""

        def mutate(current: ManagedHeadlessSessionLineage) -> ManagedHeadlessSessionLineage:
            if attempt_id in current.attempt_ids:
                return current
            if len(current.attempt_ids) >= _MAX_ATTEMPTS:
                raise ManagedHeadlessSessionLineageConflictError(
                    "Managed lineage attempt limit exceeded"
                )
            return replace(current, attempt_ids=(*current.attempt_ids, attempt_id))

        return self._mutate(
            lineage_anchor=lineage_anchor,
            launch_id=launch_id,
            expected_generation=expected_generation,
            expected_record_digest=expected_record_digest,
            mutate=mutate,
        )

    def bind_candidate_native_session_id(
        self,
        *,
        lineage_anchor: Path,
        launch_id: str,
        session_id: str,
        expected_generation: int,
        expected_record_digest: str,
    ) -> ManagedHeadlessSessionLineage:
        """Append one advisory native session candidate without alias ownership."""

        def mutate(current: ManagedHeadlessSessionLineage) -> ManagedHeadlessSessionLineage:
            if session_id in current.candidate_native_session_ids:
                return current
            if len(current.candidate_native_session_ids) >= _MAX_CANDIDATE_SESSION_IDS:
                raise ManagedHeadlessSessionLineageConflictError(
                    "Managed lineage candidate-session limit exceeded"
                )
            return replace(
                current,
                candidate_native_session_ids=(
                    *current.candidate_native_session_ids,
                    session_id,
                ),
            )

        return self._mutate(
            lineage_anchor=lineage_anchor,
            launch_id=launch_id,
            expected_generation=expected_generation,
            expected_record_digest=expected_record_digest,
            mutate=mutate,
        )

    def bind_launch_contract_digest(
        self,
        *,
        lineage_anchor: Path,
        launch_id: str,
        launch_contract_digest: str,
        expected_generation: int,
        expected_record_digest: str,
    ) -> ManagedHeadlessSessionLineage:
        """Persist the exact physical contract digest before child spawn."""

        def mutate(current: ManagedHeadlessSessionLineage) -> ManagedHeadlessSessionLineage:
            if current.launch_contract_digest == launch_contract_digest:
                return current
            return replace(current, launch_contract_digest=launch_contract_digest)

        return self._mutate(
            lineage_anchor=lineage_anchor,
            launch_id=launch_id,
            expected_generation=expected_generation,
            expected_record_digest=expected_record_digest,
            mutate=mutate,
        )

    def bind_final_native_session_id(
        self,
        *,
        lineage_anchor: Path,
        launch_id: str,
        session_id: str,
        expected_generation: int,
        expected_record_digest: str,
    ) -> ManagedHeadlessSessionLineage:
        """Bind one final native session ID and acquire its durable index."""
        return self._bind_indexed_identity(
            lineage_anchor=lineage_anchor,
            launch_id=launch_id,
            index_name=_FINAL_NATIVE_INDEX,
            key=session_id,
            expected_generation=expected_generation,
            expected_record_digest=expected_record_digest,
            current_value=lambda lineage: lineage.final_native_session_id,
            update=lambda lineage: replace(
                lineage,
                candidate_native_session_ids=(
                    lineage.candidate_native_session_ids
                    if session_id in lineage.candidate_native_session_ids
                    else (*lineage.candidate_native_session_ids, session_id)
                ),
                final_native_session_id=session_id,
            ),
        )

    def rebind_final_native_session_id(
        self,
        *,
        lineage_anchor: Path,
        launch_id: str,
        expected_session_id: str,
        session_id: str,
        expected_generation: int,
        expected_record_digest: str,
    ) -> ManagedHeadlessSessionLineage:
        """Transfer final-session ownership from one verified ID to another."""
        anchor, device, inode = _resolve_anchor(lineage_anchor)
        root = _prepare_root(anchor)
        record_path = _record_path(root, launch_id)
        with _store_lock(root, exclusive=True):
            current = _read_record(record_path)
            _validate_anchor_identity(current, anchor, device, inode)
            if current.final_native_session_id == session_id:
                return current
            if current.final_native_session_id != expected_session_id:
                raise ManagedHeadlessSessionLineageConflictError(
                    "Managed lineage final identity no longer matches the verified resume"
                )
            _require_cas(current, expected_generation, expected_record_digest)
            indexed_launch_id = _read_index(
                root,
                _FINAL_NATIVE_INDEX,
                expected_session_id,
            )
            if indexed_launch_id != launch_id:
                raise ManagedHeadlessSessionLineageConflictError(
                    "Managed lineage prior final identity is owned by another launch"
                )
            _assert_index_available(
                root,
                _FINAL_NATIVE_INDEX,
                session_id,
                launch_id,
            )
            candidates = current.candidate_native_session_ids
            if session_id not in candidates:
                candidates = (*candidates, session_id)
            updated = _next_generation(
                replace(
                    current,
                    candidate_native_session_ids=candidates,
                    final_native_session_id=session_id,
                )
            )
            _write_record(record_path, updated)
            _write_index(root, _FINAL_NATIVE_INDEX, session_id, launch_id)
            _remove_index(
                root,
                _FINAL_NATIVE_INDEX,
                expected_session_id,
            )
            return updated

    def bind_dispatch_id(
        self,
        *,
        lineage_anchor: Path,
        launch_id: str,
        dispatch_id: str,
        expected_generation: int,
        expected_record_digest: str,
    ) -> ManagedHeadlessSessionLineage:
        """Bind one dispatch ID and acquire its durable index."""
        return self._bind_indexed_identity(
            lineage_anchor=lineage_anchor,
            launch_id=launch_id,
            index_name=_DISPATCH_INDEX,
            key=dispatch_id,
            expected_generation=expected_generation,
            expected_record_digest=expected_record_digest,
            current_value=lambda lineage: lineage.dispatch_id,
            update=lambda lineage: replace(lineage, dispatch_id=dispatch_id),
        )

    def set_terminal_state(
        self,
        *,
        lineage_anchor: Path,
        launch_id: str,
        terminal_state: ManagedHeadlessSessionTerminalState,
        expected_generation: int,
        expected_record_digest: str,
    ) -> ManagedHeadlessSessionLineage:
        """Close an active lineage without deleting its provenance."""
        if terminal_state is ManagedHeadlessSessionTerminalState.ACTIVE:
            raise ValueError("terminal_state must close the lineage")

        def mutate(current: ManagedHeadlessSessionLineage) -> ManagedHeadlessSessionLineage:
            if current.terminal_state is terminal_state:
                return current
            if current.terminal_state is not ManagedHeadlessSessionTerminalState.ACTIVE:
                raise ManagedHeadlessSessionLineageConflictError(
                    "Managed lineage terminal state is already closed"
                )
            return replace(current, terminal_state=terminal_state)

        return self._mutate(
            lineage_anchor=lineage_anchor,
            launch_id=launch_id,
            expected_generation=expected_generation,
            expected_record_digest=expected_record_digest,
            mutate=mutate,
        )

    def record_observation(
        self,
        *,
        lineage_anchor: Path,
        launch_id: str,
        observation: NativeShellCaptureObservation,
        expected_generation: int,
        expected_record_digest: str,
    ) -> ManagedHeadlessSessionLineage:
        """Append one idempotent closed observation under strict size bounds."""
        if not isinstance(observation, NativeShellCaptureObservation):
            raise TypeError("observation must be a NativeShellCaptureObservation")

        def mutate(current: ManagedHeadlessSessionLineage) -> ManagedHeadlessSessionLineage:
            if observation in current.observations:
                return current
            candidate = (*current.observations, observation)
            encoded_size = len(
                _canonical_json([item.to_dict() for item in candidate]).encode("utf-8")
            )
            if len(candidate) > _MAX_OBSERVATIONS or encoded_size > _MAX_OBSERVATION_BYTES:
                return replace(
                    current,
                    dropped_observation_count=current.dropped_observation_count + 1,
                )
            return replace(current, observations=candidate)

        return self._mutate(
            lineage_anchor=lineage_anchor,
            launch_id=launch_id,
            expected_generation=expected_generation,
            expected_record_digest=expected_record_digest,
            mutate=mutate,
        )

    def collect_runner_observations(
        self,
        reference: ManagedHeadlessSessionLineageRef,
    ) -> ManagedHeadlessSessionLineage:
        """Ingest closed descriptor-written runner markers with CAS-safe replay."""

        current = self.load_reference(reference)
        root = _prepare_root(Path(reference.lineage_anchor))
        markers = _read_runner_markers(root, reference, current)
        for observation in markers:
            for _ in range(8):
                current = self.load_reference(reference)
                if observation in current.observations:
                    break
                try:
                    current = self.record_observation(
                        lineage_anchor=Path(reference.lineage_anchor),
                        launch_id=reference.launch_id,
                        observation=observation,
                        expected_generation=current.generation,
                        expected_record_digest=current.record_digest,
                    )
                    break
                except ManagedHeadlessSessionLineageCASMismatch:
                    continue
            else:
                raise ManagedHeadlessSessionLineageCASMismatch(
                    "Managed runner observation CAS retry limit exceeded"
                )
            _settle_runner_observation(root, reference, observation)
        return self.load_reference(reference)

    def _find_by_index(
        self,
        *,
        lineage_anchor: Path,
        index_name: str,
        key: str,
        predicate: Callable[[ManagedHeadlessSessionLineage], bool],
    ) -> ManagedHeadlessSessionLineage:
        anchor, device, inode = _resolve_anchor(lineage_anchor)
        root = _prepare_root(anchor)
        with _store_lock(root, exclusive=False):
            launch_id = _read_index(root, index_name, key)
            lineage = _read_record(_record_path(root, launch_id))
        _validate_anchor_identity(lineage, anchor, device, inode)
        if not predicate(lineage):
            raise ValueError("Managed lineage index binding mismatch")
        return lineage

    def _bind_indexed_identity(
        self,
        *,
        lineage_anchor: Path,
        launch_id: str,
        index_name: str,
        key: str,
        expected_generation: int,
        expected_record_digest: str,
        current_value: Callable[[ManagedHeadlessSessionLineage], str | None],
        update: Callable[
            [ManagedHeadlessSessionLineage],
            ManagedHeadlessSessionLineage,
        ],
    ) -> ManagedHeadlessSessionLineage:
        anchor, device, inode = _resolve_anchor(lineage_anchor)
        root = _prepare_root(anchor)
        record_path = _record_path(root, launch_id)
        with _store_lock(root, exclusive=True):
            current = _read_record(record_path)
            _validate_anchor_identity(current, anchor, device, inode)
            existing_value = current_value(current)
            if existing_value == key:
                return current
            if existing_value is not None:
                raise ManagedHeadlessSessionLineageConflictError(
                    f"Managed lineage {index_name} identity is already bound"
                )
            _require_cas(current, expected_generation, expected_record_digest)
            _assert_index_available(root, index_name, key, launch_id)
            updated = _next_generation(update(current))
            _write_record(record_path, updated)
            _write_index(root, index_name, key, launch_id)
            return updated

    def _mutate(
        self,
        *,
        lineage_anchor: Path,
        launch_id: str,
        expected_generation: int,
        expected_record_digest: str,
        mutate: Callable[
            [ManagedHeadlessSessionLineage],
            ManagedHeadlessSessionLineage,
        ],
    ) -> ManagedHeadlessSessionLineage:
        anchor, device, inode = _resolve_anchor(lineage_anchor)
        root = _prepare_root(anchor)
        record_path = _record_path(root, launch_id)
        with _store_lock(root, exclusive=True):
            current = _read_record(record_path)
            _validate_anchor_identity(current, anchor, device, inode)
            candidate = mutate(current)
            if candidate == current:
                return current
            _require_cas(current, expected_generation, expected_record_digest)
            updated = _next_generation(candidate)
            _write_record(record_path, updated)
            return updated


# Index/runner/records helpers live in sibling modules; re-exported for
# existing callers using the canonical _managed_headless_session_lineage path.
from autoskillit.execution.session._managed_headless_session_lineage_indexes import (  # noqa: F401
    _assert_index_available,
    _index_path,
    _read_index,
    _remove_index,
    _write_index,
)
from autoskillit.execution.session._managed_headless_session_lineage_records import (  # noqa: F401
    _creation_projection,
    _new_lineage,
    _next_generation,
    _prepare_root,
    _read_record,
    _record_path,
    _resolve_anchor,
    _store_lock,
    _write_record,
)
from autoskillit.execution.session._managed_headless_session_lineage_runner import (  # noqa: F401
    _read_runner_markers,
    _settle_runner_observation,
)


def _validate_anchor_identity(
    lineage: ManagedHeadlessSessionLineage,
    anchor: Path,
    device: int,
    inode: int,
) -> None:
    if (
        lineage.lineage_anchor != str(anchor)
        or lineage.anchor_device != device
        or lineage.anchor_inode != inode
    ):
        raise ValueError("Managed lineage anchor identity mismatch")


def _require_cas(
    lineage: ManagedHeadlessSessionLineage,
    expected_generation: int,
    expected_record_digest: str,
) -> None:
    if (
        lineage.generation != expected_generation
        or lineage.record_digest != expected_record_digest
    ):
        raise ManagedHeadlessSessionLineageCASMismatch("Managed lineage compare-and-swap mismatch")


def _read_bounded(path: Path) -> bytes:
    try:
        with path.open("rb") as handle:
            raw = handle.read(_MAX_RECORD_BYTES + 1)
    except FileNotFoundError:
        raise FileNotFoundError(f"Managed lineage record not found: {path.name}") from None
    if len(raw) > _MAX_RECORD_BYTES:
        raise ValueError("Managed lineage artifact is oversized")
    return raw
