"""Focused contracts for segmented startup and checkpoint delivery."""

from __future__ import annotations

from dataclasses import replace

import pytest

import autoskillit.server._recipe_generation as generation_module
import autoskillit.server._recipe_segment_delivery as segment_delivery_module
from autoskillit.core import (
    RECIPE_EXECUTION_CREDENTIAL_WIRE_KEY,
    FinalizedRecipeProjection,
    FinalizedRecipeSegment,
    RecipeBindingProjection,
)
from autoskillit.pipeline import NoActiveRecipe, ReadyRecipe
from autoskillit.server._recipe_artifact import (
    _finalized_projection_payload,
    _normalized_recipe_compile_identity,
    load_recipe_artifact,
    persist_recipe_artifact,
)
from autoskillit.server._recipe_generation import RecipeGenerationStore
from autoskillit.server._recipe_section_pagination import extract_recipe_step_bodies
from autoskillit.server._recipe_segment_delivery import (
    RecipeSegmentDeliveryError,
    prepare_recipe_segment_delivery,
    uses_segmented_startup,
)

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium]


def _projection(*, segmented: bool) -> FinalizedRecipeProjection:
    return FinalizedRecipeProjection(
        binding_projection=RecipeBindingProjection(invocations={}),
        ordered_step_names=("step",),
        entrypoint="step",
        ordered_flow_edges=(),
        delivery_segments=(
            (FinalizedRecipeSegment(name="Initial", ordered_step_names=("step",)),)
            if segmented
            else ()
        ),
    )


@pytest.mark.parametrize(
    ("surface", "expected"),
    [
        ("open_kitchen", True),
        ("open_kitchen_deferred_recall", True),
        ("load_recipe", False),
        ("get_recipe", False),
    ],
)
def test_compact_startup_is_limited_to_open_kitchen_surfaces(
    surface: str,
    expected: bool,
) -> None:
    assert uses_segmented_startup(surface, _projection(segmented=True)) is expected
    assert uses_segmented_startup(surface, _projection(segmented=False)) is False


def _install_segmented_ready(ready_context):
    tool_ctx = ready_context.tool_ctx
    state = tool_ctx.recipe_initialization_state
    assert isinstance(state, ReadyRecipe)
    ordered = state.finalized_projection.ordered_step_names
    assert ordered[:2] == ("scope", "select_directions")
    projection = replace(
        state.finalized_projection,
        delivery_segments=(
            FinalizedRecipeSegment(name="initial", ordered_step_names=(ordered[0],)),
            FinalizedRecipeSegment(
                name="next",
                ordered_step_names=(ordered[1],),
                checkpoint_sources=(ordered[0],),
            ),
            FinalizedRecipeSegment(
                name="remaining",
                ordered_step_names=ordered[2:],
                checkpoint_sources=(ordered[0], ordered[1]),
            ),
        ),
    )
    persisted = load_recipe_artifact(
        tool_ctx.temp_dir,
        kitchen_id=tool_ctx.kitchen_id,
        identity=state.artifact_generation,
    )
    persisted["finalized_recipe_projection"] = _finalized_projection_payload(projection)
    source_payload = dict(persisted)
    source_payload.pop("finalized_recipe_projection")
    _compile_inputs, compile_key = _normalized_recipe_compile_identity(
        source_payload,
        recipe_name=state.recipe_name,
        finalized_projection=projection,
        flow_generation=state.flow_generation,
    )
    artifact = persist_recipe_artifact(
        tool_ctx.temp_dir,
        kitchen_id=tool_ctx.kitchen_id,
        producer_tool=state.artifact_generation.producer_tool,
        recipe_name=state.recipe_name,
        payload=persisted,
        flow_generation=state.flow_generation,
    )
    segmented = replace(
        state,
        artifact_generation=artifact,
        generation_store_key=compile_key,
        finalized_projection=projection,
    )
    tool_ctx.recipe_initialization_state = segmented
    return segmented, persisted


