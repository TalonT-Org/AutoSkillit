"""Server translation and post-enforcement commits for recipe initialization."""

from __future__ import annotations

import hashlib
import json
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from autoskillit.core import (
    RECIPE_EXECUTION_CREDENTIAL_WIRE_KEY,
    RecipeArtifactGeneration,
    RecipeExecutionCredential,
    RecipeExecutionSnapshot,
    RecipeFlowGeneration,
    ToolInitializationOperation,
    build_recipe_execution_credential,
    get_logger,
    get_tool_def,
)
from autoskillit.pipeline import (
    InitializingRecipe,
    ReadyRecipe,
    RecipeInitializationRequirement,
    initialization_is_complete,
    record_initialization_page,
    start_recipe_initialization,
)
from autoskillit.server._recipe_execution import (
    RecipeExecutionAdmissionError,
    install_recipe_execution,
    prepare_recipe_execution,
)
from autoskillit.server._state import _get_ctx_or_none

if TYPE_CHECKING:
    from autoskillit.pipeline import ToolContext

logger = get_logger(__name__)

__all__ = [
    "FinalizedRecipeInitializationResponse",
    "FinalizedRecipeSectionResponse",
    "admit_registered_tool_during_initialization",
    "build_completion_response",
    "complete_initialization_response",
    "complete_section_response",
    "matches_recipe_initialization_requirement",
    "stage_recipe_initialization",
]


