"""Server adapter from recipe execution to the IL-0 plan-set verifier."""

from __future__ import annotations

from pathlib import Path

from autoskillit.core import (
    PlanSetBindingMode,
    PlanSetPreflightRequest,
    PlanSetPreflightResolver,
    PlanSetVerification,
    RecipeExecutionId,
    verify_plan_set_authority,
)


def resolve_plan_set_preflight(
    resolver: PlanSetPreflightResolver,
    *,
    execution_generation: str,
    kitchen_id: str,
    step_name: str,
    skill_name: str,
    plan_path: str | None,
    authority_path: str,
    allowed_root: Path,
    allow_part_drift: bool,
) -> PlanSetVerification:
    return resolver.resolve(
        PlanSetPreflightRequest(
            execution_generation=execution_generation,
            expected_kitchen_id=kitchen_id,
            step_name=step_name,
            skill_name=skill_name,
            plan_path=plan_path,
            plan_set_authority_path=authority_path,
            allow_part_drift=allow_part_drift,
        ),
        allowed_root=allowed_root,
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
            expected_binding_mode=PlanSetBindingMode.RECIPE,
            current_plan_path=request.plan_path,
            require_sealed=request.require_sealed,
            allow_part_drift=request.allow_part_drift,
        )