@pytest.mark.anyio
async def test_checkpoint_delivery_reads_ready_exact_durable_artifact(
    tool_ctx_ready_recipe,
) -> None:
    state, persisted = _install_segmented_ready(tool_ctx_ready_recipe)

    prepared = prepare_recipe_segment_delivery(
        tool_ctx_ready_recipe.tool_ctx,
        "scope",
    )

    assert prepared is not None
    assert prepared.success_carrier["source_step"] == "scope"
    assert [body["step"] for body in prepared.success_carrier["bodies"]] == ["select_directions"]
    assert prepared.success_carrier["recipe_pull"] == state.artifact_generation.pull_identity()
    assert (
        prepared.success_carrier["bodies"][0]["body"]
        == extract_recipe_step_bodies(
            persisted,
            ("select_directions",),
        )[0][1]
    )
    assert persisted["content"]


@pytest.mark.parametrize(
    ("mismatch", "message"),
    [
        ("compile", "compile identity"),
        ("projection", "finalized projection"),
        ("flow", "flow generation"),
        ("execution", "execution credential"),
    ],
)
@pytest.mark.anyio
async def test_checkpoint_delivery_rejects_ready_identity_mismatch(
    tool_ctx_ready_recipe,
    monkeypatch: pytest.MonkeyPatch,
    mismatch: str,
    message: str,
) -> None:
    state, persisted = _install_segmented_ready(tool_ctx_ready_recipe)
    if mismatch == "compile":
        tool_ctx_ready_recipe.tool_ctx.recipe_initialization_state = replace(
            state,
            generation_store_key="sha256:" + ("0" * 64),
        )
    else:
        altered = dict(persisted)
        if mismatch == "projection":
            altered["finalized_recipe_projection"] = {}
        elif mismatch == "flow":
            altered["recipe_flow"] = {}
        else:
            altered[RECIPE_EXECUTION_CREDENTIAL_WIRE_KEY] = {}
        monkeypatch.setattr(
            segment_delivery_module,
            "load_recipe_artifact",
            lambda *_args, **_kwargs: altered,
        )

    with pytest.raises(RecipeSegmentDeliveryError, match=message):
        prepare_recipe_segment_delivery(tool_ctx_ready_recipe.tool_ctx, "scope")


@pytest.mark.anyio
async def test_checkpoint_delivery_is_noop_without_ready_mapping(
    tool_ctx_ready_recipe,
) -> None:
    state, _persisted = _install_segmented_ready(tool_ctx_ready_recipe)
    assert prepare_recipe_segment_delivery(tool_ctx_ready_recipe.tool_ctx, "unmapped") is None

    tool_ctx_ready_recipe.tool_ctx.recipe_initialization_state = NoActiveRecipe()
    assert prepare_recipe_segment_delivery(tool_ctx_ready_recipe.tool_ctx, "scope") is None
    tool_ctx_ready_recipe.tool_ctx.recipe_initialization_state = state


@pytest.mark.anyio
async def test_checkpoint_delivery_survives_ready_generation_lru_eviction(
    tool_ctx_ready_recipe,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_store = generation_module.get_recipe_generation_store()
    original_state = tool_ctx_ready_recipe.tool_ctx.recipe_initialization_state
    assert isinstance(original_state, ReadyRecipe)
    base_record = original_store.lookup_compile(
        original_state.kitchen_id,
        original_state.generation_store_key,
    )
    assert base_record is not None
    state, _persisted = _install_segmented_ready(tool_ctx_ready_recipe)
    ready_record = replace(
        base_record,
        normalized_compile_key=state.generation_store_key,
        finalized_projection=state.finalized_projection,
        surface_bindings={"open_kitchen": state.artifact_generation},
    )
    store = RecipeGenerationStore(max_entries=8)
    monkeypatch.setattr(generation_module, "_RECIPE_GENERATION_STORE", store)
    store.put(ready_record)
    for index in range(9):
        store.put(
            replace(
                ready_record,
                kitchen_id=f"unrelated-kitchen-{index}",
                normalized_compile_key=f"unrelated-compile-{index}",
                surface_bindings={},
            )
        )
    assert store.lookup_compile(state.kitchen_id, state.generation_store_key) is None

    prepared = prepare_recipe_segment_delivery(tool_ctx_ready_recipe.tool_ctx, "scope")

    assert prepared is not None
    assert prepared.success_carrier["recipe_pull"] == state.artifact_generation.pull_identity()