def _receipt(initialization_id: str, artifact: RecipeArtifactGeneration) -> str:
    material = json.dumps(
        {
            "artifact": artifact.pull_identity(),
            "initialization_id": initialization_id,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return (
        "sha256:"
        + hashlib.sha256(b"autoskillit.recipe-initialization-receipt.v1\0" + material).hexdigest()
    )


@dataclass(frozen=True, slots=True)
class FinalizedRecipeSectionResponse:
    """A rendered page whose lifecycle credit awaits response enforcement."""

    rendered: str
    tool_ctx: ToolContext
    initialization_id: str
    artifact_generation: RecipeArtifactGeneration
    section: str
    page_plan_sha256: str
    part: int


@dataclass(frozen=True, slots=True)
class FinalizedRecipeInitializationResponse:
    """A completion receipt whose READY transition awaits enforcement."""

    rendered: str
    tool_ctx: ToolContext
    initialization_id: str
    artifact_generation: RecipeArtifactGeneration
    staged_snapshot: RecipeExecutionSnapshot
    completion_receipt: str


def stage_recipe_initialization(
    tool_ctx: ToolContext,
    *,
    recipe_name: str,
    artifact_generation: RecipeArtifactGeneration,
    flow_generation: RecipeFlowGeneration,
    initialization_id: str,
    staged_snapshot: RecipeExecutionSnapshot,
    requirements: tuple[RecipeInitializationRequirement, ...],
    generation_store_key: str,
) -> InitializingRecipe:
    """Replace all prior recipe authority with one immutable INITIALIZING state."""
    candidate = start_recipe_initialization(
        kitchen_id=tool_ctx.kitchen_id,
        recipe_name=recipe_name,
        artifact_generation=artifact_generation,
        flow_generation=flow_generation,
        initialization_id=initialization_id,
        staged_snapshot=staged_snapshot,
        requirements=requirements,
        generation_store_key=generation_store_key,
    )
    with tool_ctx.recipe_execution_lock:
        tool_ctx.recipe_initialization_state = candidate
    return candidate


def admit_registered_tool_during_initialization(tool_name: str) -> str | None:
    """Deny execution/mutation before any registered handler side effect."""
    tool_ctx = _get_ctx_or_none()
    if tool_ctx is None:
        return None
    with tool_ctx.recipe_execution_lock:
        state = tool_ctx.recipe_initialization_state
    if not isinstance(state, InitializingRecipe):
        return None
    definition = get_tool_def(tool_name)
    if definition is None:
        return json.dumps(
            {
                "success": False,
                "error": "recipe_initialization_unknown_operation",
            },
            separators=(",", ":"),
        )
    if definition.initialization_operation in {
        ToolInitializationOperation.RECOVERY,
        ToolInitializationOperation.INSPECTION,
        ToolInitializationOperation.LIFECYCLE_CONTROL,
    }:
        return None
    return json.dumps(
        {
            "success": False,
            "error": "recipe_initialization_incomplete",
            "initialization_id": state.initialization_id,
            "recipe_name": state.recipe_name,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def matches_recipe_initialization_requirement(
    state: object,
    *,
    initialization_id: str,
    artifact_generation: RecipeArtifactGeneration,
    section: str,
    page_plan_sha256: str,
) -> bool:
    """Return whether a page belongs to the active immutable initialization."""
    return (
        isinstance(state, InitializingRecipe)
        and state.initialization_id == initialization_id
        and state.artifact_generation == artifact_generation
        and any(
            requirement.section == section and requirement.page_plan_sha256 == page_plan_sha256
            for requirement in state.requirements
        )
    )


def complete_section_response(
    finalized: FinalizedRecipeSectionResponse,
    enforced: Any,
) -> Any:
    """Credit a page only when the exact rendered bytes survived enforcement."""
    if enforced != finalized.rendered:
        return enforced
    tool_ctx = finalized.tool_ctx
    with tool_ctx.recipe_execution_lock:
        state = tool_ctx.recipe_initialization_state
        if (
            not isinstance(state, InitializingRecipe)
            or state.initialization_id != finalized.initialization_id
            or state.artifact_generation != finalized.artifact_generation
        ):
            return json.dumps(
                {"success": False, "error": "recipe_initialization_stale"},
                separators=(",", ":"),
            )
        try:
            tool_ctx.recipe_initialization_state = record_initialization_page(
                state,
                initialization_id=finalized.initialization_id,
                section=finalized.section,
                page_plan_sha256=finalized.page_plan_sha256,
                part=finalized.part,
            )
        except ValueError:
            return json.dumps(
                {"success": False, "error": "recipe_initialization_page_rejected"},
                separators=(",", ":"),
            )
    return enforced


def complete_initialization_response(
    finalized: FinalizedRecipeInitializationResponse,
    enforced: Any,
) -> Any:
    """Install the staged snapshot and READY state after exact enforcement."""
    if enforced != finalized.rendered:
        _budget_error = ""
        if isinstance(enforced, str):
            with suppress(ValueError, TypeError):
                _parsed = json.loads(enforced)
                if isinstance(_parsed, dict):
                    _budget_error = str(_parsed.get("error", ""))
        logger.warning(
            "recipe_initialization_receipt_altered",
            initialization_id=finalized.initialization_id,
            response_budget_error=_budget_error,
        )
        return json.dumps(
            {
                "success": False,
                "error": "recipe_initialization_receipt_altered",
                "initialization_id": finalized.initialization_id,
                "response_budget_error": _budget_error,
                "user_visible_message": (
                    "The completion receipt was rewritten by the response boundary, "
                    "so no recipe execution was installed and the recipe is still "
                    "INITIALIZING. This is a response-budget failure, not a transient "
                    "one: the receipt for this recipe does not fit the active "
                    "delivery bound. Close the kitchen and re-open with a smaller "
                    "recipe, or raise output_budget.response_max_bytes."
                ),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
    tool_ctx = finalized.tool_ctx
    try:
        prepared = prepare_recipe_execution(
            tool_ctx,
            snapshot=finalized.staged_snapshot,
        )
    except Exception:
        logger.error("recipe execution preparation failed", exc_info=True)
        return json.dumps(
            {"success": False, "error": "recipe_execution_install_failed"},
            separators=(",", ":"),
        )
    with tool_ctx.recipe_execution_lock:
        state = tool_ctx.recipe_initialization_state
        if (
            not isinstance(state, InitializingRecipe)
            or state.initialization_id != finalized.initialization_id
            or state.artifact_generation != finalized.artifact_generation
            or not initialization_is_complete(state)
        ):
            return json.dumps(
                {"success": False, "error": "recipe_initialization_incomplete"},
                separators=(",", ":"),
            )
        try:
            install_recipe_execution(
                tool_ctx,
                prepared_execution=prepared,
                completion_receipt=finalized.completion_receipt,
            )
        except RecipeExecutionAdmissionError:
            return json.dumps(
                {"success": False, "error": "recipe_execution_install_failed"},
                separators=(",", ":"),
            )
    return enforced


def _render_completion_receipt(
    *,
    initialization_id: str,
    completion_receipt: str,
    recipe_name: str,
    artifact_generation: RecipeArtifactGeneration,
    flow_generation: RecipeFlowGeneration,
    credential: RecipeExecutionCredential,
) -> str:
    return json.dumps(
        {
            "success": True,
            "initialization_id": initialization_id,
            "completion_receipt": completion_receipt,
            "recipe_name": recipe_name,
            "recipe_pull": artifact_generation.pull_identity(),
            "recipe_flow": flow_generation.identity(),
            RECIPE_EXECUTION_CREDENTIAL_WIRE_KEY: credential.as_wire_block(),
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def build_completion_response(
    tool_ctx: ToolContext,
    initialization_id: str,
) -> FinalizedRecipeInitializationResponse | str:
    """Validate server-owned completion state and build its exact receipt."""
    with tool_ctx.recipe_execution_lock:
        state = tool_ctx.recipe_initialization_state
        if isinstance(state, ReadyRecipe):
            if state.initialization_id != initialization_id:
                return json.dumps(
                    {"success": False, "error": "recipe_initialization_stale"},
                    separators=(",", ":"),
                )
            rendered = _render_completion_receipt(
                initialization_id=initialization_id,
                completion_receipt=state.completion_receipt,
                recipe_name=state.recipe_name,
                artifact_generation=state.artifact_generation,
                flow_generation=state.flow_generation,
                credential=build_recipe_execution_credential(state.installed_execution.snapshot),
            )
            return rendered
        if (
            not isinstance(state, InitializingRecipe)
            or state.initialization_id != initialization_id
            or not initialization_is_complete(state)
        ):
            return json.dumps(
                {"success": False, "error": "recipe_initialization_incomplete"},
                separators=(",", ":"),
            )
        completion_receipt = _receipt(initialization_id, state.artifact_generation)
        rendered = _render_completion_receipt(
            initialization_id=initialization_id,
            completion_receipt=completion_receipt,
            recipe_name=state.recipe_name,
            artifact_generation=state.artifact_generation,
            flow_generation=state.flow_generation,
            credential=build_recipe_execution_credential(state.staged_snapshot),
        )
        return FinalizedRecipeInitializationResponse(
            rendered=rendered,
            tool_ctx=tool_ctx,
            initialization_id=initialization_id,
            artifact_generation=state.artifact_generation,
            staged_snapshot=state.staged_snapshot,
            completion_receipt=completion_receipt,
        )
