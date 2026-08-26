"""Process-local weighted LRU cache for skill capability evidence.

Owns the cache singleton, the three cache dataclasses, the four cache
constants, and the three weight helpers. Stdlib-only at runtime — the
``SkillCapabilityEvidence`` typing import is guarded by ``TYPE_CHECKING``
because the cache dataclasses' annotations reference it.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from threading import Event, RLock
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autoskillit.workspace.skill_capability_scanner import SkillCapabilityEvidence

_SkillCapabilityEvidenceKey = tuple[str, str]

# Accounted resident payload includes exact key strings, evidence source strings,
# and a stable policy charge per immutable evidence record. Entry count bounds
# the remaining fixed per-entry overhead.
_SKILL_CAPABILITY_EVIDENCE_RECORD_WEIGHT_BYTES = 192
_SKILL_CAPABILITY_EVIDENCE_CACHE_MAX_ENTRIES = 256
_SKILL_CAPABILITY_EVIDENCE_CACHE_MAX_BYTES = 16 * 1024 * 1024
_SKILL_CAPABILITY_EVIDENCE_CACHE_MAX_INPUT_BYTES = 512 * 1024


@dataclass(frozen=True, slots=True)
class _SkillCapabilityEvidenceCacheEntry:
    evidence: tuple[SkillCapabilityEvidence, ...]
    weight_bytes: int


@dataclass(frozen=True, slots=True)
class _SkillCapabilityEvidenceCacheInfo:
    max_entries: int
    max_bytes: int
    max_input_bytes: int
    entry_count: int
    weight_bytes: int
    inflight_builds: int
    inflight_waiters: int


@dataclass(slots=True)
class _SkillCapabilityEvidenceBuildState:
    event: Event = field(default_factory=Event)
    result: tuple[SkillCapabilityEvidence, ...] | None = None
    error: BaseException | None = None


class _SkillCapabilityEvidenceCache:
    """Thread-safe weighted LRU with generation-scoped single-flight state."""

    def __init__(
        self,
        *,
        max_entries: int,
        max_bytes: int,
        max_input_bytes: int,
    ) -> None:
        for field_name, value in (
            ("max_entries", max_entries),
            ("max_bytes", max_bytes),
            ("max_input_bytes", max_input_bytes),
        ):
            if value <= 0:
                raise ValueError(f"{field_name} must be positive")

        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._max_input_bytes = max_input_bytes
        self._entries: OrderedDict[
            _SkillCapabilityEvidenceKey,
            _SkillCapabilityEvidenceCacheEntry,
        ] = OrderedDict()
        self._inflight: dict[
            _SkillCapabilityEvidenceKey,
            _SkillCapabilityEvidenceBuildState,
        ] = {}
        self._weight_bytes = 0
        self._inflight_waiters = 0
        self._lock = RLock()

    @property
    def max_input_bytes(self) -> int:
        return self._max_input_bytes

    def info(self) -> _SkillCapabilityEvidenceCacheInfo:
        with self._lock:
            return _SkillCapabilityEvidenceCacheInfo(
                max_entries=self._max_entries,
                max_bytes=self._max_bytes,
                max_input_bytes=self._max_input_bytes,
                entry_count=len(self._entries),
                weight_bytes=self._weight_bytes,
                inflight_builds=len(self._inflight),
                inflight_waiters=self._inflight_waiters,
            )

    def _new_build_state(self) -> _SkillCapabilityEvidenceBuildState:
        return _SkillCapabilityEvidenceBuildState()

    def _lookup_or_register(
        self,
        key: _SkillCapabilityEvidenceKey,
    ) -> tuple[
        tuple[SkillCapabilityEvidence, ...] | None,
        _SkillCapabilityEvidenceBuildState | None,
        bool,
    ]:
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                self._entries.move_to_end(key)
                return entry.evidence, None, False

            state = self._inflight.get(key)
            if state is not None:
                self._inflight_waiters += 1
                return None, state, False

            state = self._new_build_state()
            self._inflight[key] = state
            return None, state, True

    def _wait_for_build(
        self,
        key: _SkillCapabilityEvidenceKey,
        state: _SkillCapabilityEvidenceBuildState,
    ) -> tuple[SkillCapabilityEvidence, ...]:
        try:
            state.event.wait()
        except BaseException:
            with self._lock:
                self._inflight_waiters -= 1
            raise

        with self._lock:
            self._inflight_waiters -= 1
            if state.error is not None:
                raise RuntimeError(
                    "Capability evidence build failed in another thread"
                ) from state.error
            result = state.result
            if result is None:
                raise RuntimeError(
                    "Capability evidence build completed without a result for skill "
                    f"{key[1]!r} (content bytes={len(key[0])})"
                )
            entry = self._entries.get(key)
            if entry is not None and entry.evidence is result:
                self._entries.move_to_end(key)
            return result

    def _evict_if_needed_locked(self) -> None:
        while len(self._entries) > self._max_entries or self._weight_bytes > self._max_bytes:
            _, entry = self._entries.popitem(last=False)
            self._weight_bytes -= entry.weight_bytes

    def _publish_failure(
        self,
        key: _SkillCapabilityEvidenceKey,
        state: _SkillCapabilityEvidenceBuildState,
        error: BaseException,
    ) -> None:
        with self._lock:
            state.result = None
            state.error = error
            if self._inflight.get(key) is state:
                del self._inflight[key]
            state.event.set()

    def _complete_build(
        self,
        key: _SkillCapabilityEvidenceKey,
        state: _SkillCapabilityEvidenceBuildState,
        result: tuple[SkillCapabilityEvidence, ...],
        weight_bytes: int,
    ) -> tuple[SkillCapabilityEvidence, ...]:
        with self._lock:
            resident_mutated = False
            try:
                if weight_bytes <= self._max_bytes:
                    resident_mutated = True
                    previous = self._entries.pop(key, None)
                    if previous is not None:
                        self._weight_bytes -= previous.weight_bytes
                    self._entries[key] = _SkillCapabilityEvidenceCacheEntry(
                        evidence=result,
                        weight_bytes=weight_bytes,
                    )
                    self._weight_bytes += weight_bytes
                    self._evict_if_needed_locked()

                state.result = result
                state.error = None
                if self._inflight.get(key) is state:
                    del self._inflight[key]
                state.event.set()
            except BaseException as error:
                if resident_mutated:
                    self._entries.clear()
                    self._weight_bytes = 0
                state.result = None
                state.error = error
                if self._inflight.get(key) is state:
                    del self._inflight[key]
                state.event.set()
                raise
        return result


def _retained_string_weight_bytes(value: str) -> int:
    return len(value.encode("utf-8", errors="surrogatepass"))


def _skill_capability_evidence_input_weight_bytes(
    content: str,
    effective_skill_name: str,
) -> int:
    return _retained_string_weight_bytes(content) + _retained_string_weight_bytes(
        effective_skill_name
    )


def _skill_capability_evidence_entry_weight_bytes(
    input_weight_bytes: int,
    evidence: tuple[SkillCapabilityEvidence, ...],
) -> int:
    return (
        input_weight_bytes
        + sum(_retained_string_weight_bytes(item.source) for item in evidence)
        + len(evidence) * _SKILL_CAPABILITY_EVIDENCE_RECORD_WEIGHT_BYTES
    )


_SKILL_CAPABILITY_EVIDENCE_CACHE = _SkillCapabilityEvidenceCache(
    max_entries=_SKILL_CAPABILITY_EVIDENCE_CACHE_MAX_ENTRIES,
    max_bytes=_SKILL_CAPABILITY_EVIDENCE_CACHE_MAX_BYTES,
    max_input_bytes=_SKILL_CAPABILITY_EVIDENCE_CACHE_MAX_INPUT_BYTES,
)


__all__ = [
    "_SKILL_CAPABILITY_EVIDENCE_CACHE",
    "_SKILL_CAPABILITY_EVIDENCE_CACHE_MAX_ENTRIES",
    "_SKILL_CAPABILITY_EVIDENCE_CACHE_MAX_BYTES",
    "_SKILL_CAPABILITY_EVIDENCE_CACHE_MAX_INPUT_BYTES",
    "_SKILL_CAPABILITY_EVIDENCE_RECORD_WEIGHT_BYTES",
    "_SkillCapabilityEvidenceBuildState",
    "_SkillCapabilityEvidenceCache",
    "_SkillCapabilityEvidenceCacheEntry",
    "_SkillCapabilityEvidenceCacheInfo",
    "_retained_string_weight_bytes",
    "_skill_capability_evidence_entry_weight_bytes",
    "_skill_capability_evidence_input_weight_bytes",
]
