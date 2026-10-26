"""Server adapter from recipe execution to the IL-0 plan-set verifier."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from autoskillit.core import (
    BoundScalar,
    InstalledRecipeExecution,
    InvocationTemplate,
    PlanSetBindingMode,
    PlanSetPreflightEvidence,
    PlanSetPreflightRequest,
    PlanSetRejectReason,
    PlanSetVerification,
    PreflightKind,
    RecipeExecutionId,
    verify_plan_set_authority,
)
from autoskillit.server.recipe._admission import RecipeExecutionAdmissionError

if TYPE_CHECKING:
    from autoskillit.pipeline import ToolContext


def _resolve_plan_set_preflight(
    tool_ctx: ToolContext,
    installed: InstalledRecipeExecution,
    bound_input_map: Mapping[str, BoundScalar],
    execution_id: str,
    step_name: str,
    template: InvocationTemplate,
    kind: PreflightKind,
) -> PlanSetPreflightEvidence | None:
    """Use recipe_execution_preflight_* for input or service failures.

    Authority and verifier rejections use input_preflight_plan_set_* codes.
    """
    authority_path = bound_input_map.get("plan_set_authority_path")
    if authority_path in (None, ""):
        return None
    stage_scope = kind is PreflightKind.PLAN_SET_STAGE_OBLIGATIONS
    plan_path = None if stage_scope else bound_input_map.get("plan_path")
    if not isinstance(authority_path, str) or (not stage_scope and not isinstance(plan_path, str)):
        raise RecipeExecutionAdmissionError(
            "recipe_execution_preflight_input",
            "plan-set preflight requires bound string plan_path and authority path",
        )
    materializer = tool_ctx.plan_set_materializer
    if materializer is None or installed.plan_set_preflight_resolver is None:
        raise RecipeExecutionAdmissionError(
            "recipe_execution_preflight_unavailable",
            "plan-set materializer or preflight resolver is unavailable",
        )
    binding = materializer.lookup_binding(execution_id, authority_path)
    if binding is None:
        raise RecipeExecutionAdmissionError(
            f"input_preflight_plan_set_{PlanSetRejectReason.AUTHORITY_INVALID.value}",
            "authority was not bound by this server for the current recipe execution",
        )
    bind_root, issued_digest = binding
    verification = installed.plan_set_preflight_resolver.resolve(
        PlanSetPreflightRequest(
            execution_generation=execution_id,
            expected_kitchen_id=tool_ctx.kitchen_id,
            step_name=step_name,
            skill_name=template.invocation.skill_name or "",
            plan_path=plan_path if isinstance(plan_path, str) else None,
            plan_set_authority_path=authority_path,
            allow_part_drift=stage_scope,
        ),
        allowed_root=bind_root,
    )
    if not verification.accepted:
        reason = verification.reason.value if verification.reason else "rejected"
        raise RecipeExecutionAdmissionError(
            f"input_preflight_plan_set_{reason}",
            "; ".join(verification.detail),
        )
    evidence = verification.evidence
    if evidence is None:
        raise RecipeExecutionAdmissionError(
            "recipe_execution_preflight_unavailable",
            "accepted plan-set verification did not provide evidence",
        )
    if evidence.plan_set_authority_digest != issued_digest:
        raise RecipeExecutionAdmissionError(
            "input_preflight_plan_set_authority_digest",
            "verified authority digest differs from its server-issued binding",
        )
    return evidence


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
