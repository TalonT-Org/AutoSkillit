"""Server-owned compiled recipe execution and audit admission state."""

from __future__ import annotations

import json
import threading
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING
from uuid import uuid4

from autoskillit.core import (
    AdmissionReason,
    AdmissionStatus,
    AuditCycleAuthority,
    AuditCycleHead,
    AuditCycleVerificationError,
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
    RecipeStep,
    RuntimeBindingError,
    bind_runtime_skill_invocation,
    bind_step_invocation,
    compute_skill_contract_identity,
    get_skill_contract,
    load_bundled_manifest,
)

if TYPE_CHECKING:
    from autoskillit.core import AuditCycleHeadStore, SkillResult
    from autoskillit.pipeline import ToolContext

__all__ = [
    "AuditCycleHeadConflict",
    "DefaultAuditCycleHeadStore",
    "DefaultInputPreflightResolver",
    "RecipeExecutionAdmissionError",
    "bind_attested_runtime_invocation",
    "build_bound_child_prompt",
    "build_recipe_execution_snapshot",
    "build_standalone_child_prompt",
    "clear_recipe_execution",
    "get_recipe_execution",
    "install_recipe_execution",
    "prepare_recipe_execution",
    "publish_audit_cycle_result",
    "publish_reported_audit_cycle",
    "publish_verified_audit_cycle",
    "record_runtime_binding_digest",
    "resolve_attested_input_preflight",
]


class AuditCycleHeadConflict(RuntimeError):
    """A candidate authority failed the trusted-head compare-and-swap."""


