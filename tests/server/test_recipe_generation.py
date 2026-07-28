"""Focused contracts for the kitchen-scoped recipe generation store."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier
from typing import cast

import pytest

import autoskillit.server._recipe_generation as generation_module
from autoskillit.core import (
    RECIPE_ARTIFACT_DESCRIPTOR_VERSION,
    RECIPE_ARTIFACT_SCHEMA_VERSION,
    RECIPE_FLOW_SCHEMA_VERSION,
    BindingMode,
    BoundStepInvocation,
    FinalizedRecipeProjection,
    RecipeArtifactGeneration,
    RecipeBindingProjection,
    RecipeExecutionSnapshot,
    RecipeFlowGeneration,
    compute_recipe_execution_snapshot_digest,
)
from autoskillit.server._recipe_generation import (
    RecipeGenerationCapacityError,
    RecipeGenerationConflictError,
    RecipeGenerationRecord,
    RecipeGenerationRetiredError,
    RecipeGenerationStore,
    recipe_generation_weight_bytes,
)
from autoskillit.server.recipe_section._lifecycle import notify_kitchen_retired

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def _hash(seed: str) -> str:
    return f"sha256:{hashlib.sha256(seed.encode()).hexdigest()}"


def _projection() -> FinalizedRecipeProjection:
    invocation = BoundStepInvocation(
        step_name="step",
        tool_name="run_skill",
        mode=BindingMode.RECIPE,
        skill_name="example",
        mcp_kwargs=(),
        skill_inputs=(),
    )
    return FinalizedRecipeProjection(
        binding_projection=RecipeBindingProjection({"step": invocation}),
        ordered_step_names=("step",),
        entrypoint="step",
        ordered_flow_edges=(),
    )


def _snapshot(recipe_name: str, execution_id: str) -> RecipeExecutionSnapshot:
    content_hash = _hash(f"{execution_id}:content")
    composite_hash = _hash(f"{execution_id}:composite")
    dynamic_skill_step_names = frozenset({"step"})
    snapshot_digest = compute_recipe_execution_snapshot_digest(
        execution_id=execution_id,
        recipe_name=recipe_name,
        content_hash=content_hash,
        composite_hash=composite_hash,
        templates={},
        dynamic_skill_step_names=dynamic_skill_step_names,
    )
    return RecipeExecutionSnapshot(
        execution_id=execution_id,
        recipe_name=recipe_name,
        content_hash=content_hash,
        composite_hash=composite_hash,
        templates={},
        snapshot_digest=snapshot_digest,
        dynamic_skill_step_names=dynamic_skill_step_names,
    )


def _record(
    *,
    kitchen_id: str = "kitchen-a",
    compile_key: str = "compile-a",
    recipe_name: str = "recipe",
    payload_marker: str = "a",
) -> RecipeGenerationRecord:
    execution_id = f"execution-{compile_key}"
    record = json.dumps(
        {"kind": "entrypoint", "name": "step"},
        separators=(",", ":"),
        sort_keys=True,
    )
    return RecipeGenerationRecord(
        kitchen_id=kitchen_id,
        normalized_compile_key=compile_key,
        recipe_name=recipe_name,
        finalized_projection=_projection(),
        flow_generation=RecipeFlowGeneration(
            schema_version=RECIPE_FLOW_SCHEMA_VERSION,
            records=(record,),
        ),
        artifact_payload={
            "marker": payload_marker,
            "nested": {"items": [1, True, None]},
        },
        execution_snapshot=_snapshot(recipe_name, execution_id),
        execution_id=execution_id,
        compile_inputs={"ingredient_overrides": {"names": ["one", "two"]}},
    )


def _generation(
    marker: str,
    *,
    recipe_name: str = "recipe",
) -> RecipeArtifactGeneration:
    return RecipeArtifactGeneration(
        producer_tool=f"producer-{marker}",
        recipe_name=recipe_name,
        descriptor_version=RECIPE_ARTIFACT_DESCRIPTOR_VERSION,
        schema_version=RECIPE_ARTIFACT_SCHEMA_VERSION,
        payload_sha256=_hash(f"{marker}:payload"),
        artifact_blob_sha256=_hash(f"{marker}:blob"),
        artifact_blob_size_bytes=100,
        body_sha256=_hash(f"{marker}:body"),
        body_size_bytes=40,
        flow_schema_version=RECIPE_FLOW_SCHEMA_VERSION,
        flow_sha256=_hash(f"{marker}:flow"),
        flow_size_bytes=20,
        flow_record_count=1,
    )


def test_record_owns_canonical_immutable_primitive_copies() -> None:
    payload = {"nested": {"items": [1, 2]}}
    compile_inputs = {"names": ["first"]}
    base = _record()
    record = replace(
        base,
        artifact_payload=payload,
        compile_inputs=compile_inputs,
    )
    payload["nested"]["items"].append(3)
    compile_inputs["names"].append("second")

    store = RecipeGenerationStore()
    stored = store.put(record)
    fetched = store.lookup_compile("kitchen-a", "compile-a")

    assert fetched is not None
    assert fetched is not stored
    nested = cast(Mapping[str, object], fetched.artifact_payload["nested"])
    assert nested["items"] == (1, 2)
    assert fetched.compile_inputs["names"] == ("first",)
    with pytest.raises(TypeError):
        fetched.artifact_payload["new"] = "value"  # type: ignore[index]
    with pytest.raises(TypeError):
        nested["new"] = "value"  # type: ignore[index]


def test_put_accepts_exact_compile_replay_and_rejects_conflict() -> None:
    store = RecipeGenerationStore()
    record = _record()

    first = store.put(record)
    replay = store.put(record)

    assert replay == first
    assert store.entry_count == 1
    with pytest.raises(RecipeGenerationConflictError):
        store.put(replace(record, artifact_payload={"marker": "different"}))


def test_surface_binding_is_exact_idempotent_and_dually_indexed() -> None:
    store = RecipeGenerationStore()
    record = store.put(_record())
    artifact = _generation("one")

    bound = store.bind_surface(
        record.kitchen_id,
        record.normalized_compile_key,
        "open_kitchen",
        artifact,
    )
    replay = store.bind_surface(
        record.kitchen_id,
        record.normalized_compile_key,
        "open_kitchen",
        artifact,
    )

    assert replay == bound
    assert bound.surface_bindings == {"open_kitchen": artifact}
    assert store.lookup_artifact(record.kitchen_id, artifact) == bound
    with pytest.raises(RecipeGenerationConflictError):
        store.bind_surface(
            record.kitchen_id,
            record.normalized_compile_key,
            "open_kitchen",
            _generation("different"),
        )


def test_artifact_generation_cannot_alias_another_compile_generation() -> None:
    store = RecipeGenerationStore()
    first = store.put(_record(compile_key="first"))
    second = store.put(_record(compile_key="second"))
    artifact = _generation("shared")
    store.bind_surface(
        first.kitchen_id,
        first.normalized_compile_key,
        "open_kitchen",
        artifact,
    )

    with pytest.raises(RecipeGenerationConflictError):
        store.bind_surface(
            second.kitchen_id,
            second.normalized_compile_key,
            "load_recipe",
            artifact,
        )


def test_entry_lru_eviction_removes_every_artifact_alias() -> None:
    store = RecipeGenerationStore(max_entries=2)
    first = store.put(_record(compile_key="first"))
    second = store.put(_record(compile_key="second"))
    first_artifact = _generation("first")
    second_artifact = _generation("second")
    store.bind_surface(
        first.kitchen_id,
        first.normalized_compile_key,
        "open_kitchen",
        first_artifact,
    )
    store.bind_surface(
        second.kitchen_id,
        second.normalized_compile_key,
        "open_kitchen",
        second_artifact,
    )
    assert store.lookup_artifact(first.kitchen_id, first_artifact) is not None

    store.put(_record(compile_key="third"))

    assert store.lookup_compile(second.kitchen_id, second.normalized_compile_key) is None
    assert store.lookup_artifact(second.kitchen_id, second_artifact) is None
    assert store.lookup_compile(first.kitchen_id, first.normalized_compile_key) is not None


def test_canonical_weight_drives_byte_admission_and_lru_eviction() -> None:
    first = _record(compile_key="first")
    second = _record(compile_key="second", payload_marker="larger-payload")
    first_weight = recipe_generation_weight_bytes(first)
    second_weight = recipe_generation_weight_bytes(second)
    store = RecipeGenerationStore(
        max_entries=3,
        max_bytes=first_weight + second_weight - 1,
    )

    store.put(first)
    store.put(second)

    assert store.lookup_compile(first.kitchen_id, first.normalized_compile_key) is None
    assert store.lookup_compile(second.kitchen_id, second.normalized_compile_key) is not None
    assert store.weight_bytes == second_weight

    too_small = RecipeGenerationStore(
        max_entries=1,
        max_bytes=first_weight - 1,
    )
    with pytest.raises(RecipeGenerationCapacityError):
        too_small.put(first)
    assert too_small.entry_count == 0


def test_retirement_callback_removes_kitchen_and_permanently_rejects_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = RecipeGenerationStore()
    monkeypatch.setattr(generation_module, "_RECIPE_GENERATION_STORE", store)
    record = store.put(_record())
    artifact = _generation("retired")
    store.bind_surface(
        record.kitchen_id,
        record.normalized_compile_key,
        "open_kitchen",
        artifact,
    )

    notify_kitchen_retired(record.kitchen_id)

    assert store.lookup_compile(record.kitchen_id, record.normalized_compile_key) is None
    assert store.lookup_artifact(record.kitchen_id, artifact) is None
    assert store.entry_count == 0
    assert store.weight_bytes == 0
    with pytest.raises(RecipeGenerationRetiredError):
        store.put(record)


def test_concurrent_conflicting_surface_bind_has_one_winner() -> None:
    store = RecipeGenerationStore()
    record = store.put(_record())
    generations = (_generation("left"), _generation("right"))
    barrier = Barrier(2)

    def bind(artifact: RecipeArtifactGeneration) -> str:
        barrier.wait()
        try:
            store.bind_surface(
                record.kitchen_id,
                record.normalized_compile_key,
                "open_kitchen",
                artifact,
            )
        except RecipeGenerationConflictError:
            return "conflict"
        return "bound"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(bind, generations))

    assert sorted(outcomes) == ["bound", "conflict"]
    stored = store.lookup_compile(record.kitchen_id, record.normalized_compile_key)
    assert stored is not None
    winner = stored.surface_bindings["open_kitchen"]
    assert store.lookup_artifact(record.kitchen_id, winner) == stored
