"""Focused contracts for segmented startup and checkpoint delivery."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

import autoskillit.server.recipe._recipe_generation as generation_module
import autoskillit.server.recipe._recipe_segment_delivery as segment_delivery_module
from autoskillit.core import (
    RECIPE_EXECUTION_CREDENTIAL_WIRE_KEY,
    FinalizedRecipeProjection,
    FinalizedRecipeSegment,
    FinalizedRecipeStep,
    RecipeBindingProjection,
)
from autoskillit.pipeline import KitchenActivationId, NoActiveRecipe, ReadyRecipe
from autoskillit.server.recipe._recipe_artifact import (
    _finalized_projection_payload,
    _normalized_recipe_compile_identity,
    extract_recipe_step_bodies,
    load_recipe_artifact,
    persist_recipe_artifact,
)
from autoskillit.server.recipe._recipe_generation import RecipeGenerationStore
from autoskillit.server.recipe._recipe_segment_delivery import (
    RECIPE_SEGMENT_MAX_BYTES,
    RecipeSegmentDeliveryError,
    attach_recipe_segment,
    prepare_recipe_segment_delivery,
    uses_segmented_startup,
)
from autoskillit.server.response._response_budget import (
    RESPONSE_SPILL_METADATA_KEY,
    enforce_response_budget,
)
from tests.server._recipe_fixture_parameters import (
    IMPLEMENTATION_OVERRIDES,
    REVIEW_APPROACH_OVERRIDES,
)

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium]

_IMPLEMENTATION_READY_PARAMETERS = [
    pytest.param(("implementation", "plan", IMPLEMENTATION_OVERRIDES), id="default"),
    pytest.param(
        (
            "implementation",
            "plan",
            REVIEW_APPROACH_OVERRIDES,
        ),
        id="review-approach",
    ),
]


def _projection(*, segmented: bool) -> FinalizedRecipeProjection:
    return FinalizedRecipeProjection(
        binding_projection=RecipeBindingProjection(invocations={}),
        ordered_step_names=("step",),
        entrypoint="step",
        ordered_flow_edges=(),
        ordered_steps=(FinalizedRecipeStep(name="step"),),
        ingredient_names=frozenset(),
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


def test_segmented_startup_preserves_domain_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def reject_startup(*_args, **_kwargs):
        raise RecipeSegmentDeliveryError("persisted recipe is missing a finalized step body")

    monkeypatch.setattr(
        segment_delivery_module,
        "build_startup_recipe_segment",
        reject_startup,
    )

    with pytest.raises(
        RecipeSegmentDeliveryError,
        match="persisted recipe is missing a finalized step body",
    ):
        segment_delivery_module.shape_segmented_startup_payload(
            {},
            {},
            surface="open_kitchen",
            projection=_projection(segmented=True),
            generation=object(),
            execution_snapshot=object(),
        )


def test_carrier_admission_error_identifies_oversized_carrier() -> None:
    carrier = {"kind": "success", "source_step": "scope", "body": "x" * 10_000}
    serialized_size = len(segment_delivery_module._serialized_bytes(carrier))

    with pytest.raises(RecipeSegmentDeliveryError) as exc_info:
        segment_delivery_module._admit_carrier(carrier)

    assert str(exc_info.value) == (
        "recipe segment carrier 'success' from step 'scope' is "
        f"{serialized_size:,} UTF-8 bytes; must be below 10,000"
    )


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
        activation_id=tool_ctx.kitchen_activation_id,
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
        activation_id=tool_ctx.kitchen_activation_id,
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
    assert prepared.success_carrier is not None
    assert prepared.success_carrier["source_step"] == "scope"
    assert [body["step"] for body in prepared.success_carrier["bodies"]] == ["select_directions"]
    credential = prepared.success_carrier[RECIPE_EXECUTION_CREDENTIAL_WIRE_KEY]
    assert set(credential["invocation_template_digests"]) == {"select_directions"}
    assert set(credential["skill_input_shapes"]) == {"select_directions"}
    assert prepared.success_carrier["recipe_pull"] == state.artifact_generation.pull_identity()
    assert (
        prepared.success_carrier["bodies"][0]["body"]
        == extract_recipe_step_bodies(
            persisted,
            ("select_directions",),
        )[0][1]
    )
    assert persisted["content"]


@pytest.mark.anyio
async def test_checkpoint_delivery_sizes_failure_with_actual_tool_name(
    tool_ctx_ready_recipe,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, _persisted = _install_segmented_ready(tool_ctx_ready_recipe)
    invocation = state.finalized_projection.binding_projection.invocations["scope"]
    expected_tool_name = (
        "complete_run_skill_result"
        if invocation.tool_name == "run_skill"
        else invocation.tool_name
    )
    seen_tool_names: list[str] = []
    validated_carriers: list[dict[str, object]] = []
    build_failure = segment_delivery_module.build_post_effect_segment_failure

    def record_tool_name(carrier, *, tool_name: str):
        seen_tool_names.append(tool_name)
        validated_carriers.append(carrier)
        return build_failure(carrier, tool_name=tool_name)

    monkeypatch.setattr(
        segment_delivery_module,
        "build_post_effect_segment_failure",
        record_tool_name,
    )

    prepared = prepare_recipe_segment_delivery(tool_ctx_ready_recipe.tool_ctx, "scope")

    assert prepared is not None
    assert prepared.success_carrier is not None
    assert prepared.success_pull_carrier is not None
    assert prepared.recovery_carrier is not None
    assert seen_tool_names == [expected_tool_name] * 4
    assert validated_carriers == [
        prepared.success_carrier,
        prepared.success_pull_carrier,
        prepared.success_carrier,
        prepared.recovery_carrier,
    ]


@pytest.mark.anyio
async def test_persisted_generation_recomputes_original_compile_identity(
    tool_ctx_ready_recipe,
) -> None:
    tool_ctx = tool_ctx_ready_recipe.tool_ctx
    state = tool_ctx.recipe_initialization_state
    assert isinstance(state, ReadyRecipe)
    persisted = load_recipe_artifact(
        tool_ctx.temp_dir,
        activation_id=tool_ctx.kitchen_activation_id,
        identity=state.artifact_generation,
    )

    _compile_inputs, compile_key = _normalized_recipe_compile_identity(
        persisted,
        recipe_name=state.recipe_name,
        finalized_projection=state.finalized_projection,
        flow_generation=state.flow_generation,
    )

    assert compile_key == state.generation_store_key


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
        tool_ctx_ready_recipe.tool_ctx.kitchen_activation_id,
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
                activation_id=KitchenActivationId(f"unrelated-kitchen-{index}"),
                normalized_compile_key=f"unrelated-compile-{index}",
                surface_bindings={},
            )
        )
    assert (
        store.lookup_compile(
            tool_ctx_ready_recipe.tool_ctx.kitchen_activation_id,
            state.generation_store_key,
        )
        is None
    )

    prepared = prepare_recipe_segment_delivery(tool_ctx_ready_recipe.tool_ctx, "scope")

    assert prepared is not None
    assert prepared.success_carrier is not None
    assert prepared.success_carrier["recipe_pull"] == state.artifact_generation.pull_identity()


@pytest.mark.parametrize("tool_ctx_ready_recipe", _IMPLEMENTATION_READY_PARAMETERS, indirect=True)
@pytest.mark.anyio
async def test_recovery_only_success_carrier_follows_success_crossings(
    tool_ctx_ready_recipe,
) -> None:
    tool_ctx = tool_ctx_ready_recipe.tool_ctx

    close_issue = prepare_recipe_segment_delivery(tool_ctx, "close_issue_no_changes")
    assert close_issue is not None
    assert close_issue.success_carrier is not None
    assert close_issue.success_carrier["kind"] == "recovery"
    assert "register_clone_no_changes" in close_issue.success_carrier["target_steps"]

    claim_issue = prepare_recipe_segment_delivery(tool_ctx, "claim_and_resolve")
    assert claim_issue is not None
    assert claim_issue.success_carrier is None


@pytest.mark.parametrize("tool_ctx_ready_recipe", _IMPLEMENTATION_READY_PARAMETERS, indirect=True)
@pytest.mark.anyio
async def test_success_carrier_degrades_to_pull_only_when_response_would_not_fit(
    tool_ctx_ready_recipe,
    tmp_path: Path,
) -> None:
    tool_ctx = tool_ctx_ready_recipe.tool_ctx
    prepared = prepare_recipe_segment_delivery(tool_ctx, "bind_plan_set")
    assert prepared is not None
    assert prepared.success_carrier is not None
    assert prepared.success_pull_carrier is not None

    base = {
        "success": True,
        "plan_set_authority_path": "/tmp/plan-set-authority.json",
        "base_marker": "retained",
        "auxiliary": "",
    }
    inline_size = len(
        segment_delivery_module._serialized_bytes(
            {**base, "recipe_segment": prepared.success_carrier}
        )
    )
    assert inline_size < RECIPE_SEGMENT_MAX_BYTES
    base["auxiliary"] = "x" * (RECIPE_SEGMENT_MAX_BYTES - inline_size)
    assert (
        len(
            segment_delivery_module._serialized_bytes(
                {**base, "recipe_segment": prepared.success_carrier}
            )
        )
        >= RECIPE_SEGMENT_MAX_BYTES
    )

    attached = attach_recipe_segment(base, prepared, success=True)
    assert attached["recipe_segment"] == prepared.success_pull_carrier
    shaped = enforce_response_budget(
        attached,
        tool_name="bind_plan_set",
        artifact_dir=tmp_path,
        config=tool_ctx.config.output_budget,
    )
    payload = json.loads(shaped) if isinstance(shaped, str) else shaped

    assert payload["success"] is True
    assert payload["recipe_segment"] == prepared.success_pull_carrier
    assert all(payload[key] == value for key, value in base.items())


@pytest.mark.parametrize("tool_ctx_ready_recipe", _IMPLEMENTATION_READY_PARAMETERS, indirect=True)
@pytest.mark.anyio
async def test_pull_only_success_survives_oversized_bind_response_spill(
    tool_ctx_ready_recipe,
    tmp_path: Path,
) -> None:
    tool_ctx = tool_ctx_ready_recipe.tool_ctx
    state = tool_ctx.recipe_initialization_state
    assert isinstance(state, ReadyRecipe)
    prepared = prepare_recipe_segment_delivery(tool_ctx, "bind_plan_set")
    assert prepared is not None
    assert prepared.success_pull_carrier is not None
    base = {
        "success": True,
        "plan_set_state": "sealed",
        "plan_set_authority_path": "/tmp/plan-set-authority.json",
        "plan_set_authority_digest": "sha256:" + ("a" * 64),
        "auxiliary": "spill-sentinel:" + ("x" * 20_000),
    }

    attached = attach_recipe_segment(base, prepared, success=True)
    assert attached["recipe_segment"] == prepared.success_pull_carrier
    shaped = enforce_response_budget(
        attached,
        tool_name="bind_plan_set",
        artifact_dir=tmp_path,
        config=tool_ctx.config.output_budget,
    )
    payload = json.loads(shaped) if isinstance(shaped, str) else shaped

    assert payload.get("subtype") != "recipe_segment_post_effect_delivery_failure"
    assert "do_not_repeat" not in payload
    assert payload["success"] is True
    assert payload["plan_set_authority_path"] == base["plan_set_authority_path"]
    assert payload["recipe_segment"] == prepared.success_pull_carrier
    assert payload["recipe_segment"]["source_step"] == "bind_plan_set"
    assert payload["recipe_segment"]["recipe_pull"] == state.artifact_generation.pull_identity()
    pull_requests = [
        request
        for closure in payload["recipe_segment"]["pull_closures"]
        for request in closure["pull_requests"]
    ]
    assert {"section": "verify", "part": 0} in pull_requests
    assert RESPONSE_SPILL_METADATA_KEY in payload

    metadata = payload[RESPONSE_SPILL_METADATA_KEY]
    spilled_base = json.loads(Path(metadata["artifact_path"]).read_text(encoding="utf-8"))
    assert spilled_base == base
