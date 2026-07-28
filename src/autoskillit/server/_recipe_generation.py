"""Kitchen-scoped ownership of compiled and persisted recipe generations."""

from __future__ import annotations

import json
import math
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass, field, fields, is_dataclass, replace
from enum import Enum
from threading import Lock, RLock
from types import MappingProxyType
from typing import Any, cast

from autoskillit.core import (
    FinalizedRecipeProjection,
    RecipeArtifactGeneration,
    RecipeExecutionSnapshot,
    RecipeFlowGeneration,
)
from autoskillit.server.recipe_section._lifecycle import (
    register_kitchen_retirement_callback,
)

RECIPE_GENERATION_STORE_MAX_ENTRIES = 8
RECIPE_GENERATION_STORE_MAX_BYTES = 32 * 1024 * 1024

__all__ = [
    "RECIPE_GENERATION_STORE_MAX_BYTES",
    "RECIPE_GENERATION_STORE_MAX_ENTRIES",
    "RecipeGenerationCapacityError",
    "RecipeGenerationConflictError",
    "RecipeGenerationError",
    "RecipeGenerationRecord",
    "RecipeGenerationRetiredError",
    "RecipeGenerationStore",
    "get_recipe_generation_store",
    "recipe_generation_weight_bytes",
    "retire_kitchen",
    "thaw_recipe_generation_mapping",
]


class RecipeGenerationError(RuntimeError):
    """Base error for generation-store admission and binding failures."""


class RecipeGenerationConflictError(RecipeGenerationError):
    """An existing exact generation disagrees with a replay."""


class RecipeGenerationCapacityError(RecipeGenerationError):
    """A single generation cannot fit within the configured store bounds."""


class RecipeGenerationRetiredError(RecipeGenerationError):
    """A write targeted a kitchen whose generation namespace is retired."""


def _freeze_primitive(value: object, *, path: str) -> object:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} contains a non-finite float")
        return value
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise TypeError(f"{path} object keys must be strings")
        copied: dict[str, object] = {}
        for key in sorted(value):
            copied[key] = _freeze_primitive(value[key], path=f"{path}.{key}")
        return MappingProxyType(copied)
    if isinstance(value, (list, tuple)):
        return tuple(
            _freeze_primitive(item, path=f"{path}[{index}]") for index, item in enumerate(value)
        )
    raise TypeError(f"{path} contains unsupported value {type(value).__name__}")


def _freeze_primitive_mapping(
    value: Mapping[str, object],
    *,
    field_name: str,
) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be a mapping")
    frozen = _freeze_primitive(value, path=field_name)
    return cast(Mapping[str, object], frozen)


