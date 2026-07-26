"""Server-owned compiled recipe execution and audit admission state."""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from autoskillit.core import (
    AdmissionReason,
    AuditCycleAuthority,
    AuditCycleHead,
    AuditCycleVerifier,
    AuditVerdict,
    BindingMode,
    BoundScalar,
    BoundValueState,
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
    resolve_skill_name,
)
from autoskillit.recipe import (
    RecipeStep,
    bind_step_invocation,
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
]

_SKILL_CONTRACT_IDENTITY_DOMAIN = b"autoskillit:skill-contract:v1\0"


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
                except Exception as exc:
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
        try:
            authority = self._verifier.load_authority(authority_path)
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
        decision = self._verifier.evaluate_paths(
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


def _skill_contract_identity(
    skill_name: str,
    *,
    manifest: dict[str, Any] | None = None,
) -> str:
    active_manifest = manifest if manifest is not None else load_bundled_manifest()
    contract = get_skill_contract(skill_name, active_manifest)
    if contract is None:
        raise ValueError(f"skill contract is unavailable for {skill_name!r}")
    payload = json.dumps(
        {
            "completion_required": contract.completion_required,
            "input_preflight": getattr(contract, "input_preflight", None),
            "inputs": [
                {
                    "name": item.name,
                    "nullable": item.nullable,
                    "required": item.required,
                    "type": item.type,
                }
                for item in contract.inputs
            ],
            "skill_name": skill_name,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(_SKILL_CONTRACT_IDENTITY_DOMAIN + payload).hexdigest()


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
    for step_name, invocation in projection.invocations.items():
        if invocation.tool_name != "run_skill" or not invocation.attested:
            continue
        if invocation.skill_name is None:
            raise AssertionError("attested invocation is missing its skill identity")
        skill_identity = _skill_contract_identity(invocation.skill_name)
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
    )
    return RecipeExecutionSnapshot(
        execution_id=active_execution_id,
        recipe_name=recipe_name,
        content_hash=content_hash,
        composite_hash=composite_hash,
        templates=templates,
        snapshot_digest=snapshot_digest,
    )


def get_recipe_execution(tool_ctx: ToolContext) -> InstalledRecipeExecution | None:
    with tool_ctx.recipe_execution_lock:
        return tool_ctx.active_recipe_execution


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
        previous = tool_ctx.active_recipe_execution
        tool_ctx.active_recipe_execution = installed
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
        previous = tool_ctx.active_recipe_execution
        tool_ctx.active_recipe_execution = None
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
        installed = tool_ctx.active_recipe_execution
        if installed is None or installed.snapshot.execution_id != execution_id:
            raise RecipeExecutionAdmissionError(
                "recipe_execution_replaced",
                "active recipe execution changed before runtime binding was recorded",
            )
        updated = dict(installed.runtime_binding_digests)
        updated[step_name] = digest
        tool_ctx.active_recipe_execution = replace(
            installed,
            runtime_binding_digests=MappingProxyType(updated),
        )


def _is_dynamic(value: Any) -> bool:
    return bool(value.context_dependencies or value.input_dependencies)


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
    invocation = template.invocation
    if resolve_skill_name(skill_command) != invocation.skill_name:
        raise RecipeExecutionAdmissionError(
            "recipe_execution_skill_mismatch",
            "runtime skill identity differs from the compiled template",
        )
    compiled_mcp_names = frozenset(value.name for value in invocation.mcp_kwargs)
    protocol_mcp_values = {
        "step_name": step_name,
        "recipe_execution_id": execution_id,
        "invocation_template_digest": template_digest,
    }
    for name, expected in protocol_mcp_values.items():
        if name in actual_mcp_kwargs and actual_mcp_kwargs[name] != expected:
            raise RecipeExecutionAdmissionError(
                "recipe_execution_tool_shape",
                f"attestation parameter {name!r} differs from the active invocation",
            )
    undeclared_effective_names = sorted(
        name
        for name, value in actual_mcp_kwargs.items()
        if name not in compiled_mcp_names and name not in protocol_mcp_values and value != ""
    )
    if undeclared_effective_names:
        raise RecipeExecutionAdmissionError(
            "recipe_execution_tool_shape",
            (
                "runtime tool parameters are absent from the compiled template: "
                f"{undeclared_effective_names!r}"
            ),
        )
    manifest = load_bundled_manifest()
    contract = get_skill_contract(invocation.skill_name or "", manifest)
    if contract is None:
        raise RecipeExecutionAdmissionError(
            "recipe_execution_contract_unavailable",
            "the compiled skill contract is unavailable at runtime",
        )
    runtime_skill_identity = _skill_contract_identity(
        invocation.skill_name or "",
        manifest=manifest,
    )
    if runtime_skill_identity != template.skill_contract_identity:
        raise RecipeExecutionAdmissionError(
            "recipe_execution_contract_mismatch",
            "runtime skill contract differs from the compiled template",
        )
    contract_inputs = {input_def.name: input_def for input_def in contract.inputs}
    supplied = dict(skill_inputs or {})
    if skill_inputs is None:
        runtime_with_args: dict[str, object] = {"skill_command": skill_command}
        runtime_cwd = actual_mcp_kwargs.get("cwd")
        if isinstance(runtime_cwd, str):
            runtime_with_args["cwd"] = runtime_cwd
        runtime_inline = bind_step_invocation(
            step_name,
            RecipeStep(
                name=step_name,
                tool="run_skill",
                with_args=runtime_with_args,
                declared_with_args=dict(runtime_with_args),
            ),
            manifest=manifest,
        )
        if runtime_inline.failures:
            raise RecipeExecutionAdmissionError(
                "recipe_execution_input_shape",
                runtime_inline.failures[0].message,
            )
        supplied = {
            value.name: value.effective_value
            for value in runtime_inline.skill_inputs
            if value.state is BoundValueState.PRESENT
            and isinstance(value.effective_value, (str, int, float, bool))
        }
    if any(
        not isinstance(value, (str, int, float, bool)) or value is None
        for value in supplied.values()
    ):
        raise RecipeExecutionAdmissionError(
            "recipe_execution_input_type",
            "skill_inputs values must be strict JSON scalars",
        )
    expected_names = tuple(
        value.name for value in invocation.skill_inputs if value.state is BoundValueState.PRESENT
    )
    if frozenset(supplied) != frozenset(expected_names):
        raise RecipeExecutionAdmissionError(
            "recipe_execution_input_shape",
            "skill_inputs keys do not exactly match the compiled template",
        )
    bound_inputs: list[tuple[str, BoundScalar]] = []
    for value in invocation.skill_inputs:
        if value.state is not BoundValueState.PRESENT:
            continue
        actual = supplied[value.name]
        input_def = contract_inputs.get(value.name)
        if input_def is None:
            raise RecipeExecutionAdmissionError(
                "recipe_execution_contract_mismatch",
                f"compiled skill input {value.name!r} is absent from the runtime contract",
            )
        if not input_def.accepts(actual):
            raise RecipeExecutionAdmissionError(
                "recipe_execution_input_type",
                f"runtime skill input {value.name!r} expects {input_def.type!r}",
            )
        if not _is_dynamic(value) and actual != value.effective_value:
            raise RecipeExecutionAdmissionError(
                "recipe_execution_static_input_mismatch",
                f"static skill input {value.name!r} differs from the template",
            )
        bound_inputs.append((value.name, actual))
    for value in invocation.mcp_kwargs:
        if value.name not in actual_mcp_kwargs:
            raise RecipeExecutionAdmissionError(
                "recipe_execution_tool_shape",
                f"compiled tool parameter {value.name!r} is absent",
            )
        actual = actual_mcp_kwargs[value.name]
        if not _is_dynamic(value) and actual != value.effective_value:
            raise RecipeExecutionAdmissionError(
                "recipe_execution_static_tool_mismatch",
                f"static tool parameter {value.name!r} differs from the template",
            )
    return tuple(bound_inputs), template


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
) -> AuditCycleHead:
    if authority.execution_generation != installed.snapshot.execution_id:
        raise AuditCycleHeadConflict("authority crosses recipe execution generations")
    verifier = AuditCycleVerifier(tool_ctx.temp_dir)
    for audited_plan_ref in authority.audited_plan_refs:
        verifier.verify_artifact_ref(audited_plan_ref)
    verifier.verify_artifact_ref(authority.inventory_ref)
    if authority.remediation_ref is not None:
        verifier.verify_artifact_ref(authority.remediation_ref)
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
    manifest = load_bundled_manifest()
    preflight_identities = dict(installed.preflight_identities)
    for step_name, template in installed.snapshot.templates.items():
        contract = get_skill_contract(template.invocation.skill_name or "", manifest)
        if (
            contract is not None
            and contract.input_preflight == PreflightKind.AUDIT_CYCLE_INVENTORY.value
        ):
            preflight_identities[step_name] = expected_identity
    with tool_ctx.recipe_execution_lock:
        if tool_ctx.active_recipe_execution is not installed:
            raise AuditCycleHeadConflict(
                "active recipe execution changed while publishing audit authority"
            )
        tool_ctx.active_recipe_execution = replace(
            installed,
            preflight_identities=preflight_identities,
        )
    return head


def publish_verified_audit_cycle(
    tool_ctx: ToolContext,
    *,
    authority_path: str,
    expected_parent_digest: str | None,
    expected_round: int,
    authorized_successor_part_id: str | None = None,
) -> AuditCycleHead:
    """Verify an explicit child output, then CAS-publish it as trusted."""
    installed = get_recipe_execution(tool_ctx)
    if installed is None:
        raise AuditCycleHeadConflict("no active recipe execution")
    authority = AuditCycleVerifier(tool_ctx.temp_dir).load_authority(authority_path)
    return _publish_loaded_audit_cycle(
        tool_ctx,
        installed=installed,
        authority=authority,
        expected_parent_digest=expected_parent_digest,
        expected_round=expected_round,
        authorized_successor_part_id=authorized_successor_part_id,
    )


def publish_reported_audit_cycle(
    tool_ctx: ToolContext,
    *,
    authority_path: str,
) -> AuditCycleHead:
    """Verify and publish the authority path reported by a successful audit child."""
    installed = get_recipe_execution(tool_ctx)
    if installed is None:
        raise AuditCycleHeadConflict("no active recipe execution")
    authority = AuditCycleVerifier(tool_ctx.temp_dir).load_authority(authority_path)
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
    )


def publish_audit_cycle_result(
    tool_ctx: ToolContext,
    target_name: str | None,
    skill_result: SkillResult,
    installed: InstalledRecipeExecution | None,
) -> None:
    """Publish a successful attested audit child's declared authority."""
    if not skill_result.success or target_name != "audit-impl" or installed is None:
        return
    authority_path = (skill_result.outcome_fields or {}).get("audit_cycle_path")
    if not isinstance(authority_path, str) or not authority_path:
        raise RecipeExecutionAdmissionError(
            "recipe_execution_audit_output_missing",
            "successful audit-impl result did not declare a valid audit_cycle_path",
        )
    publish_reported_audit_cycle(tool_ctx, authority_path=authority_path)
