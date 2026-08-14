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
    build_recipe_execution_credential,
    get_tool_def,
    load_yaml,
)
from autoskillit.pipeline import ReadyRecipe
from autoskillit.server._recipe_artifact import (
    _finalized_projection_payload,
    _normalized_recipe_compile_identity,
    load_recipe_artifact,
)
from autoskillit.server._recipe_section_pagination import extract_recipe_step_bodies

if TYPE_CHECKING:
    from autoskillit.pipeline import ToolContext

RECIPE_SEGMENT_MAX_BYTES = 10_000
_SUCCESS_EDGE_TYPES = frozenset({"success", "result_condition"})


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
            or ((edge.edge_type in _SUCCESS_EDGE_TYPES) is not success)
        ):
            continue
        if target_index not in selected:
            selected.append(target_index)
    return tuple(selected)


def _pull_requests(
    generation: RecipeArtifactGeneration,
    step_names: tuple[str, ...],
) -> list[dict[str, str | int]]:
    pull = generation.pull_identity()
    return [{**pull, "section": step_name, "part": 0} for step_name in step_names]


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
        if source in projection.binding_projection.invocations
        if (
            definition := get_tool_def(projection.binding_projection.invocations[source].tool_name)
        )
        is not None
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
                    "pull_requests": _pull_requests(generation, closure),
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
        definition = get_tool_def(invocation.tool_name)
        if definition is None:
            raise RecipeSegmentDeliveryError(
                f"delivery checkpoint tool {invocation.tool_name!r} is not registered"
            )
        capability = (
            definition.automatic_recipe_delivery
            if edge.edge_type in _SUCCESS_EDGE_TYPES
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


def _checkpoint_carrier(
    persisted: dict[str, Any],
    *,
    ready: ReadyRecipe,
    step_name: str,
    success: bool,
) -> dict[str, Any]:
    projection = ready.finalized_projection
    target_indices = _target_segment_indices(projection, step_name, success=success)
    generation = ready.artifact_generation
    if success:
        target_segments = tuple(projection.delivery_segments[index] for index in target_indices)
        target_steps = tuple(
            step for segment in target_segments for step in segment.ordered_step_names
        )
        bodies = _body_records(persisted, target_steps, ready.installed_execution.snapshot)
        return _admit_carrier(
            {
                "kind": "success",
                "source_step": step_name,
                "segments": [
                    {"index": index, "name": projection.delivery_segments[index].name}
                    for index in target_indices
                ],
                "bodies": bodies,
                RECIPE_EXECUTION_CREDENTIAL_WIRE_KEY: _segment_execution_credential(
                    ready.installed_execution.snapshot,
                    target_steps,
                ),
                "pull_closures": _route_pull_closures(projection, bodies, generation),
                "recipe_pull": generation.pull_identity(),
            }
        )

    recovery_targets = tuple(
        edge.target
        for edge in projection.ordered_flow_edges
        if edge.source == step_name
        and edge.target in projection.ordered_step_names
        and edge.target not in projection.delivery_segments[0].ordered_step_names
    )
    closure = _manual_closure(projection, recovery_targets)
    return _admit_carrier(
        {
            "kind": "recovery",
            "source_step": step_name,
            "target_steps": list(dict.fromkeys(recovery_targets)),
            "pull_closure": list(closure),
            "pull_requests": _pull_requests(generation, closure),
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
    source_payload = dict(persisted)
    source_payload.pop("finalized_recipe_projection", None)
    _compile_inputs, normalized_key = _normalized_recipe_compile_identity(
        source_payload,
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
    invocation = state.finalized_projection.binding_projection.invocations.get(step_name)
    tool_name = invocation.tool_name if invocation is not None else "unknown"
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