def _weight_primitive(value: object) -> object:
    """Convert retained immutable values to a deterministic JSON primitive tree."""
    if isinstance(value, Enum):
        return _weight_primitive(value.value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("generation weight input contains a non-finite float")
        return value
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("generation weight mapping keys must be strings")
        result: dict[str, object] = {}
        for key in sorted(value):
            result[key] = _weight_primitive(value[key])
        return result
    if isinstance(value, (list, tuple)):
        return [_weight_primitive(item) for item in value]
    if isinstance(value, (set, frozenset)):
        converted = [_weight_primitive(item) for item in value]
        return sorted(converted, key=_canonical_json)
    if is_dataclass(value) and not isinstance(value, type):
        return {
            item.name: _weight_primitive(getattr(value, item.name))
            for item in fields(cast(Any, value))
        }
    raise TypeError(f"generation weight input contains unsupported value {type(value).__name__}")


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


@dataclass(frozen=True, slots=True)
class RecipeGenerationRecord:
    """One immutable compile generation and its exact persisted surface bindings."""

    kitchen_id: str
    normalized_compile_key: str
    recipe_name: str
    finalized_projection: FinalizedRecipeProjection
    flow_generation: RecipeFlowGeneration
    artifact_payload: Mapping[str, object]
    execution_snapshot: RecipeExecutionSnapshot
    execution_id: str
    compile_inputs: Mapping[str, object]
    surface_bindings: Mapping[str, RecipeArtifactGeneration] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.kitchen_id, str):
            raise TypeError("RecipeGenerationRecord.kitchen_id must be a string")
        for field_name in ("normalized_compile_key", "recipe_name", "execution_id"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"RecipeGenerationRecord.{field_name} must be a non-empty string")
        if not isinstance(self.finalized_projection, FinalizedRecipeProjection):
            raise TypeError("finalized_projection must be a FinalizedRecipeProjection")
        if not isinstance(self.flow_generation, RecipeFlowGeneration):
            raise TypeError("flow_generation must be a RecipeFlowGeneration")
        if not isinstance(self.execution_snapshot, RecipeExecutionSnapshot):
            raise TypeError("execution_snapshot must be a RecipeExecutionSnapshot")
        if self.execution_id != self.execution_snapshot.execution_id:
            raise ValueError("execution_id must match execution_snapshot.execution_id")
        if self.recipe_name != self.execution_snapshot.recipe_name:
            raise ValueError("recipe_name must match execution_snapshot.recipe_name")

        artifact_payload = _freeze_primitive_mapping(
            self.artifact_payload,
            field_name="artifact_payload",
        )
        compile_inputs = _freeze_primitive_mapping(
            self.compile_inputs,
            field_name="compile_inputs",
        )
        bindings: dict[str, RecipeArtifactGeneration] = {}
        if not isinstance(self.surface_bindings, Mapping):
            raise TypeError("surface_bindings must be a mapping")
        for surface in sorted(self.surface_bindings):
            generation = self.surface_bindings[surface]
            if not isinstance(surface, str) or not surface:
                raise ValueError("surface binding names must be non-empty strings")
            if not isinstance(generation, RecipeArtifactGeneration):
                raise TypeError("surface bindings must contain RecipeArtifactGeneration values")
            if generation.recipe_name != self.recipe_name:
                raise ValueError("surface artifact generation must match the record recipe_name")
            bindings[surface] = generation

        object.__setattr__(self, "artifact_payload", artifact_payload)
        object.__setattr__(self, "compile_inputs", compile_inputs)
        object.__setattr__(self, "surface_bindings", MappingProxyType(bindings))


def recipe_generation_weight_bytes(record: RecipeGenerationRecord) -> int:
    """Return the exact UTF-8 size of the canonical retained-record projection."""
    if not isinstance(record, RecipeGenerationRecord):
        raise TypeError("record must be a RecipeGenerationRecord")
    primitive = _weight_primitive(record)
    return len(_canonical_json(primitive).encode("utf-8"))


def thaw_recipe_generation_mapping(
    value: Mapping[str, object],
) -> dict[str, object]:
    """Return a mutable JSON-primitive copy of one retained mapping."""
    primitive = _weight_primitive(value)
    if not isinstance(primitive, dict):
        raise TypeError("recipe generation mapping did not thaw to a JSON object")
    return primitive


def _copy_record(record: RecipeGenerationRecord) -> RecipeGenerationRecord:
    return replace(
        record,
        artifact_payload=dict(record.artifact_payload),
        compile_inputs=dict(record.compile_inputs),
        surface_bindings=dict(record.surface_bindings),
    )


def _same_compile_generation(
    left: RecipeGenerationRecord,
    right: RecipeGenerationRecord,
) -> bool:
    return (
        left.kitchen_id == right.kitchen_id
        and left.normalized_compile_key == right.normalized_compile_key
        and left.recipe_name == right.recipe_name
        and left.finalized_projection == right.finalized_projection
        and left.flow_generation == right.flow_generation
        and left.artifact_payload == right.artifact_payload
        and left.execution_snapshot == right.execution_snapshot
        and left.execution_id == right.execution_id
        and left.compile_inputs == right.compile_inputs
    )


_CompileIndexKey = tuple[str, str]
_ArtifactIndexKey = tuple[str, RecipeArtifactGeneration]


