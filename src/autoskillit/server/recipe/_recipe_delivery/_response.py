"""The internal carrier consumed before FastMCP result conversion.

Its own module so both ``_finalize.py`` (constructs it) and ``_completion.py``
(consumes it, type-only) can import it without a circular dependency through
this package's pure-facade ``__init__.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from autoskillit.core import (
    FinalizedRecipeProjection,
    RecipeArtifactGeneration,
    RecipeDeliveryDecision,
    RecipeFlowGeneration,
)
from autoskillit.execution import RecipeDeliveryReceiptLedger, RecipeReceiptHandle
from autoskillit.pipeline import KitchenTransitionToken, RecipeInitializationRequirement

if TYPE_CHECKING:
    from autoskillit.core import RecipeExecutionSnapshot
    from autoskillit.pipeline import ToolContext


@dataclass(frozen=True, slots=True)
class FinalizedRecipeResponse:
    """Internal carrier consumed before FastMCP result conversion."""

    rendered: str
    decision: RecipeDeliveryDecision
    receipt_handle: RecipeReceiptHandle | None = None
    receipt_ledger: RecipeDeliveryReceiptLedger | None = None
    artifact_generation: RecipeArtifactGeneration | None = None
    finalized_projection: FinalizedRecipeProjection | None = None
    flow_generation: RecipeFlowGeneration | None = None
    execution_snapshot: RecipeExecutionSnapshot | None = None
    normalized_compile_key: str | None = None
    tool_ctx: ToolContext | None = None
    recipe_name: str | None = None
    initialization_activating: bool = False
    initialization_id: str | None = None
    initialization_requirements: tuple[RecipeInitializationRequirement, ...] = ()
    kitchen_transition_token: KitchenTransitionToken | None = None
