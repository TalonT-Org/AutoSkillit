"""Receipt commit, lifecycle-transition close-out, and response-backstop
enforcement for a prepared ``FinalizedRecipeResponse``.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from autoskillit.config import OutputBudgetConfig
from autoskillit.core import (
    RecipeDeliveryMode,
    RecipeExecutionId,
    get_logger,
)
from autoskillit.pipeline import (
    KITCHEN_EFFECT_RECIPE_SERVING as _RECIPE_SERVING,
)
from autoskillit.pipeline import (
    InitializingRecipe,
    KitchenEffectPhase,
    ReadyRecipe,
    confirm_kitchen_effect,
    mark_kitchen_effect_ambiguous,
)
from autoskillit.server.recipe._recipe_artifact import _qualified_sha256
from autoskillit.server.recipe._recipe_execution import (
    install_recipe_execution,
    prepare_recipe_execution,
)
from autoskillit.server.recipe._recipe_initialization import stage_recipe_initialization
from autoskillit.server.response._response_budget import enforce_response_budget

if TYPE_CHECKING:
    from autoskillit.pipeline import ToolContext
    from autoskillit.server.recipe._recipe_delivery._response import FinalizedRecipeResponse


def complete_finalized_recipe_response(
    finalized: FinalizedRecipeResponse,
    enforced: Any,
    *,
    now_unix: int | None = None,
) -> Any:
    """Commit receipt and lifecycle state only for exact enforced response bytes."""
    handle = finalized.receipt_handle
    ledger = finalized.receipt_ledger
    parsed: dict[str, Any] | None = None
    prepared_execution: Any = None
    previous_initialization_state: Any = None
    transition_token = finalized.kitchen_transition_token
    if enforced == finalized.rendered and transition_token is not None:
        transition_owned = False
        if (
            finalized.tool_ctx is not None
            and hasattr(finalized.tool_ctx, "kitchen_transition_lock")
            and hasattr(finalized.tool_ctx, "kitchen_open_state")
        ):
            with finalized.tool_ctx.kitchen_transition_lock:
                state = finalized.tool_ctx.kitchen_open_state
                transition_owned = state.operation_id == transition_token.operation_id and any(
                    effect.name == _RECIPE_SERVING
                    and effect.effect_id == transition_token.effect_id
                    for effect in state.effects
                )
        if not transition_owned:
            enforced = json.dumps(
                {
                    "success": False,
                    "error": "kitchen_transition_ownership_mismatch",
                },
                separators=(",", ":"),
            )
    if enforced == finalized.rendered and finalized.initialization_activating:
        required_values = (
            finalized.tool_ctx,
            finalized.recipe_name,
            finalized.artifact_generation,
            finalized.flow_generation,
            finalized.execution_snapshot,
            finalized.normalized_compile_key,
            finalized.initialization_id,
        )
        if any(value is None or value == "" for value in required_values):
            enforced = json.dumps(
                {"success": False, "error": "recipe_initialization_identity_missing"},
                separators=(",", ":"),
            )
        else:
            assert finalized.tool_ctx is not None
            assert finalized.execution_snapshot is not None
            try:
                candidate = (
                    json.loads(finalized.rendered)
                    if finalized.decision.mode is not RecipeDeliveryMode.ATTESTED_INLINE
                    else {"success": True}
                )
            except json.JSONDecodeError:
                candidate = {"success": False}
            if not isinstance(candidate, dict) or candidate.get("success") is False:
                enforced = json.dumps(
                    {"success": False, "error": "recipe_initialization_failed"},
                    separators=(",", ":"),
                )
            else:
                parsed = candidate
    if enforced == finalized.rendered and finalized.initialization_activating:
        assert finalized.tool_ctx is not None
        assert finalized.recipe_name is not None
        assert finalized.artifact_generation is not None
        assert finalized.flow_generation is not None
        assert finalized.execution_snapshot is not None
        assert finalized.finalized_projection is not None
        assert finalized.normalized_compile_key is not None
        assert finalized.initialization_id is not None
        assert parsed is not None
        with finalized.tool_ctx.recipe_execution_lock:
            previous_initialization_state = finalized.tool_ctx.recipe_initialization_state
        try:
            stage_recipe_initialization(
                finalized.tool_ctx,
                recipe_name=finalized.recipe_name,
                artifact_generation=finalized.artifact_generation,
                flow_generation=finalized.flow_generation,
                initialization_id=finalized.initialization_id,
                staged_snapshot=finalized.execution_snapshot,
                requirements=(
                    finalized.initialization_requirements
                    if parsed.get("delivery_bound_spill") is True
                    else ()
                ),
                generation_store_key=finalized.normalized_compile_key,
                finalized_projection=finalized.finalized_projection,
            )
            if parsed.get("delivery_bound_spill") is not True:
                prepared_execution = prepare_recipe_execution(
                    finalized.tool_ctx,
                    snapshot=finalized.execution_snapshot,
                )
                install_recipe_execution(
                    finalized.tool_ctx,
                    prepared_execution=prepared_execution,
                    completion_receipt=_qualified_sha256(
                        (
                            finalized.initialization_id
                            + finalized.artifact_generation.payload_sha256
                        ).encode("utf-8")
                    ),
                )
        except Exception:
            with finalized.tool_ctx.recipe_execution_lock:
                current_state = finalized.tool_ctx.recipe_initialization_state
                if current_state is not previous_initialization_state and isinstance(
                    current_state, InitializingRecipe
                ):
                    finalized.tool_ctx.audit_admission_ledger.retire_installation(
                        recipe_execution_id=RecipeExecutionId(
                            current_state.staged_snapshot.execution_id
                        ),
                        installation_version=current_state.installation_version,
                    )
                elif current_state is not previous_initialization_state and isinstance(
                    current_state, ReadyRecipe
                ):
                    finalized.tool_ctx.audit_admission_ledger.retire_installation(
                        recipe_execution_id=RecipeExecutionId(
                            current_state.installed_execution.snapshot.execution_id
                        ),
                        installation_version=(
                            current_state.installed_execution.installation_version
                        ),
                    )
                finalized.tool_ctx.recipe_initialization_state = previous_initialization_state
            get_logger(__name__).error(
                "recipe execution snapshot installation failed",
                initialization_id=finalized.initialization_id,
                exc_info=True,
            )
            enforced = json.dumps(
                {
                    "success": False,
                    "error": "recipe_execution_install_failed",
                },
                separators=(",", ":"),
            )
    if enforced == finalized.rendered and handle is not None:
        try:
            receipt_committed = ledger is not None and ledger.commit(
                handle,
                now_unix=int(time.time()) if now_unix is None else now_unix,
            )
        except Exception:
            receipt_committed = False
            get_logger(__name__).error(
                "recipe delivery receipt commit failed",
                exc_info=True,
            )
        if not receipt_committed:
            if finalized.initialization_activating:
                assert finalized.tool_ctx is not None
                with finalized.tool_ctx.recipe_execution_lock:
                    finalized.tool_ctx.recipe_initialization_state = previous_initialization_state
            enforced = json.dumps(
                {"success": False, "error": "recipe_delivery_receipt_commit_failed"},
                separators=(",", ":"),
            )
        else:
            handle = None
    if handle is not None and (ledger is None or not ledger.abort(handle)):
        enforced = json.dumps(
            {"success": False, "error": "recipe_delivery_receipt_abort_failed"},
            separators=(",", ":"),
        )
    _complete_kitchen_serving_transition(finalized, enforced)
    return enforced


def _complete_kitchen_serving_transition(
    finalized: FinalizedRecipeResponse,
    enforced: Any,
) -> None:
    """Close the owned serving effect at the response-enforcement boundary."""
    transition_token = finalized.kitchen_transition_token
    tool_ctx = finalized.tool_ctx
    if transition_token is None or tool_ctx is None:
        return
    with tool_ctx.kitchen_transition_lock:
        state = tool_ctx.kitchen_open_state
        if state.operation_id != transition_token.operation_id:
            return
        effect = next(
            (
                candidate
                for candidate in state.effects
                if candidate.name == _RECIPE_SERVING
                and candidate.effect_id == transition_token.effect_id
            ),
            None,
        )
        if effect is None or effect.phase is not KitchenEffectPhase.STARTED:
            return
        if enforced == finalized.rendered:
            state = confirm_kitchen_effect(
                state,
                effect.name,
                receipt=f"response:{effect.effect_id}",
            )
        else:
            state = mark_kitchen_effect_ambiguous(
                state,
                effect.name,
                evidence="finalized recipe response changed during enforcement",
            )
        tool_ctx.kitchen_open_state = state


def enforce_recipe_resource_response(
    finalized: FinalizedRecipeResponse,
    *,
    tool_ctx: ToolContext,
) -> str:
    """Apply the ordinary response backstop and complete the receipt transaction."""
    configured_budget = getattr(tool_ctx.config, "output_budget", None)
    output_budget = (
        configured_budget
        if isinstance(configured_budget, OutputBudgetConfig)
        else OutputBudgetConfig()
    )
    temp_dir = getattr(tool_ctx, "temp_dir", None)
    enforced = enforce_response_budget(
        finalized.rendered,
        tool_name="get_recipe",
        artifact_dir=(
            temp_dir / "responses" / "get_recipe" if isinstance(temp_dir, Path) else None
        ),
        config=output_budget,
        selected_result_token_limit=finalized.decision.selected_result_token_limit,
    )
    completed = complete_finalized_recipe_response(finalized, enforced)
    if isinstance(completed, str):
        return completed
    return json.dumps(completed, ensure_ascii=False, separators=(",", ":"))
