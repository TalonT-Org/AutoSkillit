"""Canonical startup and checkpoint carriers for segmented recipe delivery."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from autoskillit.core import (
    RECIPE_EXECUTION_CREDENTIAL_WIRE_KEY,
    FinalizedRecipeProjection,
    RecipeArtifactGeneration,
    RecipeExecutionSnapshot,
    RecipeFlowEdge,
    build_recipe_execution_credential,
    get_tool_def,
    load_yaml,
)
from autoskillit.pipeline import ReadyRecipe
from autoskillit.recipe import edge_routes_success
from autoskillit.server._recipe_artifact import (
    _finalized_projection_payload,
    _normalized_recipe_compile_identity,
    extract_recipe_step_bodies,
    load_recipe_artifact,
)

if TYPE_CHECKING:
    from autoskillit.pipeline import ToolContext

RECIPE_SEGMENT_MAX_BYTES = 10_000
_SEGMENTED_STARTUP_SURFACES = frozenset({"open_kitchen", "open_kitchen_deferred_recall"})
_SEGMENTED_STARTUP_EXCLUDED_FIELDS = frozenset(
    {
        "content",
        "deferred_guards",
        "delivery_bound_spill",
        "diagram",
        "effects",
        "flow_records",
        "hook_warning",
        "ingredients_table",
        "kitchen_rules",
        "orchestration_rules",
        "post_prune_routing_edges",
        "post_prune_step_names",
        RECIPE_EXECUTION_CREDENTIAL_WIRE_KEY,
        "recovery",
        "required_sections",
        "suggestions",
        "stop_step_semantics",
    }
)


class RecipeSegmentDeliveryError(RuntimeError):
    """A mapped segment carrier could not be verified or admitted."""


@dataclass(frozen=True, slots=True)
class PreparedRecipeSegmentDelivery:
    """Pre-effect carriers for one mapped checkpoint source."""

    step_name: str
    success_carrier: dict[str, Any]
    recovery_carrier: dict[str, Any]


def _qualified_sha256(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def _serialized_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _admit_carrier(carrier: dict[str, Any]) -> dict[str, Any]:
    if len(_serialized_bytes(carrier)) >= RECIPE_SEGMENT_MAX_BYTES:
        raise RecipeSegmentDeliveryError("recipe segment carrier exceeds 10,000 UTF-8 bytes")
    return carrier


def build_post_effect_segment_failure(
    carrier: dict[str, Any],
    *,
    tool_name: str,
) -> dict[str, Any]:
    """Build the bounded no-repeat fallback for a completed checkpoint effect."""
    result = {
        "success": False,
        "subtype": "recipe_segment_post_effect_delivery_failure",
        "error": "Response shaping failed after the operation ran; do not repeat it.",
        "tool_name": tool_name,
        "step_name": carrier.get("source_step", ""),
        "operation_already_ran": True,
        "do_not_repeat": True,
        "recipe_segment": carrier,
    }
    if len(_serialized_bytes(result)) >= RECIPE_SEGMENT_MAX_BYTES:
        raise RecipeSegmentDeliveryError(
            "recipe segment post-effect failure exceeds 10,000 UTF-8 bytes"
        )
    return result


def _body_records(
    persisted: dict[str, Any],
    ordered_step_names: tuple[str, ...],
    execution_snapshot: RecipeExecutionSnapshot,
) -> list[dict[str, str]]:
    bodies = extract_recipe_step_bodies(persisted, ordered_step_names)
    if tuple(name for name, _body in bodies) != ordered_step_names:
        raise RecipeSegmentDeliveryError("persisted recipe is missing a finalized step body")
    template_digests = execution_snapshot.template_digests
    return [
        {
            "step": step_name,
            "body": body,
            "body_sha256": _qualified_sha256(body.encode("utf-8")),
            **(
                {"invocation_template_digest": template_digests[step_name]}
                if step_name in template_digests
                else {}
            ),
        }
        for step_name, body in bodies
    ]


def _segment_execution_credential(
    execution_snapshot: RecipeExecutionSnapshot,
    ordered_step_names: tuple[str, ...],
) -> dict[str, Any]:
    credential = build_recipe_execution_credential(execution_snapshot).as_wire_block()
    digests = credential["invocation_template_digests"]
    assert isinstance(digests, dict)
    credential["invocation_template_digests"] = {
        step_name: digests[step_name] for step_name in ordered_step_names if step_name in digests
    }
    return credential


def _segment_index(projection: FinalizedRecipeProjection) -> dict[str, int]:
    return {
        step_name: index
        for index, segment in enumerate(projection.delivery_segments)
        for step_name in segment.ordered_step_names
    }


def _delivery_tool_name(projection: FinalizedRecipeProjection, source_step: str) -> str | None:
    invocation = projection.binding_projection.invocations.get(source_step)
    if invocation is None:
        return None
    return (
        "complete_run_skill_result"
        if invocation.tool_name == "run_skill"
        else invocation.tool_name
    )


def _edge_routes_success(
    projection: FinalizedRecipeProjection,
    edge: RecipeFlowEdge,
) -> bool:
    tool_name = _delivery_tool_name(projection, edge.source)
    definition = get_tool_def(tool_name) if tool_name is not None else None
    return edge_routes_success(
        tool_name or "",
        edge,
        automatic=definition is not None and definition.automatic_recipe_delivery,
        recovery=definition is not None and definition.recovery_recipe_delivery,
    )


def _target_segment_indices(
    projection: FinalizedRecipeProjection,
    source_step: str,
    *,
    success: bool,
) -> tuple[int, ...]:
    indices = _segment_index(projection)
    source_index = indices.get(source_step)
    if source_index is None:
        return ()
    selected: list[int] = []
    for edge in projection.ordered_flow_edges:
        target_index = indices.get(edge.target)
        if (
            edge.source != source_step
            or target_index is None
            or target_index <= source_index
            or (_edge_routes_success(projection, edge) is not success)
        ):
            continue
        if target_index not in selected:
            selected.append(target_index)
    return tuple(selected)


def _pull_requests(
    step_names: tuple[str, ...],
) -> list[dict[str, str | int]]:
    return [{"section": step_name, "part": 0} for step_name in step_names]


def _manual_closure(
    projection: FinalizedRecipeProjection,
    target_steps: tuple[str, ...],
) -> tuple[str, ...]:
    ordered = projection.ordered_step_names
    known = frozenset(ordered)
    automatic_sources = frozenset(
        source
        for segment in projection.delivery_segments
        for source in segment.checkpoint_sources
        if (tool_name := _delivery_tool_name(projection, source)) is not None
        if (definition := get_tool_def(tool_name)) is not None
        and definition.automatic_recipe_delivery
    )
    visited: set[str] = set()
    pending = list(target_steps)
    while pending:
        step_name = pending.pop()
        if step_name in visited or step_name not in known:
            continue
        visited.add(step_name)
        if step_name in automatic_sources and step_name not in target_steps:
            continue
        pending.extend(
            edge.target
            for edge in projection.ordered_flow_edges
            if edge.source == step_name and edge.target in known
        )
    return tuple(step_name for step_name in ordered if step_name in visited)


def _route_pull_closures(
    projection: FinalizedRecipeProjection,
    delivered_bodies: list[dict[str, str]],
    generation: RecipeArtifactGeneration,
) -> list[dict[str, Any]]:
    delivered_names = frozenset(body["step"] for body in delivered_bodies)
    closures: list[dict[str, Any]] = []
    for body in delivered_bodies:
        parsed = load_yaml(body["body"])
        step_value = parsed.get(body["step"]) if isinstance(parsed, dict) else None
        if not isinstance(step_value, dict) or step_value.get("action") != "route":
            continue
        targets = tuple(
            edge.target
            for edge in projection.ordered_flow_edges
            if edge.source == body["step"] and edge.target not in delivered_names
        )
        closure = _manual_closure(projection, targets)
        if closure:
            closures.append(
                {
                    "source_step": body["step"],
                    "steps": list(closure),
                    "pull_requests": _pull_requests(closure),
                }
            )
    return closures


def validate_segment_delivery_projection(projection: FinalizedRecipeProjection) -> None:
    """Validate registry capabilities for every finalized forward crossing."""
    if not projection.delivery_segments:
        return
    indices = _segment_index(projection)
    invocations = projection.binding_projection.invocations
    for edge in projection.ordered_flow_edges:
        source_index = indices.get(edge.source)
        target_index = indices.get(edge.target)
        if source_index is None or target_index is None or source_index >= target_index:
            continue
        invocation = invocations.get(edge.source)
        if invocation is None:
            continue
        tool_name = _delivery_tool_name(projection, edge.source)
        definition = get_tool_def(tool_name) if tool_name is not None else None
        if definition is None:
            raise RecipeSegmentDeliveryError(
                f"delivery checkpoint tool {invocation.tool_name!r} is not registered"
            )
        capability = (
            definition.automatic_recipe_delivery
            if _edge_routes_success(projection, edge)
            else definition.recovery_recipe_delivery
        )
        if not capability:
            raise RecipeSegmentDeliveryError(
                f"delivery checkpoint tool {invocation.tool_name!r} lacks "
                f"{edge.edge_type!r} carrier capability"
            )


def build_startup_recipe_segment(
    persisted: dict[str, Any],
    *,
    projection: FinalizedRecipeProjection,
    generation: RecipeArtifactGeneration,
    execution_snapshot: RecipeExecutionSnapshot,
) -> dict[str, Any]:
    """Build the compact overview and initial-segment bodies."""
    validate_segment_delivery_projection(projection)
    if not projection.delivery_segments:
        raise RecipeSegmentDeliveryError("recipe has no finalized delivery segments")
    initial = projection.delivery_segments[0]
    bodies = _body_records(persisted, initial.ordered_step_names, execution_snapshot)
    return _admit_carrier(
        {
            "kind": "startup",
            "segment": {"index": 0, "name": initial.name},
            "overview": [
                {
                    "index": index,
                    "name": segment.name,
                    "steps": list(segment.ordered_step_names),
                }
                for index, segment in enumerate(projection.delivery_segments)
            ],
            "bodies": bodies,
            RECIPE_EXECUTION_CREDENTIAL_WIRE_KEY: _segment_execution_credential(
                execution_snapshot,
                initial.ordered_step_names,
            ),
            "pull_closures": _route_pull_closures(projection, bodies, generation),
            "recipe_pull": generation.pull_identity(),
        }
    )


def uses_segmented_startup(
    surface: str,
    projection: FinalizedRecipeProjection,
) -> bool:
    return bool(projection.delivery_segments) and surface in _SEGMENTED_STARTUP_SURFACES


def shape_segmented_startup_payload(
    payload: dict[str, Any],
    persisted: dict[str, Any],
    *,
    surface: str,
    projection: FinalizedRecipeProjection,
    generation: RecipeArtifactGeneration,
    execution_snapshot: RecipeExecutionSnapshot,
) -> dict[str, Any]:
    """Replace a segmented open surface with its admitted compact startup view."""
    if not uses_segmented_startup(surface, projection):
        return payload
    try:
        recipe_segment = build_startup_recipe_segment(
            persisted,
            projection=projection,
            generation=generation,
            execution_snapshot=execution_snapshot,
        )
    except RecipeSegmentDeliveryError:
        raise
    except (TypeError, ValueError) as exc:
        raise RecipeSegmentDeliveryError("recipe_segment_startup_failed") from exc
    compact = {
        key: value
        for key, value in payload.items()
        if key not in _SEGMENTED_STARTUP_EXCLUDED_FIELDS
    }
    compact["recipe_segment"] = recipe_segment
    if len(_serialized_bytes(compact)) >= RECIPE_SEGMENT_MAX_BYTES:
        raise RecipeSegmentDeliveryError("recipe_segment_startup_exceeds_bound")
    return compact


def _checkpoint_carrier(
    persisted: dict[str, Any],
    *,
    ready: ReadyRecipe,
    step_name: str,
    success: bool,
    target_index: int | None = None,
    recovery_target: str | None = None,
) -> dict[str, Any]:
    projection = ready.finalized_projection
    target_indices = (
        (target_index,)
        if target_index is not None
        else _target_segment_indices(projection, step_name, success=success)
    )
    generation = ready.artifact_generation
    if success:
        body_indices = [
            index for index in target_indices if index < len(projection.delivery_segments) - 1
        ]
        while True:
            target_segments = tuple(projection.delivery_segments[index] for index in body_indices)
            target_steps = tuple(
                step for segment in target_segments for step in segment.ordered_step_names
            )
            bodies = _body_records(persisted, target_steps, ready.installed_execution.snapshot)
            pull_closures = _route_pull_closures(projection, bodies, generation)
            manual_roots = tuple(
                projection.delivery_segments[index].ordered_step_names[0]
                for index in target_indices
                if index not in body_indices
            )
            manual_closure = _manual_closure(projection, manual_roots)
            if manual_closure:
                pull_closures.append(
                    {
                        "source_step": step_name,
                        "steps": list(manual_closure),
                        "pull_requests": _pull_requests(manual_closure),
                    }
                )
            carrier = {
                "kind": "success",
                "source_step": step_name,
                "segments": [
                    {"index": index, "name": projection.delivery_segments[index].name}
                    for index in body_indices
                ],
                "bodies": bodies,
                RECIPE_EXECUTION_CREDENTIAL_WIRE_KEY: _segment_execution_credential(
                    ready.installed_execution.snapshot,
                    target_steps,
                ),
                "pull_closures": pull_closures,
                "recipe_pull": generation.pull_identity(),
            }
            try:
                admitted = _admit_carrier(carrier)
                build_post_effect_segment_failure(
                    admitted,
                    tool_name="complete_run_skill_result",
                )
                return admitted
            except RecipeSegmentDeliveryError:
                if not body_indices:
                    raise
                body_indices.pop()

    recovery_targets = (
        (recovery_target,)
        if recovery_target is not None
        else tuple(
            edge.target
            for edge in projection.ordered_flow_edges
            if edge.source == step_name
            and edge.target in projection.ordered_step_names
            and edge.target not in projection.delivery_segments[0].ordered_step_names
        )
    )
    closure = _manual_closure(projection, recovery_targets)
    return _admit_carrier(
        {
            "kind": "recovery",
            "source_step": step_name,
            "target_steps": list(dict.fromkeys(recovery_targets)),
            "pull_closure": list(closure),
            "pull_requests": _pull_requests(closure),
            "recipe_pull": generation.pull_identity(),
        }
    )


def prepare_recipe_segment_delivery(
    tool_ctx: ToolContext,
    step_name: str | None,
) -> PreparedRecipeSegmentDelivery | None:
    """Verify READY's exact durable generation and pre-render mapped carriers."""
    if not step_name:
        return None
    with tool_ctx.recipe_execution_lock:
        state = tool_ctx.recipe_initialization_state
    if not isinstance(state, ReadyRecipe) or not state.finalized_projection.delivery_segments:
        return None
    if not any(
        step_name in segment.checkpoint_sources
        for segment in state.finalized_projection.delivery_segments
    ):
        return None
    artifact_dir = getattr(tool_ctx, "temp_dir", None)
    if not isinstance(artifact_dir, Path):
        raise RecipeSegmentDeliveryError("recipe artifact directory is unavailable")
    persisted = load_recipe_artifact(
        artifact_dir,
        kitchen_id=tool_ctx.kitchen_id,
        identity=state.artifact_generation,
    )
    expected_projection = _finalized_projection_payload(state.finalized_projection)
    if persisted.get("finalized_recipe_projection") != expected_projection:
        raise RecipeSegmentDeliveryError("READY finalized projection differs from artifact")
    if persisted.get("recipe_flow") != state.flow_generation.identity() or persisted.get(
        "flow_records"
    ) != list(state.flow_generation.records):
        raise RecipeSegmentDeliveryError("READY flow generation differs from artifact")
    expected_credential = build_recipe_execution_credential(
        state.installed_execution.snapshot
    ).as_wire_block()
    if persisted.get(RECIPE_EXECUTION_CREDENTIAL_WIRE_KEY) != expected_credential:
        raise RecipeSegmentDeliveryError("READY execution credential differs from artifact")
    _compile_inputs, normalized_key = _normalized_recipe_compile_identity(
        persisted,
        recipe_name=state.recipe_name,
        finalized_projection=state.finalized_projection,
        flow_generation=state.flow_generation,
    )
    if normalized_key != state.generation_store_key:
        raise RecipeSegmentDeliveryError("READY compile identity differs from artifact")
    validate_segment_delivery_projection(state.finalized_projection)
    success_carrier = _checkpoint_carrier(
        persisted,
        ready=state,
        step_name=step_name,
        success=True,
    )
    recovery_carrier = _checkpoint_carrier(
        persisted,
        ready=state,
        step_name=step_name,
        success=False,
    )
    tool_name = _delivery_tool_name(state.finalized_projection, step_name) or "unknown"
    build_post_effect_segment_failure(success_carrier, tool_name=tool_name)
    build_post_effect_segment_failure(recovery_carrier, tool_name=tool_name)
    return PreparedRecipeSegmentDelivery(
        step_name=step_name,
        success_carrier=success_carrier,
        recovery_carrier=recovery_carrier,
    )


def attach_recipe_segment(
    result: dict[str, Any],
    prepared: PreparedRecipeSegmentDelivery | None,
    *,
    success: bool,
) -> dict[str, Any]:
    """Return a fresh result mapping with the selected pre-rendered carrier."""
    if prepared is None:
        return result
    return {
        **result,
        "recipe_segment": (prepared.success_carrier if success else prepared.recovery_carrier),
    }