class RecipeGenerationStore:
    """Bounded thread-safe LRU with compile and exact-artifact indexes."""

    def __init__(
        self,
        *,
        max_entries: int = RECIPE_GENERATION_STORE_MAX_ENTRIES,
        max_bytes: int = RECIPE_GENERATION_STORE_MAX_BYTES,
    ) -> None:
        if max_entries < 0:
            raise ValueError("generation store max_entries must not be negative")
        if max_bytes < 0:
            raise ValueError("generation store max_bytes must not be negative")
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._compile_index: OrderedDict[_CompileIndexKey, RecipeGenerationRecord] = OrderedDict()
        self._artifact_index: dict[_ArtifactIndexKey, _CompileIndexKey] = {}
        self._weight_bytes = 0
        self._retired_kitchens: set[str] = set()
        self._lock = RLock()

    @property
    def entry_count(self) -> int:
        with self._lock:
            return len(self._compile_index)

    @property
    def weight_bytes(self) -> int:
        with self._lock:
            return self._weight_bytes

    def _require_active_locked(self, kitchen_id: str) -> None:
        if kitchen_id in self._retired_kitchens:
            raise RecipeGenerationRetiredError(
                f"recipe generation kitchen is retired: {kitchen_id}"
            )

    def _deindex_artifacts_locked(
        self,
        key: _CompileIndexKey,
        record: RecipeGenerationRecord,
    ) -> None:
        for generation in record.surface_bindings.values():
            artifact_key = (record.kitchen_id, generation)
            if self._artifact_index.get(artifact_key) == key:
                self._artifact_index.pop(artifact_key)

    def _remove_locked(self, key: _CompileIndexKey) -> None:
        record = self._compile_index.pop(key)
        self._deindex_artifacts_locked(key, record)
        self._weight_bytes -= recipe_generation_weight_bytes(record)

    def _validate_artifact_owners_locked(
        self,
        key: _CompileIndexKey,
        record: RecipeGenerationRecord,
    ) -> None:
        for generation in record.surface_bindings.values():
            owner = self._artifact_index.get((record.kitchen_id, generation))
            if owner is not None and owner != key:
                raise RecipeGenerationConflictError(
                    "exact artifact generation is already bound to another compile generation"
                )

    def _replace_locked(
        self,
        key: _CompileIndexKey,
        record: RecipeGenerationRecord,
        *,
        weight: int,
    ) -> None:
        if key in self._compile_index:
            self._remove_locked(key)
        self._compile_index[key] = record
        self._weight_bytes += weight
        for generation in record.surface_bindings.values():
            self._artifact_index[(record.kitchen_id, generation)] = key
        while len(self._compile_index) > self._max_entries or self._weight_bytes > self._max_bytes:
            oldest_key = next(iter(self._compile_index))
            self._remove_locked(oldest_key)

    def put(self, record: RecipeGenerationRecord) -> RecipeGenerationRecord:
        """Admit a compile generation or accept an exact idempotent replay."""
        if not isinstance(record, RecipeGenerationRecord):
            raise TypeError("record must be a RecipeGenerationRecord")
        candidate = _copy_record(record)
        key = (candidate.kitchen_id, candidate.normalized_compile_key)
        with self._lock:
            self._require_active_locked(candidate.kitchen_id)
            existing = self._compile_index.get(key)
            if existing is not None:
                if not _same_compile_generation(existing, candidate):
                    raise RecipeGenerationConflictError(
                        "normalized compile generation replay does not match"
                    )
                merged_bindings = dict(existing.surface_bindings)
                for surface, generation in candidate.surface_bindings.items():
                    bound = merged_bindings.get(surface)
                    if bound is not None and bound != generation:
                        raise RecipeGenerationConflictError(
                            f"surface {surface!r} already has another exact generation"
                        )
                    merged_bindings[surface] = generation
                candidate = replace(existing, surface_bindings=merged_bindings)
            self._validate_artifact_owners_locked(key, candidate)
            weight = recipe_generation_weight_bytes(candidate)
            if self._max_entries == 0 or weight > self._max_bytes:
                raise RecipeGenerationCapacityError(
                    "recipe generation exceeds store entry or byte capacity"
                )
            self._replace_locked(key, candidate, weight=weight)
            return _copy_record(candidate)

    def bind_surface(
        self,
        kitchen_id: str,
        normalized_compile_key: str,
        surface: str,
        generation: RecipeArtifactGeneration,
    ) -> RecipeGenerationRecord:
        """Atomically bind one surface to an exact persisted generation."""
        if not isinstance(kitchen_id, str):
            raise TypeError("kitchen_id must be a string")
        for name, value in (
            ("normalized_compile_key", normalized_compile_key),
            ("surface", surface),
        ):
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must be a non-empty string")
        if not isinstance(generation, RecipeArtifactGeneration):
            raise TypeError("generation must be a RecipeArtifactGeneration")

        key = (kitchen_id, normalized_compile_key)
        with self._lock:
            self._require_active_locked(kitchen_id)
            existing = self._compile_index.get(key)
            if existing is None:
                raise KeyError("normalized compile generation is not present for this kitchen")
            if generation.recipe_name != existing.recipe_name:
                raise RecipeGenerationConflictError(
                    "artifact generation recipe_name does not match compile generation"
                )
            bound = existing.surface_bindings.get(surface)
            if bound is not None:
                if bound != generation:
                    raise RecipeGenerationConflictError(
                        f"surface {surface!r} already has another exact generation"
                    )
                self._compile_index.move_to_end(key)
                return _copy_record(existing)
            owner = self._artifact_index.get((kitchen_id, generation))
            if owner is not None and owner != key:
                raise RecipeGenerationConflictError(
                    "exact artifact generation is already bound to another compile generation"
                )

            bindings = dict(existing.surface_bindings)
            bindings[surface] = generation
            candidate = replace(existing, surface_bindings=bindings)
            weight = recipe_generation_weight_bytes(candidate)
            if weight > self._max_bytes:
                raise RecipeGenerationCapacityError(
                    "surface binding exceeds generation store byte capacity"
                )
            self._replace_locked(key, candidate, weight=weight)
            return _copy_record(candidate)

    def lookup_compile(
        self,
        kitchen_id: str,
        normalized_compile_key: str,
    ) -> RecipeGenerationRecord | None:
        """Return an immutable defensive copy for a normalized compile key."""
        key = (kitchen_id, normalized_compile_key)
        with self._lock:
            record = self._compile_index.get(key)
            if record is None:
                return None
            self._compile_index.move_to_end(key)
            return _copy_record(record)

    def lookup_artifact(
        self,
        kitchen_id: str,
        generation: RecipeArtifactGeneration,
    ) -> RecipeGenerationRecord | None:
        """Return the compile record owning one exact artifact descriptor."""
        with self._lock:
            key = self._artifact_index.get((kitchen_id, generation))
            if key is None:
                return None
            record = self._compile_index.get(key)
            if record is None:
                raise RuntimeError("artifact generation index has no compile owner")
            self._compile_index.move_to_end(key)
            return _copy_record(record)

    def retire_kitchen(self, kitchen_id: str) -> None:
        """Permanently reject writes and remove all entries for one kitchen."""
        if not isinstance(kitchen_id, str) or not kitchen_id:
            raise ValueError("kitchen_id must be a non-empty string")
        with self._lock:
            self._retired_kitchens.add(kitchen_id)
            keys = [key for key in self._compile_index if key[0] == kitchen_id]
            for key in keys:
                self._remove_locked(key)

    def clear(self) -> None:
        """Clear all indexes and retirement markers."""
        with self._lock:
            self._compile_index.clear()
            self._artifact_index.clear()
            self._weight_bytes = 0
            self._retired_kitchens.clear()


_RECIPE_GENERATION_STORE: RecipeGenerationStore | None = None
_RECIPE_GENERATION_STORE_LOCK = Lock()


def get_recipe_generation_store() -> RecipeGenerationStore:
    """Return the process-local bounded generation store."""
    global _RECIPE_GENERATION_STORE
    with _RECIPE_GENERATION_STORE_LOCK:
        if _RECIPE_GENERATION_STORE is None:
            _RECIPE_GENERATION_STORE = RecipeGenerationStore()
        return _RECIPE_GENERATION_STORE


def retire_kitchen(kitchen_id: str) -> None:
    """Retire generation state when the owning kitchen artifact is retired."""
    get_recipe_generation_store().retire_kitchen(kitchen_id)


register_kitchen_retirement_callback(retire_kitchen)
