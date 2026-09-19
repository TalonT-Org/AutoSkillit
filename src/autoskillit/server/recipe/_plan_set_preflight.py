"""Server adapter from recipe execution to the IL-0 plan-set verifier."""

from __future__ import annotations

from pathlib import Path

from autoskillit.core import (
    PlanSetPreflightRequest,
    PlanSetVerification,
    RecipeExecutionId,
    verify_plan_set_authority,
)


class DefaultPlanSetPreflightResolver:
    """Verify a bound authority without allowing recipe code to read its bytes."""

    def __init__(self, recipe_execution_id: RecipeExecutionId, kitchen_id: str) -> None:
        self._recipe_execution_id = recipe_execution_id
        self._kitchen_id = kitchen_id

    def resolve(
        self,
        request: PlanSetPreflightRequest,
        *,
        allowed_root: Path,
    ) -> PlanSetVerification:
        return verify_plan_set_authority(
            request.plan_set_authority_path,
            allowed_root=allowed_root,
            expected_execution_generation=self._recipe_execution_id.value,
            expected_kitchen_id=request.expected_kitchen_id or self._kitchen_id,
            current_plan_path=request.plan_path,
            require_sealed=request.require_sealed,
        )
