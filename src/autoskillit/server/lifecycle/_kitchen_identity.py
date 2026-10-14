"""Sole owner of the kitchen-identity lifecycle edges and activation retirement."""

from pathlib import Path
from uuid import uuid4

from autoskillit.core import get_logger, resolve_kitchen_id
from autoskillit.pipeline import (
    KitchenActivationId,
    KitchenOpenPhase,
    ToolContext,
    closed_kitchen_open_state,
    new_kitchen_open_state,
)

logger = get_logger(__name__)


def establish_kitchen_identity(ctx: ToolContext, *, campaign_id: str | None = None) -> str:
    """Return the lifecycle kitchen id, minting and activating it at most once.

    Callers may already hold the context's reentrant transition lock.
    """
    with ctx.kitchen_transition_lock:
        state = ctx.kitchen_open_state
        if state.phase is KitchenOpenPhase.CLOSED:
            from autoskillit.server.recipe._recipe_generation import (  # circular-break
                activate_kitchen,
            )

            kitchen_id = campaign_id or resolve_kitchen_id()
            if not kitchen_id.strip():
                raise ValueError("kitchen_id must contain non-whitespace characters")
            activation_id = KitchenActivationId(uuid4().hex)
            activate_kitchen(activation_id)
            ctx.kitchen_process_identity = None
            state = new_kitchen_open_state(
                kitchen_id=kitchen_id,
                activation_id=activation_id,
                context_id=state.context_id,
            )
            ctx.kitchen_open_state = state
        return state.kitchen_id


def retire_kitchen_identity(ctx: ToolContext) -> None:
    """Return the lifecycle to CLOSED, then permanently retire its activation."""
    from autoskillit.server.recipe._recipe_artifact import (  # circular-break
        retire_recipe_artifacts,
    )
    from autoskillit.server.recipe.section._lifecycle import (  # circular-break
        notify_kitchen_retired,
    )

    with ctx.kitchen_transition_lock:
        state = ctx.kitchen_open_state
        ctx.kitchen_open_state = closed_kitchen_open_state(context_id=state.context_id)
    activation_id = state.activation_id
    if not isinstance(activation_id, KitchenActivationId):
        return
    if isinstance(ctx.temp_dir, Path):
        try:
            if retire_recipe_artifacts(ctx.temp_dir, activation_id=activation_id):
                return
        except Exception:
            try:
                notify_kitchen_retired(activation_id)
            except Exception:
                logger.warning(
                    "kitchen_activation_retirement_notification_failed",
                    activation_id=activation_id.value,
                    exc_info=True,
                )
            raise
        logger.warning(
            "kitchen_activation_artifact_retirement_failed",
            kitchen_id=state.kitchen_id,
            activation_id=activation_id.value,
        )
    notify_kitchen_retired(activation_id)