class RecipeExecutionAdmissionError(RuntimeError):
    """Stable pre-launch attested-execution rejection."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class DefaultAuditCycleHeadStore:
    """Lock-safe in-memory trusted-head ledger."""

    def __init__(self) -> None:
        self._heads: dict[tuple[str, str, str, str], AuditCycleHead] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _key(
        execution_generation: str,
        plan_set_id: str,
        scope_id: str,
        part_id: str,
    ) -> tuple[str, str, str, str]:
        return execution_generation, plan_set_id, scope_id, part_id

    def get(
        self,
        *,
        execution_generation: str,
        plan_set_id: str,
        scope_id: str,
        part_id: str,
    ) -> AuditCycleHead | None:
        with self._lock:
            return self._heads.get(self._key(execution_generation, plan_set_id, scope_id, part_id))

    def publish(
        self,
        authority: AuditCycleAuthority,
        *,
        expected_parent_digest: str | None,
        expected_round: int,
        authorized_successor_part_id: str | None = None,
    ) -> AuditCycleHead:
        key = self._key(
            authority.execution_generation,
            authority.plan_set_id,
            authority.scope_id,
            authority.part_id,
        )
        with self._lock:
            current = self._heads.get(key)
            if current is None:
                if (
                    expected_parent_digest is not None
                    or expected_round != 0
                    or authority.parent_authority_digest is not None
                    or authority.audit_round != 1
                ):
                    raise AuditCycleHeadConflict(
                        "initial authority requires an empty parent and round one"
                    )
            else:
                if current.verdict is AuditVerdict.GO:
                    raise AuditCycleHeadConflict(
                        "terminal GO authority cannot be advanced within the same part"
                    )
                if (
                    current.current_authority_digest != expected_parent_digest
                    or current.audit_round != expected_round
                ):
                    raise AuditCycleHeadConflict("audit-cycle head compare-and-swap failed")
                try:
                    AuditCycleVerifier.verify_successor(authority, current)
                except AuditCycleVerificationError as exc:
                    raise AuditCycleHeadConflict(str(exc)) from exc
            if (
                authorized_successor_part_id is not None
                and authority.verdict is not AuditVerdict.GO
            ):
                raise AuditCycleHeadConflict("only a terminal GO may authorize a successor part")
            head = AuditCycleHead(
                execution_generation=authority.execution_generation,
                cycle_id=authority.cycle_id,
                plan_set_id=authority.plan_set_id,
                scope_id=authority.scope_id,
                part_id=authority.part_id,
                current_authority_digest=authority.authority_digest,
                audit_round=authority.audit_round,
                audited_plan_refs=authority.audited_plan_refs,
                inventory_ref=authority.inventory_ref,
                verdict=authority.verdict,
                authorized_successor_part_id=authorized_successor_part_id,
            )
            self._heads[key] = head
            return head

    def clear_generation(self, execution_generation: str) -> None:
        with self._lock:
            self._heads = {
                key: value for key, value in self._heads.items() if key[0] != execution_generation
            }

    def clear_all(self) -> None:
        with self._lock:
            self._heads.clear()


class DefaultInputPreflightResolver:
    """Verify audit-cycle input provenance before any child construction."""

    def __init__(self, *, allowed_root: Path, head_store: AuditCycleHeadStore) -> None:
        self._verifier = AuditCycleVerifier(allowed_root)
        self._head_store = head_store

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
        if not (
            request.expected_plan_set_id and request.expected_scope_id and request.expected_part_id
        ):
            return self._result(
                InventoryAdmissionDecision.reject(
                    AdmissionReason.INTERNAL_ERROR,
                    "trusted preflight identity is missing from the invocation template",
                )
            )
        head = self._head_store.get(
            execution_generation=authority.execution_generation,
            plan_set_id=authority.plan_set_id,
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
        decision = verifier.evaluate_paths(
            authority_path=authority_path,
            report_path=report_path,
            trusted_head=head,
            current_plan_path=request.plan_path,
            expected_generation=request.execution_generation,
            expected_plan_set_id=request.expected_plan_set_id,
            expected_scope_id=request.expected_scope_id,
            expected_part_id=request.expected_part_id,
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
    return factory(snapshot=snapshot, allowed_root=tool_ctx.temp_dir)


def install_recipe_execution(
    tool_ctx: ToolContext,
    *,
    snapshot: RecipeExecutionSnapshot | None = None,
    prepared_execution: InstalledRecipeExecution | None = None,
    completion_receipt: str | None = None,
) -> InstalledRecipeExecution:
    """Atomically install a snapshot, empty runtime map, and empty head ledger."""
    if (snapshot is None) == (prepared_execution is None):
        raise TypeError("provide exactly one of snapshot or prepared_execution")
    if prepared_execution is not None:
        installed = prepared_execution
    else:
        assert snapshot is not None
        installed = prepare_recipe_execution(tool_ctx, snapshot=snapshot)
    with tool_ctx.recipe_execution_lock:
        state = tool_ctx.recipe_initialization_state
        previous = state.installed_execution if isinstance(state, ReadyRecipe) else None
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
    if previous is not None and previous is not installed:
        try:
            previous.audit_cycle_heads.clear_generation(previous.snapshot.execution_id)
        except Exception:
            get_logger(__name__).warning(
                "prior recipe execution cleanup failed",
                exc_info=True,
            )
    return installed


def clear_recipe_execution(tool_ctx: ToolContext) -> None:
    """Clear the complete active attestation generation in one locked transition."""
    with tool_ctx.recipe_execution_lock:
        state = tool_ctx.recipe_initialization_state
        previous = state.installed_execution if isinstance(state, ReadyRecipe) else None
        tool_ctx.recipe_initialization_state = NoActiveRecipe()
    if previous is not None:
        previous.audit_cycle_heads.clear_generation(previous.snapshot.execution_id)


def record_runtime_binding_digest(
    tool_ctx: ToolContext,
    *,
    execution_id: str,
    step_name: str,
    digest: str,
) -> None:
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
        tool_ctx.recipe_initialization_state = replace_ready_execution(
            state,
            replace(
                installed,
                runtime_binding_digests=MappingProxyType(updated),
            ),
        )


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
    expected_identity = installed.preflight_identities.get(step_name)
    if audit_cycle_path and expected_identity is None:
        raise RecipeExecutionAdmissionError(
            "recipe_execution_preflight_identity_missing",
            "authority-bearing preflight requires a trusted template identity",
        )
    expected_identity = expected_identity or ("", "", "")
    result = installed.input_preflight_resolver.resolve(
        VerifiedInputPreflightRequest(
            execution_generation=execution_id,
            step_name=step_name,
            skill_name=template.invocation.skill_name or "",
            plan_path=plan_path,
            audit_cycle_path=audit_cycle_path or None,
            plan_disposition_path=plan_disposition_path or None,
            expected_plan_set_id=expected_identity[0],
            expected_scope_id=expected_identity[1],
            expected_part_id=expected_identity[2],
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
    return f"{skill_command.strip()}\n\nAUTOSKILLIT_BOUND_INVOCATION_V1\n" + json.dumps(
        payload, ensure_ascii=False, separators=(",", ":")
    )


def build_standalone_child_prompt(
    skill_command: str,
    cwd: str,
    skill_inputs: Mapping[str, str | int | float | bool] | None,
) -> str:
    """Validate standalone inputs without requiring attested recipe state.

    An unknown skill with no structured inputs may still be resolved from a
    project/plugin installation later in the normal dispatch path. Structured
    inputs require a canonical contract because their names and order otherwise
    cannot be validated safely.
    """
    if skill_inputs is None:
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
    )


def _publish_loaded_audit_cycle(
    tool_ctx: ToolContext,
    *,
    installed: InstalledRecipeExecution,
    authority: AuditCycleAuthority,
    expected_parent_digest: str | None,
    expected_round: int,
    authorized_successor_part_id: str | None = None,
    allowed_root: Path,
) -> AuditCycleHead:
    if authority.execution_generation != installed.snapshot.execution_id:
        raise AuditCycleHeadConflict("authority crosses recipe execution generations")
    verifier = AuditCycleVerifier(allowed_root)
    for audited_plan_ref in authority.audited_plan_refs:
        verifier.verify_artifact_ref(audited_plan_ref)
    verifier.verify_artifact_ref(authority.inventory_ref)
    if authority.remediation_ref is not None:
        verifier.verify_artifact_ref(authority.remediation_ref)
    manifest = load_bundled_manifest()
    with tool_ctx.recipe_execution_lock:
        state = tool_ctx.recipe_initialization_state
        if not isinstance(state, ReadyRecipe) or state.installed_execution is not installed:
            raise AuditCycleHeadConflict(
                "active recipe execution changed while publishing audit authority"
            )
        head = installed.audit_cycle_heads.publish(
            authority,
            expected_parent_digest=expected_parent_digest,
            expected_round=expected_round,
            authorized_successor_part_id=authorized_successor_part_id,
        )
        expected_identity = (
            head.plan_set_id,
            head.scope_id,
            head.authorized_successor_part_id or head.part_id,
        )
        preflight_identities = dict(installed.preflight_identities)
        for step_name, template in installed.snapshot.templates.items():
            contract = get_skill_contract(template.invocation.skill_name or "", manifest)
            if (
                contract is not None
                and contract.input_preflight == PreflightKind.AUDIT_CYCLE_INVENTORY.value
            ):
                preflight_identities[step_name] = expected_identity
        tool_ctx.recipe_initialization_state = replace_ready_execution(
            state,
            replace(
                installed,
                preflight_identities=preflight_identities,
            ),
        )
    return head


def publish_verified_audit_cycle(
    tool_ctx: ToolContext,
    *,
    authority_path: str,
    expected_parent_digest: str | None,
    expected_round: int,
    authorized_successor_part_id: str | None = None,
    allowed_root: Path,
) -> AuditCycleHead:
    """Verify an explicit child output, then CAS-publish it as trusted."""
    installed = get_recipe_execution(tool_ctx)
    if installed is None:
        raise AuditCycleHeadConflict("no active recipe execution")
    authority = AuditCycleVerifier(allowed_root).load_authority(authority_path)
    return _publish_loaded_audit_cycle(
        tool_ctx,
        installed=installed,
        authority=authority,
        expected_parent_digest=expected_parent_digest,
        expected_round=expected_round,
        authorized_successor_part_id=authorized_successor_part_id,
        allowed_root=allowed_root,
    )


def publish_reported_audit_cycle(
    tool_ctx: ToolContext,
    *,
    authority_path: str,
    prior_authority_path: str | None,
    allowed_root: Path,
) -> AuditCycleHead:
    """Verify and publish the authority path reported by a successful audit child."""
    installed = get_recipe_execution(tool_ctx)
    if installed is None:
        raise AuditCycleHeadConflict("no active recipe execution")
    verifier = AuditCycleVerifier(allowed_root)
    authority = verifier.load_authority(authority_path)
    if prior_authority_path:
        prior = verifier.load_authority(prior_authority_path)
        if (
            authority.execution_generation,
            authority.cycle_id,
            authority.plan_set_id,
            authority.scope_id,
            authority.part_id,
        ) != (
            prior.execution_generation,
            prior.cycle_id,
            prior.plan_set_id,
            prior.scope_id,
            prior.part_id,
        ):
            raise AuditCycleHeadConflict(
                "reported authority identity differs from its attested prior authority"
            )
        current = installed.audit_cycle_heads.get(
            execution_generation=prior.execution_generation,
            plan_set_id=prior.plan_set_id,
            scope_id=prior.scope_id,
            part_id=prior.part_id,
        )
        if current is None or current.current_authority_digest != prior.authority_digest:
            raise AuditCycleHeadConflict(
                "attested prior authority is not the trusted current head"
            )
    else:
        current = installed.audit_cycle_heads.get(
            execution_generation=authority.execution_generation,
            plan_set_id=authority.plan_set_id,
            scope_id=authority.scope_id,
            part_id=authority.part_id,
        )
    return _publish_loaded_audit_cycle(
        tool_ctx,
        installed=installed,
        authority=authority,
        expected_parent_digest=(current.current_authority_digest if current is not None else None),
        expected_round=current.audit_round if current is not None else 0,
        allowed_root=allowed_root,
    )


def publish_audit_cycle_result(
    tool_ctx: ToolContext,
    target_name: str | None,
    skill_result: SkillResult,
    installed: InstalledRecipeExecution | None,
    bound_inputs: tuple[tuple[str, BoundScalar], ...],
    allowed_root: Path,
) -> None:
    """Publish a successful attested audit child's declared authority."""
    if not skill_result.success or target_name is None or installed is None:
        return
    contract = get_skill_contract(target_name, load_bundled_manifest())
    publication = contract.audit_authority_publication if contract is not None else None
    if publication is None:
        return
    authority_path = (skill_result.outcome_fields or {}).get(publication.output_field)
    if not isinstance(authority_path, str) or not authority_path:
        raise RecipeExecutionAdmissionError(
            "recipe_execution_audit_output_missing",
            "successful authority-producing skill result did not declare "
            f"a valid {publication.output_field}",
        )
    prior_authority_path = dict(bound_inputs).get(publication.prior_input_field)
    publish_reported_audit_cycle(
        tool_ctx,
        authority_path=authority_path,
        prior_authority_path=(
            prior_authority_path
            if isinstance(prior_authority_path, str) and prior_authority_path
            else None
        ),
        allowed_root=allowed_root,
    )
