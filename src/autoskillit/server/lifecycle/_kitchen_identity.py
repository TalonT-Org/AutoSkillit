"""Sole owner of the kitchen-identity CLOSED -> INFRASTRUCTURE_READY edge."""

from autoskillit.core import resolve_kitchen_id
from autoskillit.pipeline import (
    KitchenOpenPhase,
    ToolContext,
    new_kitchen_open_state,
)


def establish_kitchen_identity(ctx: ToolContext, *, campaign_id: str | None = None) -> str:
    """Return the lifecycle kitchen id, minting and activating it at most once."""
    with ctx.kitchen_transition_lock:
        state = ctx.kitchen_open_state
        if state.phase is KitchenOpenPhase.CLOSED:
            from autoskillit.server.recipe._recipe_generation import (  # circular-break
                activate_kitchen,
            )

            kitchen_id = campaign_id or resolve_kitchen_id()
            if not kitchen_id.strip():
                raise ValueError("kitchen_id must contain non-whitespace characters")
            activate_kitchen(kitchen_id)
            ctx.kitchen_process_identity = None
            state = new_kitchen_open_state(
                kitchen_id=kitchen_id,
                context_id=state.context_id,
            )
            ctx.kitchen_open_state = state
        return state.kitchen_id
