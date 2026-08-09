"""Server-owned compiled recipe execution and audit admission state."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING
from uuid import uuid4

from autoskillit.core import (
    AdmissionReason,
    AdmissionStatus,
    ArtifactRef,
    AuditCycleVerifier,
    AuditVerdict,
    BindingMode,
    BoundScalar,
    InstalledRecipeExecution,
    InventoryAdmissionDecision,
    InvocationTemplate,
    PreflightEvidence,
    PreflightKind,
    RecipeBindingProjection,
    RecipeExecutionId,
    RecipeExecutionSnapshot,
    VerifiedInputPreflightRequest,
    VerifiedInputPreflightResult,
    compute_invocation_template_digest,
    compute_recipe_execution_snapshot_digest,
    compute_tool_contract_identity,
    get_logger,
    get_tool_def,
)
from autoskillit.pipeline import (
    InitializingRecipe,
    NoActiveRecipe,
    ReadyRecipe,
    replace_ready_execution,
    transition_recipe_ready,
)
from autoskillit.recipe import (
    AuditOutputMode,
    RecipeStep,
    RuntimeBindingError,
    bind_runtime_skill_invocation,
    bind_step_invocation,
    compute_skill_contract_identity,
)
from autoskillit.server._misc import clear_run_skill_state

if TYPE_CHECKING:
    from autoskillit.core import AuditAdmissionLedger, AuditAttemptId, InstallationVersion
    from autoskillit.pipeline import ToolContext

__all__ = [
    "DefaultInputPreflightResolver",
    "RecipeExecutionAdmissionError",
    "bind_attested_runtime_invocation",
    "build_bound_child_prompt",
    "build_recipe_execution_snapshot",
    "build_standalone_child_prompt",
    "clear_recipe_execution",
    "complete_audit_finalization_effects",
    "get_recipe_execution",
    "install_recipe_execution",
    "prepare_recipe_execution",
    "record_runtime_binding_digest",
    "required_audit_finalization_effect_names",
    "resolve_attested_input_preflight",
]


class RecipeExecutionAdmissionError(RuntimeError):
    """Stable pre-launch attested-execution rejection."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class DefaultInputPreflightResolver:
    """Verify audit-cycle input provenance before any child construction."""

    def __init__(
        self,
        *,
        allowed_root: Path,
        ledger: AuditAdmissionLedger,
        recipe_execution_id: RecipeExecutionId,
        installation_version: InstallationVersion,
    ) -> None:
        self._verifier = AuditCycleVerifier(allowed_root)
        self._ledger = ledger
        self._recipe_execution_id = recipe_execution_id
        self._installation_version = installation_version

    @staticmethod
    def _result(decision: InventoryAdmissionDecision) -> VerifiedInputPreflightResult:
        evidence: tuple[PreflightEvidence, ...] = (
            PreflightEvidence("inventory_admission_status", decision.status.value),
            PreflightEvidence("inventory_admission_reason", decision.reason.value),
            PreflightEvidence(
                "inventory_dispositions",
                json.dumps(
                    [
                        {
                            "disposition": row.disposition,
                            "implementation_step": row.implementation_step,
                            "requirement_id": row.requirement_id,
                        }
                        for row in decision.dispositions
                    ],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            ),
        )
        return VerifiedInputPreflightResult(decision=decision, evidence=evidence)

    def resolve(
        self,
        request: VerifiedInputPreflightRequest,
        *,
        allowed_root: Path | None = None,
    ) -> VerifiedInputPreflightResult:
        authority_path = request.audit_cycle_path or None
        report_path = request.plan_disposition_path or None
        if authority_path is None and report_path is None:
            return self._result(InventoryAdmissionDecision.omit(AdmissionReason.NO_AUTHORITY))
        if authority_path is None:
            return self._result(
                InventoryAdmissionDecision.reject(
                    AdmissionReason.REPORT_WITHOUT_AUTHORITY,
                    "a disposition report cannot activate without authority",
                )
            )
        verifier = AuditCycleVerifier(allowed_root) if allowed_root is not None else self._verifier
        try:
            authority = verifier.load_authority(authority_path)
        except Exception as exc:
            get_logger(__name__).error(
                "audit-cycle authority verification failed",
                exc_info=True,
            )
            return self._result(
                InventoryAdmissionDecision.reject(
                    AdmissionReason.AUTHORITY_NOT_CURRENT,
                    f"authority verification failed: {exc}",
                )
            )
        if authority.execution_generation != request.execution_generation:
            return self._result(
                InventoryAdmissionDecision.reject(
                    AdmissionReason.GENERATION_MISMATCH,
                    "authority is from another execution generation",
                )
            )
        projection = self._ledger.preflight_projection(
            recipe_execution_id=self._recipe_execution_id,
            installation_version=self._installation_version,
            step_name=request.step_name,
        )
        if projection is None:
            return self._result(
                InventoryAdmissionDecision.reject(
                    AdmissionReason.INTERNAL_ERROR,
                    "trusted preflight identity is missing from the admission ledger",
                )
            )
        expected_identity = (
            projection.plan_set_id,
            projection.scope_id,
            projection.part_id,
        )
        if expected_identity != (
            authority.plan_set_id,
            authority.scope_id,
            authority.part_id,
        ):
            return self._result(
                InventoryAdmissionDecision.reject(
                    AdmissionReason.AUTHORITY_NOT_CURRENT,
                    "authority identity differs from the committed preflight projection",
                )
            )
        head = self._ledger.current_head(
            recipe_execution_id=self._recipe_execution_id,
            cycle_id=authority.cycle_id,
            scope_id=authority.scope_id,
            part_id=authority.part_id,
        )
        if authority.verdict is AuditVerdict.GO and report_path is not None:
            return self._result(
                InventoryAdmissionDecision.reject(
                    AdmissionReason.DISPOSITION_MISMATCH,
                    "a terminal GO cannot carry a plan disposition report",
                )
            )
        if report_path is not None:
            try:
                report = verifier.load_report(report_path)
            except Exception as exc:
                get_logger(__name__).error(
                    "audit-cycle disposition verification failed",
                    exc_info=True,
                )
                return self._result(
                    InventoryAdmissionDecision.reject(
                        AdmissionReason.DISPOSITION_MISMATCH,
                        f"disposition report verification failed: {exc}",
                    )
                )
            committed_report_path = self._ledger.resolve_disposition(
                authority_digest=authority.authority_digest,
                plan_digest=report.current_plan_ref.content_digest,
            )
            if committed_report_path is None or committed_report_path != Path(report_path):
                return self._result(
                    InventoryAdmissionDecision.reject(
                        AdmissionReason.DISPOSITION_MISMATCH,
                        (
                            "disposition report does not match the admission ledger's "
                            "committed authority, plan digest, and report path"
                        ),
                    )
                )
        decision = verifier.evaluate_paths(
            authority_path=authority_path,
            report_path=report_path,
            trusted_head=head,
            current_plan_path=request.plan_path,
            expected_generation=request.execution_generation,
            expected_plan_set_id=projection.plan_set_id,
            expected_scope_id=projection.scope_id,
            expected_part_id=projection.part_id,
        )
        return self._result(decision)


def _run_skill_tool_contract_identity() -> str:
    tool_def = get_tool_def("run_skill")
    if tool_def is None:
        raise RuntimeError("canonical run_skill tool contract is unavailable")
    return compute_tool_contract_identity(tool_def)


def build_recipe_execution_snapshot(
    *,
    recipe_name: str,
    content_hash: str,
    composite_hash: str,
    projection: RecipeBindingProjection,
    execution_id: str | None = None,
) -> RecipeExecutionSnapshot:
    """Create a fresh attested snapshot from the exact post-prune projection."""
    active_execution_id = execution_id or uuid4().hex
    tool_identity = _run_skill_tool_contract_identity()
    templates: dict[str, InvocationTemplate] = {}
    dynamic_skill_step_names: set[str] = set()
    for step_name, invocation in projection.invocations.items():
        if invocation.tool_name != "run_skill":
            continue
        if (
            invocation.mode is BindingMode.RECIPE
            and invocation.is_valid
            and invocation.skill_name is None
        ):
            dynamic_skill_step_names.add(step_name)
            continue
        if not invocation.attested:
            continue
        if invocation.skill_name is None:
            raise AssertionError("attested invocation is missing its skill identity")
        skill_identity = compute_skill_contract_identity(invocation.skill_name)
        digest = compute_invocation_template_digest(
            execution_id=active_execution_id,
            recipe_name=recipe_name,
            content_hash=content_hash,
            composite_hash=composite_hash,
            invocation=invocation,
            tool_contract_identity=tool_identity,
            skill_contract_identity=skill_identity,
        )
        templates[step_name] = InvocationTemplate(
            invocation=invocation,
            tool_contract_identity=tool_identity,
            skill_contract_identity=skill_identity,
            template_digest=digest,
        )
    snapshot_digest = compute_recipe_execution_snapshot_digest(
        execution_id=active_execution_id,
        recipe_name=recipe_name,
        content_hash=content_hash,
        composite_hash=composite_hash,
        templates=templates,
        dynamic_skill_step_names=frozenset(dynamic_skill_step_names),
    )
    return RecipeExecutionSnapshot(
        execution_id=active_execution_id,
        recipe_name=recipe_name,
        content_hash=content_hash,
        composite_hash=composite_hash,
        templates=templates,
        snapshot_digest=snapshot_digest,
        dynamic_skill_step_names=frozenset(dynamic_skill_step_names),
    )


def get_recipe_execution(tool_ctx: ToolContext) -> InstalledRecipeExecution | None:
    with tool_ctx.recipe_execution_lock:
        state = tool_ctx.recipe_initialization_state
        return state.installed_execution if isinstance(state, ReadyRecipe) else None


def prepare_recipe_execution(
    tool_ctx: ToolContext,
    *,
    snapshot: RecipeExecutionSnapshot,
) -> InstalledRecipeExecution:
    """Build a replacement generation without changing the active execution."""
    factory = tool_ctx.recipe_execution_factory
    if factory is None:
        raise RecipeExecutionAdmissionError(
            "recipe_execution_factory_unavailable",
            "recipe execution factory is not configured",
        )
    with tool_ctx.recipe_execution_lock:
        state = tool_ctx.recipe_initialization_state
        if isinstance(state, InitializingRecipe) and state.staged_snapshot == snapshot:
            installation_version = state.installation_version
        elif isinstance(state, ReadyRecipe) and state.installed_execution.snapshot == snapshot:
            installation_version = state.installed_execution.installation_version
        else:
            raise RecipeExecutionAdmissionError(
                "recipe_installation_not_staged",
                "recipe execution must be staged before it can be prepared",
            )
    return factory(
        snapshot=snapshot,
        allowed_root=tool_ctx.temp_dir,
        installation_version=installation_version,
        audit_admission_ledger=tool_ctx.audit_admission_ledger,
    )


def install_recipe_execution(
    tool_ctx: ToolContext,
    *,
    snapshot: RecipeExecutionSnapshot | None = None,
    prepared_execution: InstalledRecipeExecution | None = None,
    completion_receipt: str | None = None,
) -> InstalledRecipeExecution:
    """Atomically install a snapshot using its staged installation occurrence."""
    if (snapshot is None) == (prepared_execution is None):
        raise TypeError("provide exactly one of snapshot or prepared_execution")
    if prepared_execution is not None:
        installed = prepared_execution
    else:
        assert snapshot is not None
        installed = prepare_recipe_execution(tool_ctx, snapshot=snapshot)
    with tool_ctx.recipe_execution_lock:
        state = tool_ctx.recipe_initialization_state
        if installed.audit_admission_ledger is not tool_ctx.audit_admission_ledger:
            raise RecipeExecutionAdmissionError(
                "audit_admission_ledger_mismatch",
                "prepared recipe execution uses a different audit admission ledger",
            )
        if isinstance(state, InitializingRecipe):
            tool_ctx.recipe_initialization_state = transition_recipe_ready(
                state,
                installed_execution=installed,
                completion_receipt=completion_receipt or state.completion_receipt or "",
            )
        elif isinstance(state, ReadyRecipe):
            tool_ctx.recipe_initialization_state = replace_ready_execution(state, installed)
        else:
            raise RecipeExecutionAdmissionError(
                "recipe_initialization_not_active",
                "recipe execution cannot install before initialization is staged",
            )
    return installed


def clear_recipe_execution(tool_ctx: ToolContext) -> None:
    """Clear the complete active attestation generation in one locked transition."""
    with tool_ctx.recipe_execution_lock:
        state = tool_ctx.recipe_initialization_state
        if isinstance(state, ReadyRecipe):
            recipe_execution_id = RecipeExecutionId(
                state.installed_execution.snapshot.execution_id
            )
            installation_version = state.installed_execution.installation_version
        elif isinstance(state, InitializingRecipe):
            recipe_execution_id = RecipeExecutionId(state.staged_snapshot.execution_id)
            installation_version = state.installation_version
        else:
            recipe_execution_id = None
            installation_version = None
        if recipe_execution_id is not None and installation_version is not None:
            tool_ctx.audit_admission_ledger.retire_installation(
                recipe_execution_id=recipe_execution_id,
                installation_version=installation_version,
            )
        tool_ctx.recipe_initialization_state = NoActiveRecipe()


def record_runtime_binding_digest(
    tool_ctx: ToolContext,
    *,
    execution_id: str,
    step_name: str,
    digest: str,
) -> InstalledRecipeExecution:
    with tool_ctx.recipe_execution_lock:
        state = tool_ctx.recipe_initialization_state
        if (
            not isinstance(state, ReadyRecipe)
            or state.installed_execution.snapshot.execution_id != execution_id
        ):
            raise RecipeExecutionAdmissionError(
                "recipe_execution_replaced",
                "active recipe execution changed before runtime binding was recorded",
            )
        installed = state.installed_execution
        updated = dict(installed.runtime_binding_digests)
        updated[step_name] = digest
        replacement = replace(
            installed,
            runtime_binding_digests=MappingProxyType(updated),
        )
        tool_ctx.recipe_initialization_state = replace_ready_execution(
            state,
            replacement,
        )
        return replacement


def bind_attested_runtime_invocation(
    installed: InstalledRecipeExecution,
    *,
    execution_id: str,
    step_name: str,
    template_digest: str,
    skill_command: str,
    skill_inputs: Mapping[str, BoundScalar] | None,
    actual_mcp_kwargs: Mapping[str, BoundScalar],
) -> tuple[tuple[tuple[str, BoundScalar], ...], InvocationTemplate]:
    """Validate identity/static shape, then bind only declared dynamic slots."""
    snapshot = installed.snapshot
    if execution_id != snapshot.execution_id:
        raise RecipeExecutionAdmissionError(
            "recipe_execution_id_mismatch",
            "recipe execution ID is missing, stale, or replaced",
        )
    template = snapshot.templates.get(step_name)
    if template is None:
        raise RecipeExecutionAdmissionError(
            "recipe_execution_step_mismatch",
            "step is not present in the active compiled execution",
        )
    if template_digest != template.template_digest:
        raise RecipeExecutionAdmissionError(
            "invocation_template_digest_mismatch",
            "invocation template digest does not match the active step",
        )
    try:
        bound_inputs = bind_runtime_skill_invocation(
            template,
            execution_id=execution_id,
            step_name=step_name,
            skill_command=skill_command,
            skill_inputs=skill_inputs,
            actual_mcp_kwargs=actual_mcp_kwargs,
        )
    except RuntimeBindingError as exc:
        raise RecipeExecutionAdmissionError(exc.code, str(exc)) from exc
    return bound_inputs, template


def resolve_attested_input_preflight(
    tool_ctx: ToolContext,
    installed: InstalledRecipeExecution,
    *,
    skill_command: str,
    execution_id: str,
    step_name: str,
    template: InvocationTemplate,
    bound_inputs: tuple[tuple[str, BoundScalar], ...],
    allowed_root: Path | None = None,
) -> VerifiedInputPreflightResult | None:
    """Resolve a compiled invocation's optional input preflight or fail closed."""
    contract = (
        tool_ctx.skill_contract_resolver(skill_command)
        if tool_ctx.skill_contract_resolver is not None
        else None
    )
    preflight_name = getattr(contract, "input_preflight", None)
    if not isinstance(preflight_name, str) or not preflight_name:
        return None
    if preflight_name != PreflightKind.AUDIT_CYCLE_INVENTORY.value:
        raise RecipeExecutionAdmissionError(
            "recipe_execution_preflight_unknown",
            f"unsupported input preflight {preflight_name!r}",
        )
    bound_input_map = dict(bound_inputs)
    plan_path = bound_input_map.get("plan_path")
    audit_cycle_path = bound_input_map.get("audit_cycle_path")
    plan_disposition_path = bound_input_map.get("plan_disposition_path")
    if not isinstance(plan_path, str):
        raise RecipeExecutionAdmissionError(
            "recipe_execution_preflight_input",
            "audit-cycle preflight requires a bound string plan_path",
        )
    if audit_cycle_path is not None and not isinstance(audit_cycle_path, str):
        raise RecipeExecutionAdmissionError(
            "recipe_execution_preflight_input",
            "audit_cycle_path must be a string when present",
        )
    if plan_disposition_path is not None and not isinstance(plan_disposition_path, str):
        raise RecipeExecutionAdmissionError(
            "recipe_execution_preflight_input",
            "plan_disposition_path must be a string when present",
        )
    result = installed.input_preflight_resolver.resolve(
        VerifiedInputPreflightRequest(
            execution_generation=execution_id,
            step_name=step_name,
            skill_name=template.invocation.skill_name or "",
            plan_path=plan_path,
            audit_cycle_path=audit_cycle_path or None,
            plan_disposition_path=plan_disposition_path or None,
        ),
        allowed_root=allowed_root,
    )
    if result.decision.status is AdmissionStatus.REJECT:
        raise RecipeExecutionAdmissionError(
            f"input_preflight_{result.decision.reason.value}",
            (
                result.decision.details[0]
                if result.decision.details
                else "verified input preflight rejected the invocation"
            ),
        )
    return result


def build_bound_child_prompt(
    skill_command: str,
    bound_inputs: tuple[tuple[str, BoundScalar], ...],
    preflight: VerifiedInputPreflightResult | None,
    *,
    audit_reservation_handle: str | None = None,
    audit_reserved_plan_refs: tuple[ArtifactRef, ...] = (),
    audit_output_mode: AuditOutputMode | None = None,
) -> str:
    """Serialize one non-shell child prompt from ordered bound data."""
    payload: dict[str, object] = {
        "skill_inputs": [{"name": name, "value": value} for name, value in bound_inputs]
    }
    if preflight is not None:
        payload["verified_input_preflight"] = {
            "evidence": [{"name": item.name, "value": item.value} for item in preflight.evidence],
            "reason": preflight.decision.reason.value,
            "status": preflight.decision.status.value,
        }
    if audit_reservation_handle is not None:
        payload["audit_semantic_submission"] = {
            "audited_plan_refs": [reference.to_dict() for reference in audit_reserved_plan_refs],
            "reservation_handle": audit_reservation_handle,
        }
    if audit_output_mode is not None:
        payload["audit_output_mode"] = audit_output_mode.value
    return f"{skill_command.strip()}\n\nAUTOSKILLIT_BOUND_INVOCATION_V1\n" + json.dumps(
        payload, ensure_ascii=False, separators=(",", ":")
    )


def build_standalone_child_prompt(
    skill_command: str,
    cwd: str,
    skill_inputs: Mapping[str, str | int | float | bool] | None,
    *,
    audit_output_mode: AuditOutputMode | None = None,
) -> str:
    """Validate standalone inputs without requiring attested recipe state.

    An unknown skill with no structured inputs may still be resolved from a
    project/plugin installation later in the normal dispatch path. Structured
    inputs require a canonical contract because their names and order otherwise
    cannot be validated safely.
    """
    if skill_inputs is None:
        if audit_output_mode is not None:
            return build_bound_child_prompt(
                skill_command,
                (),
                None,
                audit_output_mode=audit_output_mode,
            )
        return skill_command
    with_args: dict[str, object] = {
        "skill_command": skill_command,
        "cwd": cwd,
        "skill_inputs": skill_inputs,
    }
    binding = bind_step_invocation(
        "standalone",
        RecipeStep(
            name="standalone",
            tool="run_skill",
            with_args=with_args,
            declared_with_args=dict(with_args),
        ),
        mode=BindingMode.STANDALONE,
    )
    if binding.failures:
        failure = binding.failures[0]
        raise RecipeExecutionAdmissionError(
            f"standalone_{failure.code.value}",
            failure.message,
        )
    return build_bound_child_prompt(
        skill_command,
        binding.canonical_child_invocation,
        None,
        audit_output_mode=audit_output_mode,
    )


_BASE_AUDIT_FINALIZATION_EFFECT_NAMES = (
    "audit_success_recorded",
    "run_skill_state_cleared",
)


def required_audit_finalization_effect_names() -> tuple[str, ...]:
    """Return the closed required-effect set for one attested audit response."""
    return _BASE_AUDIT_FINALIZATION_EFFECT_NAMES


def complete_audit_finalization_effects(
    tool_ctx: ToolContext,
    *,
    attempt_id: AuditAttemptId,
    skill_command: str,
    step_name: str,
    order_id: str,
    mark_step_complete: Callable[[ToolContext, str, str], dict | None],
) -> dict[str, object] | None:
    """Complete each attempt-keyed success effect at most once."""

    def complete(
        effect_name: str,
        action: Callable[[], dict[str, object] | None],
    ) -> dict[str, object]:
        existing = tool_ctx.audit_admission_ledger.finalization_effect_result(
            attempt_id,
            effect_name,
        )
        if existing is not None:
            return existing
        result = action() or {}
        tool_ctx.audit_admission_ledger.acknowledge_finalization_effect(
            attempt_id,
            effect_name,
            result,
        )
        return result

    complete(
        "audit_success_recorded",
        lambda: tool_ctx.audit.record_success(
            skill_command,
            dedupe_key=attempt_id.value,
        ),
    )
    complete("run_skill_state_cleared", lambda: clear_run_skill_state(tool_ctx.project_dir))
    return None
