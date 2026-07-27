"""Attested recipe delivery, runtime binding, and trusted audit-head lifecycle."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    initialize,
    invariant,
    precondition,
    rule,
)

import autoskillit.recipe._binding as binding_module
import autoskillit.server._recipe_delivery as recipe_delivery_module
import autoskillit.server._recipe_execution as recipe_execution_module
from autoskillit.core import (
    AdmissionReason,
    AdmissionStatus,
    ArtifactRef,
    AuditAssessment,
    AuditAssessmentRow,
    AuditCycleAuthority,
    AuditCycleHead,
    AuditCycleVerificationError,
    AuditCycleVerifier,
    AuditVerdict,
    BindingMode,
    BoundStepInvocation,
    BoundValue,
    BoundValueOrigin,
    BoundValueState,
    InputPreflightResolver,
    InventoryAdmissionDecision,
    InventoryAdmissionEvaluator,
    InvocationTemplate,
    PlanDispositionReport,
    PlanDispositionRow,
    PreflightEvidence,
    RecipeBindingProjection,
    RecipeExecutionSnapshot,
    RetryReason,
    SkillResult,
    VerifiedInputPreflightRequest,
    VerifiedInputPreflightResult,
    compute_runtime_binding_digest,
)
from autoskillit.core.closure_hashing import compute_bytes_hash
from autoskillit.recipe import RecipeStep, bind_step_invocation
from autoskillit.server._recipe_delivery import (
    complete_finalized_recipe_response,
    finalize_recipe_delivery,
    load_recipe_artifact,
)
from autoskillit.server._recipe_execution import (
    AuditCycleHeadConflict,
    DefaultAuditCycleHeadStore,
    DefaultInputPreflightResolver,
    RecipeExecutionAdmissionError,
    bind_attested_runtime_invocation,
    build_bound_child_prompt,
    build_recipe_execution_snapshot,
    get_recipe_execution,
    install_recipe_execution,
    publish_audit_cycle_result,
    publish_reported_audit_cycle,
    publish_verified_audit_cycle,
    record_runtime_binding_digest,
)
from autoskillit.server.tools.tools_execution import run_skill
from tests.conftest import _make_result

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]

_HASH_A = "sha256:" + "a" * 64
_HASH_B = "sha256:" + "b" * 64


def _present(
    name: str,
    value: str | int | float | bool,
    *,
    origin: BoundValueOrigin = BoundValueOrigin.LITERAL,
    dependencies: tuple[str, ...] = (),
) -> BoundValue:
    return BoundValue(
        name=name,
        declared_value=value,
        effective_value=value,
        state=BoundValueState.PRESENT,
        origin=origin,
        context_dependencies=dependencies,
    )


def _projection() -> RecipeBindingProjection:
    invocation = BoundStepInvocation(
        step_name="dry",
        tool_name="run_skill",
        mode=BindingMode.RECIPE,
        skill_name="dry-walkthrough",
        mcp_kwargs=(
            _present("skill_command", "/dry-walkthrough"),
            _present(
                "cwd",
                "${{ context.worktree_path }}",
                origin=BoundValueOrigin.CONTEXT,
                dependencies=("worktree_path",),
            ),
        ),
        skill_inputs=(
            _present(
                "plan_path",
                "${{ context.plan_path }}",
                origin=BoundValueOrigin.CONTEXT,
                dependencies=("plan_path",),
            ),
            _present("issue_url", ""),
            _present("audit_cycle_path", "/tmp/audit-cycle.json"),
            _present("plan_disposition_path", "/tmp/plan-disposition.json"),
        ),
    )
    return RecipeBindingProjection({"dry": invocation})


def _preflight_projection() -> RecipeBindingProjection:
    invocation = _projection().invocations["dry"]
    return RecipeBindingProjection(
        {
            "dry": replace(
                invocation,
                skill_inputs=(
                    invocation.skill_inputs[0],
                    invocation.skill_inputs[1],
                    BoundValue.absent("audit_cycle_path"),
                    BoundValue.absent("plan_disposition_path"),
                ),
            )
        }
    )


def _wire_recipe_execution_factory(tool_ctx) -> None:
    def _factory(*, snapshot: RecipeExecutionSnapshot, allowed_root: Path):
        from autoskillit.core import InstalledRecipeExecution

        store = DefaultAuditCycleHeadStore()
        return InstalledRecipeExecution(
            snapshot=snapshot,
            runtime_binding_digests={},
            audit_cycle_heads=store,
            input_preflight_resolver=DefaultInputPreflightResolver(
                allowed_root=allowed_root,
                head_store=store,
            ),
        )

    tool_ctx.recipe_execution_factory = _factory


def _artifact(path: Path, digest: str, *, byte_size: int = 1) -> ArtifactRef:
    return ArtifactRef(
        locator=str(path),
        media_type="application/json",
        schema_version=1,
        byte_size=byte_size,
        content_digest=digest,
    )


def _authority(
    tmp_path: Path,
    *,
    generation: str,
    round_: int,
    parent: str | None,
    verdict: AuditVerdict,
    plan_set_id: str = "plans-1",
    part_id: str = "part-a",
    materialize: bool = False,
) -> AuditCycleAuthority:
    plan_ref = _artifact(tmp_path / "plan.md", _HASH_A)
    inventory_ref = _artifact(tmp_path / "inventory.json", _HASH_B)
    remediation_ref = _artifact(tmp_path / "remediation.md", _HASH_A)
    if materialize:
        artifact_payloads = {
            tmp_path / "plan.md": b"audited plan",
            tmp_path / "inventory.json": b'{"schema_version":1}',
            tmp_path / "remediation.md": b"remediation",
        }
        for path, payload in artifact_payloads.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
        plan_ref = _artifact(
            tmp_path / "plan.md",
            compute_bytes_hash(artifact_payloads[tmp_path / "plan.md"]),
            byte_size=len(artifact_payloads[tmp_path / "plan.md"]),
        )
        inventory_ref = _artifact(
            tmp_path / "inventory.json",
            compute_bytes_hash(artifact_payloads[tmp_path / "inventory.json"]),
            byte_size=len(artifact_payloads[tmp_path / "inventory.json"]),
        )
        remediation_ref = _artifact(
            tmp_path / "remediation.md",
            compute_bytes_hash(artifact_payloads[tmp_path / "remediation.md"]),
            byte_size=len(artifact_payloads[tmp_path / "remediation.md"]),
        )
    assessment = AuditAssessmentRow.create(
        requirement_id="REQ-001",
        requirement_text="requirement",
        assessment=AuditAssessment.COVERED,
        evidence_summary="covered",
    )
    return AuditCycleAuthority.create(
        execution_generation=generation,
        cycle_id="cycle-1",
        plan_set_id=plan_set_id,
        scope_id="scope-1",
        part_id=part_id,
        audit_round=round_,
        parent_authority_digest=parent,
        audited_plan_refs=(plan_ref,),
        inventory_ref=inventory_ref,
        assessments=(assessment,),
        verdict=verdict,
        remediation_ref=(remediation_ref if verdict is AuditVerdict.NO_GO else None),
        generated_at="2026-07-23T00:00:00Z",
    )


def _report(authority: AuditCycleAuthority) -> PlanDispositionReport:
    return PlanDispositionReport.create(
        execution_generation=authority.execution_generation,
        cycle_id=authority.cycle_id,
        plan_set_id=authority.plan_set_id,
        scope_id=authority.scope_id,
        part_id=authority.part_id,
        audit_round=authority.audit_round,
        parent_authority_digest=authority.authority_digest,
        inventory_digest=authority.inventory_ref.content_digest,
        findings_digest=authority.findings_digest,
        current_plan_ref=_artifact(
            Path("/virtual/autoskillit-audit-cycle-state-machine/current-plan.md"),
            _HASH_A,
        ),
        dispositions=(
            PlanDispositionRow.create(
                requirement_id="REQ-001",
                disposition=f"satisfied-by-round-{authority.audit_round}",
            ),
        ),
        generated_at="2026-07-23T00:00:00Z",
    )


def _plan_text(round_: int) -> str:
    return f"""\
## Requirements Map
| Requirement ID | Disposition | Implementation Step |
| --- | --- | --- |
| REQ-001 | satisfied-by-round-{round_} | — |

## Implementation Steps
### Step 1
REQ-001 remains satisfied.
"""


def test_delivery_persists_and_installs_matching_execution(
    minimal_ctx,
) -> None:
    _wire_recipe_execution_factory(minimal_ctx)
    finalized = finalize_recipe_delivery(
        {
            "content": "name: demo\n",
            "content_hash": _HASH_A,
            "composite_hash": _HASH_B,
            "valid": True,
        },
        surface="load_recipe",
        recipe_name="demo",
        tool_ctx=minimal_ctx,
        compiled_bindings=_projection(),
    )
    assert finalized.artifact_generation is not None
    assert finalized.execution_snapshot is not None
    persisted = load_recipe_artifact(
        minimal_ctx.temp_dir,
        kitchen_id=minimal_ctx.kitchen_id,
        identity=finalized.artifact_generation,
    )
    execution_payload = persisted["recipe_execution"]
    assert execution_payload["execution_id"] == finalized.execution_snapshot.execution_id
    assert execution_payload["invocation_template_digests"] == dict(
        finalized.execution_snapshot.template_digests
    )
    assert get_recipe_execution(minimal_ctx) is None

    assert complete_finalized_recipe_response(finalized, finalized.rendered) == finalized.rendered
    installed = get_recipe_execution(minimal_ctx)
    assert installed is not None
    assert installed.snapshot is finalized.execution_snapshot
    assert installed.runtime_binding_digests == {}
    assert (
        installed.audit_cycle_heads.get(
            execution_generation=installed.snapshot.execution_id,
            plan_set_id="plans-1",
            scope_id="scope-1",
            part_id="part-a",
        )
        is None
    )


def test_failed_execution_preparation_aborts_receipt_before_commit(
    minimal_ctx,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire_recipe_execution_factory(minimal_ctx)
    finalized = finalize_recipe_delivery(
        {
            "content": "name: demo\n",
            "content_hash": _HASH_A,
            "composite_hash": _HASH_B,
            "valid": True,
        },
        surface="load_recipe",
        recipe_name="demo",
        tool_ctx=minimal_ctx,
        compiled_bindings=_projection(),
    )
    handle = MagicMock()
    ledger = MagicMock()
    ledger.commit.return_value = True
    ledger.abort.return_value = True
    finalized = replace(
        finalized,
        receipt_handle=handle,
        receipt_ledger=ledger,
    )
    monkeypatch.setattr(
        recipe_execution_module,
        "prepare_recipe_execution",
        MagicMock(side_effect=RuntimeError("install failed")),
    )

    result = json.loads(complete_finalized_recipe_response(finalized, finalized.rendered))

    assert result == {
        "success": False,
        "error": "recipe_execution_install_failed",
    }
    ledger.commit.assert_not_called()
    ledger.abort.assert_called_once_with(handle)
    assert get_recipe_execution(minimal_ctx) is None


def test_receipt_commit_failure_preserves_previous_execution(
    tool_ctx_kitchen_open,
) -> None:
    previous_snapshot = build_recipe_execution_snapshot(
        recipe_name="previous",
        content_hash=_HASH_A,
        composite_hash=_HASH_B,
        projection=_projection(),
        execution_id="previous-execution",
    )
    previous = install_recipe_execution(
        tool_ctx_kitchen_open,
        snapshot=previous_snapshot,
    )
    finalized = finalize_recipe_delivery(
        {
            "content": "name: replacement\n",
            "content_hash": _HASH_A,
            "composite_hash": _HASH_B,
            "valid": True,
        },
        surface="load_recipe",
        recipe_name="replacement",
        tool_ctx=tool_ctx_kitchen_open,
        compiled_bindings=_projection(),
    )
    handle = MagicMock()
    ledger = MagicMock()
    ledger.commit.return_value = False
    ledger.abort.return_value = True
    finalized = replace(
        finalized,
        receipt_handle=handle,
        receipt_ledger=ledger,
    )

    result = json.loads(complete_finalized_recipe_response(finalized, finalized.rendered))

    assert result == {
        "success": False,
        "error": "recipe_delivery_receipt_commit_failed",
    }
    ledger.abort.assert_called_once_with(handle)
    assert get_recipe_execution(tool_ctx_kitchen_open) is previous


def test_transformed_delivery_preserves_previous_execution(
    tool_ctx_kitchen_open,
) -> None:
    previous_snapshot = build_recipe_execution_snapshot(
        recipe_name="previous",
        content_hash=_HASH_A,
        composite_hash=_HASH_B,
        projection=_projection(),
        execution_id="previous-execution",
    )
    previous = install_recipe_execution(
        tool_ctx_kitchen_open,
        snapshot=previous_snapshot,
    )
    finalized = finalize_recipe_delivery(
        {
            "content": "name: demo\n",
            "content_hash": _HASH_A,
            "composite_hash": _HASH_B,
            "valid": True,
        },
        surface="load_recipe",
        recipe_name="demo",
        tool_ctx=tool_ctx_kitchen_open,
        compiled_bindings=_projection(),
    )
    assert complete_finalized_recipe_response(finalized, "bounded replacement") == (
        "bounded replacement"
    )
    assert get_recipe_execution(tool_ctx_kitchen_open) is previous


def test_recipe_execution_compilation_failure_logs_exception_context(
    minimal_ctx,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    previous = MagicMock()
    minimal_ctx.active_recipe_execution = previous
    mock_logger = MagicMock()
    monkeypatch.setattr(recipe_delivery_module, "get_logger", lambda _name: mock_logger)
    monkeypatch.setattr(
        recipe_execution_module,
        "build_recipe_execution_snapshot",
        MagicMock(side_effect=ValueError("invalid template")),
    )

    finalized = finalize_recipe_delivery(
        {
            "content": "name: demo\n",
            "content_hash": _HASH_A,
            "composite_hash": _HASH_B,
            "valid": True,
        },
        surface="load_recipe",
        recipe_name="demo",
        tool_ctx=minimal_ctx,
        compiled_bindings=_projection(),
    )

    assert json.loads(finalized.rendered) == {
        "success": False,
        "error": "recipe_execution_unavailable",
    }
    mock_logger.warning.assert_called_once_with(
        "recipe_execution_compilation_failed",
        recipe_name="demo",
        surface="load_recipe",
        error_type="ValueError",
        exc_info=True,
    )
    assert get_recipe_execution(minimal_ctx) is previous


def test_bound_prompt_preserves_falsey_and_metacharacter_values() -> None:
    snapshot = build_recipe_execution_snapshot(
        recipe_name="demo",
        content_hash=_HASH_A,
        composite_hash=_HASH_B,
        projection=_projection(),
        execution_id="execution-1",
    )
    template = snapshot.templates["dry"]
    store = DefaultAuditCycleHeadStore()
    resolver = DefaultInputPreflightResolver(
        allowed_root=Path("/tmp"),
        head_store=store,
    )
    from autoskillit.core import InstalledRecipeExecution

    installed = InstalledRecipeExecution(
        snapshot=snapshot,
        runtime_binding_digests={},
        audit_cycle_heads=store,
        input_preflight_resolver=resolver,
    )
    values = {
        "plan_path": "/tmp/a path/$(touch nope);x.md",
        "issue_url": "",
        "audit_cycle_path": "/tmp/audit-cycle.json",
        "plan_disposition_path": "/tmp/plan-disposition.json",
    }
    bound, _ = bind_attested_runtime_invocation(
        installed,
        execution_id="execution-1",
        step_name="dry",
        template_digest=template.template_digest,
        skill_command="/dry-walkthrough",
        skill_inputs=values,
        actual_mcp_kwargs={
            "skill_command": "/dry-walkthrough",
            "cwd": "/tmp/work tree",
            "step_name": "dry",
            "recipe_execution_id": "execution-1",
            "invocation_template_digest": template.template_digest,
        },
    )
    prompt = build_bound_child_prompt("/dry-walkthrough", bound, None)
    encoded = json.loads(prompt.split("AUTOSKILLIT_BOUND_INVOCATION_V1\n", 1)[1])
    assert encoded["skill_inputs"] == [
        {"name": "plan_path", "value": values["plan_path"]},
        {"name": "issue_url", "value": ""},
        {"name": "audit_cycle_path", "value": "/tmp/audit-cycle.json"},
        {"name": "plan_disposition_path", "value": "/tmp/plan-disposition.json"},
    ]


def test_runtime_binding_rejects_skill_contract_identity_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = build_recipe_execution_snapshot(
        recipe_name="demo",
        content_hash=_HASH_A,
        composite_hash=_HASH_B,
        projection=_projection(),
        execution_id="execution-1",
    )
    template = snapshot.templates["dry"]
    store = DefaultAuditCycleHeadStore()
    from autoskillit.core import InstalledRecipeExecution

    installed = InstalledRecipeExecution(
        snapshot=snapshot,
        runtime_binding_digests={},
        audit_cycle_heads=store,
        input_preflight_resolver=DefaultInputPreflightResolver(
            allowed_root=Path("/tmp"),
            head_store=store,
        ),
    )
    monkeypatch.setattr(
        binding_module,
        "compute_skill_contract_identity",
        lambda *args, **kwargs: "sha256:" + "f" * 64,
    )

    with pytest.raises(RecipeExecutionAdmissionError) as exc_info:
        bind_attested_runtime_invocation(
            installed,
            execution_id="execution-1",
            step_name="dry",
            template_digest=template.template_digest,
            skill_command="/dry-walkthrough",
            skill_inputs={
                "plan_path": "/tmp/plan.md",
                "issue_url": "",
                "audit_cycle_path": "/tmp/audit-cycle.json",
                "plan_disposition_path": "/tmp/plan-disposition.json",
            },
            actual_mcp_kwargs={
                "skill_command": "/dry-walkthrough",
                "cwd": "/tmp",
            },
        )

    assert exc_info.value.code == "recipe_execution_contract_mismatch"


def test_skill_contract_identity_fails_closed_for_stale_contract_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stale_contract = SimpleNamespace(
        audit_authority_publication=None,
        completion_required=True,
        inputs=(),
    )
    monkeypatch.setattr(
        binding_module,
        "get_skill_contract",
        lambda *_args, **_kwargs: stale_contract,
    )

    with pytest.raises(AttributeError, match="input_preflight"):
        binding_module.compute_skill_contract_identity("stale", manifest={})


def test_snapshot_rejects_digest_that_does_not_attest_invocation() -> None:
    snapshot = build_recipe_execution_snapshot(
        recipe_name="demo",
        content_hash=_HASH_A,
        composite_hash=_HASH_B,
        projection=_projection(),
        execution_id="execution-1",
    )
    template = snapshot.templates["dry"]
    tampered = InvocationTemplate(
        invocation=replace(template.invocation, skill_name="different-skill"),
        tool_contract_identity=template.tool_contract_identity,
        skill_contract_identity=template.skill_contract_identity,
        template_digest=template.template_digest,
    )

    with pytest.raises(ValueError, match="invocation template digest mismatch"):
        RecipeExecutionSnapshot(
            execution_id=snapshot.execution_id,
            recipe_name=snapshot.recipe_name,
            content_hash=snapshot.content_hash,
            composite_hash=snapshot.composite_hash,
            templates={"dry": tampered},
            snapshot_digest=snapshot.snapshot_digest,
        )


def test_runtime_binding_digest_attests_mcp_values_and_preflight_evidence() -> None:
    preflight = VerifiedInputPreflightResult(
        decision=InventoryAdmissionDecision.omit(AdmissionReason.NO_AUTHORITY),
        evidence=(PreflightEvidence("inventory_dispositions", "[]"),),
    )
    digest = compute_runtime_binding_digest(
        execution_id="execution-1",
        step_name="dry",
        template_digest=_HASH_A,
        bound_inputs=(("plan_path", "/plans/current.md"),),
        actual_mcp_kwargs={"cwd": "/repo", "output_dir": "/output/a"},
        preflight=preflight,
    )
    changed_mcp_digest = compute_runtime_binding_digest(
        execution_id="execution-1",
        step_name="dry",
        template_digest=_HASH_A,
        bound_inputs=(("plan_path", "/plans/current.md"),),
        actual_mcp_kwargs={"cwd": "/repo", "output_dir": "/output/b"},
        preflight=preflight,
    )
    changed_bound_inputs_digest = compute_runtime_binding_digest(
        execution_id="execution-1",
        step_name="dry",
        template_digest=_HASH_A,
        bound_inputs=(("plan_path", "/plans/changed.md"),),
        actual_mcp_kwargs={"cwd": "/repo", "output_dir": "/output/a"},
        preflight=preflight,
    )
    changed_evidence_digest = compute_runtime_binding_digest(
        execution_id="execution-1",
        step_name="dry",
        template_digest=_HASH_A,
        bound_inputs=(("plan_path", "/plans/current.md"),),
        actual_mcp_kwargs={"cwd": "/repo", "output_dir": "/output/a"},
        preflight=replace(
            preflight,
            evidence=(PreflightEvidence("inventory_dispositions", "[changed]"),),
        ),
    )

    assert digest != changed_mcp_digest
    assert digest != changed_bound_inputs_digest
    assert digest != changed_evidence_digest


@pytest.mark.parametrize("field", ["content_hash", "composite_hash"])
def test_snapshot_rejects_invalid_recipe_hash_identity(field: str) -> None:
    snapshot = build_recipe_execution_snapshot(
        recipe_name="demo",
        content_hash=_HASH_A,
        composite_hash=_HASH_B,
        projection=_projection(),
        execution_id="execution-1",
    )

    with pytest.raises(ValueError, match="canonical sha256"):
        replace(snapshot, **{field: "not-a-recipe-hash"})


def test_published_audit_head_binds_preflight_template_identity(
    tool_ctx_kitchen_open,
) -> None:
    snapshot = build_recipe_execution_snapshot(
        recipe_name="demo",
        content_hash=_HASH_A,
        composite_hash=_HASH_B,
        projection=_projection(),
        execution_id="execution-1",
    )
    install_recipe_execution(tool_ctx_kitchen_open, snapshot=snapshot)
    authority = _authority(
        Path(tool_ctx_kitchen_open.temp_dir),
        generation="execution-1",
        round_=1,
        parent=None,
        verdict=AuditVerdict.NO_GO,
        materialize=True,
    )
    authority_path = Path(tool_ctx_kitchen_open.temp_dir) / "authority.json"
    authority_path.parent.mkdir(parents=True, exist_ok=True)
    authority_path.write_bytes(authority.canonical_bytes)

    publish_verified_audit_cycle(
        tool_ctx_kitchen_open,
        authority_path=str(authority_path),
        expected_parent_digest=None,
        expected_round=0,
    )

    installed = get_recipe_execution(tool_ctx_kitchen_open)
    assert installed is not None
    assert installed.preflight_identities["dry"] == (
        authority.plan_set_id,
        authority.scope_id,
        authority.part_id,
    )


def test_audit_publication_does_not_mutate_replaced_execution(
    tool_ctx_kitchen_open,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = build_recipe_execution_snapshot(
        recipe_name="demo",
        content_hash=_HASH_A,
        composite_hash=_HASH_B,
        projection=_projection(),
        execution_id="execution-1",
    )
    installed = install_recipe_execution(tool_ctx_kitchen_open, snapshot=snapshot)
    replacement = replace(
        installed,
        runtime_binding_digests={"replacement": _HASH_B},
    )
    root = Path(tool_ctx_kitchen_open.temp_dir)
    authority = _authority(
        root,
        generation="execution-1",
        round_=1,
        parent=None,
        verdict=AuditVerdict.NO_GO,
        materialize=True,
    )
    authority_path = root / "authority-replaced.json"
    authority_path.write_bytes(authority.canonical_bytes)
    original_verify = AuditCycleVerifier.verify_artifact_ref
    replaced = False

    def replace_during_verification(
        verifier: AuditCycleVerifier,
        artifact_ref: ArtifactRef,
    ) -> bytes:
        nonlocal replaced
        if not replaced:
            tool_ctx_kitchen_open.active_recipe_execution = replacement
            replaced = True
        return original_verify(verifier, artifact_ref)

    monkeypatch.setattr(
        AuditCycleVerifier,
        "verify_artifact_ref",
        replace_during_verification,
    )

    with pytest.raises(AuditCycleHeadConflict, match="active recipe execution changed"):
        publish_verified_audit_cycle(
            tool_ctx_kitchen_open,
            authority_path=str(authority_path),
            expected_parent_digest=None,
            expected_round=0,
        )

    assert tool_ctx_kitchen_open.active_recipe_execution is replacement
    assert dict(replacement.runtime_binding_digests) == {"replacement": _HASH_B}
    assert (
        replacement.audit_cycle_heads.get(
            execution_generation=authority.execution_generation,
            plan_set_id=authority.plan_set_id,
            scope_id=authority.scope_id,
            part_id=authority.part_id,
        )
        is None
    )


def test_audit_publication_rejects_tampered_referenced_artifact(
    tool_ctx_kitchen_open,
) -> None:
    snapshot = build_recipe_execution_snapshot(
        recipe_name="demo",
        content_hash=_HASH_A,
        composite_hash=_HASH_B,
        projection=_projection(),
        execution_id="execution-1",
    )
    installed = install_recipe_execution(tool_ctx_kitchen_open, snapshot=snapshot)
    root = Path(tool_ctx_kitchen_open.temp_dir)
    authority = _authority(
        root,
        generation="execution-1",
        round_=1,
        parent=None,
        verdict=AuditVerdict.GO,
        materialize=True,
    )
    authority_path = root / "tampered-authority.json"
    authority_path.write_bytes(authority.canonical_bytes)
    Path(authority.inventory_ref.locator).write_bytes(b"tampered")

    with pytest.raises(AuditCycleVerificationError):
        publish_verified_audit_cycle(
            tool_ctx_kitchen_open,
            authority_path=str(authority_path),
            expected_parent_digest=None,
            expected_round=0,
        )

    assert (
        installed.audit_cycle_heads.get(
            execution_generation=authority.execution_generation,
            plan_set_id=authority.plan_set_id,
            scope_id=authority.scope_id,
            part_id=authority.part_id,
        )
        is None
    )


def test_successful_audit_result_publishes_protected_successor_identity(
    tool_ctx_kitchen_open,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = build_recipe_execution_snapshot(
        recipe_name="demo",
        content_hash=_HASH_A,
        composite_hash=_HASH_B,
        projection=_projection(),
        execution_id="execution-1",
    )
    installed = install_recipe_execution(tool_ctx_kitchen_open, snapshot=snapshot)
    authority = _authority(
        Path(tool_ctx_kitchen_open.temp_dir),
        generation="execution-1",
        round_=1,
        parent=None,
        verdict=AuditVerdict.NO_GO,
        materialize=True,
    )
    authority_path = Path(tool_ctx_kitchen_open.temp_dir) / "reported-authority.json"
    authority_path.parent.mkdir(parents=True, exist_ok=True)
    authority_path.write_bytes(authority.canonical_bytes)
    result = SkillResult(
        success=True,
        result=f"authority_file = {authority_path}",
        session_id="audit-session",
        subtype="success",
        is_error=False,
        exit_code=0,
        needs_retry=False,
        retry_reason=RetryReason.NONE,
        stderr="",
        outcome_fields={"authority_file": str(authority_path)},
    )
    monkeypatch.setattr(
        recipe_execution_module,
        "load_bundled_manifest",
        lambda: {
            "skills": {
                "renamed-auditor": {
                    "audit_authority_publication": {
                        "output_field": "authority_file",
                        "prior_input_field": "previous_authority",
                    },
                    "inputs": [
                        {
                            "name": "previous_authority",
                            "type": "file_path",
                            "required": False,
                        }
                    ],
                    "outputs": [{"name": "authority_file", "type": "file_path"}],
                },
                "dry-walkthrough": {
                    "input_preflight": "audit_cycle_inventory",
                    "inputs": [],
                    "outputs": [],
                },
            }
        },
    )

    publish_audit_cycle_result(
        tool_ctx_kitchen_open,
        "renamed-auditor",
        result,
        installed,
        (),
    )

    installed = get_recipe_execution(tool_ctx_kitchen_open)
    assert installed is not None
    assert installed.preflight_identities["dry"] == (
        authority.plan_set_id,
        authority.scope_id,
        authority.part_id,
    )


def test_reported_audit_cycle_cannot_replace_attested_prior_lineage(
    tool_ctx_kitchen_open,
) -> None:
    snapshot = build_recipe_execution_snapshot(
        recipe_name="demo",
        content_hash=_HASH_A,
        composite_hash=_HASH_B,
        projection=_projection(),
        execution_id="execution-1",
    )
    installed = install_recipe_execution(tool_ctx_kitchen_open, snapshot=snapshot)
    root = Path(tool_ctx_kitchen_open.temp_dir)
    prior = _authority(
        root,
        generation="execution-1",
        round_=1,
        parent=None,
        verdict=AuditVerdict.NO_GO,
        materialize=True,
    )
    prior_path = root / "prior-authority.json"
    prior_path.write_bytes(prior.canonical_bytes)
    publish_verified_audit_cycle(
        tool_ctx_kitchen_open,
        authority_path=str(prior_path),
        expected_parent_digest=None,
        expected_round=0,
    )
    unrelated = _authority(
        root,
        generation="execution-1",
        round_=1,
        parent=None,
        verdict=AuditVerdict.NO_GO,
        plan_set_id="plans-unrelated",
        materialize=True,
    )
    unrelated_path = root / "unrelated-authority.json"
    unrelated_path.write_bytes(unrelated.canonical_bytes)

    with pytest.raises(AuditCycleHeadConflict, match="attested prior authority"):
        publish_reported_audit_cycle(
            tool_ctx_kitchen_open,
            authority_path=str(unrelated_path),
            prior_authority_path=str(prior_path),
        )

    current = installed.audit_cycle_heads.get(
        execution_generation=prior.execution_generation,
        plan_set_id=prior.plan_set_id,
        scope_id=prior.scope_id,
        part_id=prior.part_id,
    )
    assert current is not None
    assert current.current_authority_digest == prior.authority_digest
    assert (
        installed.audit_cycle_heads.get(
            execution_generation=unrelated.execution_generation,
            plan_set_id=unrelated.plan_set_id,
            scope_id=unrelated.scope_id,
            part_id=unrelated.part_id,
        )
        is None
    )


@pytest.mark.parametrize(
    ("option_name", "option_value"),
    [
        ("model", "sonnet"),
        ("output_dir", "/tmp/output"),
        ("resume_session_id", "session-1"),
        ("stale_threshold", 30),
    ],
)
def test_runtime_binding_rejects_undeclared_effective_tool_options(
    option_name: str,
    option_value: str | int,
) -> None:
    snapshot = build_recipe_execution_snapshot(
        recipe_name="demo",
        content_hash=_HASH_A,
        composite_hash=_HASH_B,
        projection=_projection(),
        execution_id="execution-1",
    )
    store = DefaultAuditCycleHeadStore()
    from autoskillit.core import InstalledRecipeExecution

    installed = InstalledRecipeExecution(
        snapshot=snapshot,
        runtime_binding_digests={},
        audit_cycle_heads=store,
        input_preflight_resolver=DefaultInputPreflightResolver(
            allowed_root=Path("/tmp"),
            head_store=store,
        ),
    )
    template = snapshot.templates["dry"]
    actual_mcp_kwargs: dict[str, str | int | float | bool] = {
        "skill_command": "/dry-walkthrough",
        "cwd": "/tmp",
        option_name: option_value,
    }

    with pytest.raises(RecipeExecutionAdmissionError) as exc_info:
        bind_attested_runtime_invocation(
            installed,
            execution_id="execution-1",
            step_name="dry",
            template_digest=template.template_digest,
            skill_command="/dry-walkthrough",
            skill_inputs={
                "plan_path": "/tmp/plan.md",
                "issue_url": "",
                "audit_cycle_path": "/tmp/audit-cycle.json",
                "plan_disposition_path": "/tmp/plan-disposition.json",
            },
            actual_mcp_kwargs=actual_mcp_kwargs,
        )
    assert exc_info.value.code == "recipe_execution_tool_shape"
    assert option_name in str(exc_info.value)


def test_runtime_binding_rejects_template_only_tool_override() -> None:
    invocation = _projection().invocations["dry"]
    template_only_cwd = BoundValue(
        name="cwd",
        declared_value="{{AUTOSKILLIT_TEMP}}/worktree",
        effective_value="/resolved/worktree",
        state=BoundValueState.PRESENT,
        origin=BoundValueOrigin.TEMPLATE,
        template_dependencies=("AUTOSKILLIT_TEMP",),
    )
    snapshot = build_recipe_execution_snapshot(
        recipe_name="demo",
        content_hash=_HASH_A,
        composite_hash=_HASH_B,
        projection=RecipeBindingProjection(
            {
                "dry": replace(
                    invocation,
                    mcp_kwargs=(invocation.mcp_kwargs[0], template_only_cwd),
                )
            }
        ),
        execution_id="execution-1",
    )
    store = DefaultAuditCycleHeadStore()
    from autoskillit.core import InstalledRecipeExecution

    installed = InstalledRecipeExecution(
        snapshot=snapshot,
        runtime_binding_digests={},
        audit_cycle_heads=store,
        input_preflight_resolver=DefaultInputPreflightResolver(
            allowed_root=Path("/tmp"),
            head_store=store,
        ),
    )

    with pytest.raises(RecipeExecutionAdmissionError) as exc_info:
        bind_attested_runtime_invocation(
            installed,
            execution_id="execution-1",
            step_name="dry",
            template_digest=snapshot.templates["dry"].template_digest,
            skill_command="/dry-walkthrough",
            skill_inputs={
                "plan_path": "/tmp/plan.md",
                "issue_url": "",
                "audit_cycle_path": "/tmp/audit-cycle.json",
                "plan_disposition_path": "/tmp/plan-disposition.json",
            },
            actual_mcp_kwargs={
                "skill_command": "/dry-walkthrough",
                "cwd": "/caller/override",
            },
        )

    assert exc_info.value.code == "recipe_execution_static_tool_mismatch"


@pytest.mark.parametrize(
    "tamper_target,expected_code",
    [
        ("skill_input", "recipe_execution_static_input_mismatch"),
        ("mcp_parameter", "recipe_execution_static_tool_mismatch"),
    ],
)
def test_runtime_binding_rejects_declared_static_value_tampering(
    tamper_target: str,
    expected_code: str,
) -> None:
    snapshot = build_recipe_execution_snapshot(
        recipe_name="demo",
        content_hash=_HASH_A,
        composite_hash=_HASH_B,
        projection=_projection(),
        execution_id="execution-1",
    )
    store = DefaultAuditCycleHeadStore()
    from autoskillit.core import InstalledRecipeExecution

    installed = InstalledRecipeExecution(
        snapshot=snapshot,
        runtime_binding_digests={},
        audit_cycle_heads=store,
        input_preflight_resolver=DefaultInputPreflightResolver(
            allowed_root=Path("/tmp"),
            head_store=store,
        ),
    )
    runtime_command = (
        "/dry-walkthrough unexpected-inline-value"
        if tamper_target == "mcp_parameter"
        else "/dry-walkthrough"
    )
    runtime_inputs = {
        "plan_path": "/tmp/plan.md",
        "issue_url": "tampered" if tamper_target == "skill_input" else "",
        "audit_cycle_path": "/tmp/audit-cycle.json",
        "plan_disposition_path": "/tmp/plan-disposition.json",
    }

    with pytest.raises(RecipeExecutionAdmissionError) as exc_info:
        bind_attested_runtime_invocation(
            installed,
            execution_id="execution-1",
            step_name="dry",
            template_digest=snapshot.templates["dry"].template_digest,
            skill_command=runtime_command,
            skill_inputs=runtime_inputs,
            actual_mcp_kwargs={"skill_command": runtime_command, "cwd": "/tmp"},
        )

    assert exc_info.value.code == expected_code


def test_runtime_binding_accepts_compiled_inline_skill_arguments() -> None:
    declared_command = (
        "/dry-walkthrough "
        "plan_path=${{ context.plan_path }} "
        "issue_url=${{ context.issue_url }} "
        "audit_cycle_path=${{ context.audit_cycle_path }} "
        "plan_disposition_path=${{ context.plan_disposition_path }}"
    )
    invocation = bind_step_invocation(
        "dry",
        RecipeStep(
            name="dry",
            tool="run_skill",
            with_args={"skill_command": declared_command, "cwd": "/tmp"},
            declared_with_args={"skill_command": declared_command, "cwd": "/tmp"},
        ),
    )
    assert invocation.is_valid
    snapshot = build_recipe_execution_snapshot(
        recipe_name="demo",
        content_hash=_HASH_A,
        composite_hash=_HASH_B,
        projection=RecipeBindingProjection({"dry": invocation}),
        execution_id="execution-1",
    )
    store = DefaultAuditCycleHeadStore()
    from autoskillit.core import InstalledRecipeExecution

    installed = InstalledRecipeExecution(
        snapshot=snapshot,
        runtime_binding_digests={},
        audit_cycle_heads=store,
        input_preflight_resolver=DefaultInputPreflightResolver(
            allowed_root=Path("/tmp"),
            head_store=store,
        ),
    )
    runtime_command = (
        "/dry-walkthrough "
        "plan_path=/tmp/plan.md "
        "issue_url=https://example.test/42 "
        "audit_cycle_path=/tmp/audit.json "
        "plan_disposition_path=/tmp/disposition.json"
    )

    bound, _template = bind_attested_runtime_invocation(
        installed,
        execution_id="execution-1",
        step_name="dry",
        template_digest=snapshot.templates["dry"].template_digest,
        skill_command=runtime_command,
        skill_inputs=None,
        actual_mcp_kwargs={"skill_command": runtime_command, "cwd": "/tmp"},
    )

    assert bound == (
        ("plan_path", "/tmp/plan.md"),
        ("issue_url", "https://example.test/42"),
        ("audit_cycle_path", "/tmp/audit.json"),
        ("plan_disposition_path", "/tmp/disposition.json"),
    )


@pytest.mark.parametrize(
    ("invalid_name", "invalid_value"),
    (("count", "3"), ("enabled", "false")),
)
def test_runtime_binding_rejects_declared_contract_type_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    invalid_name: str,
    invalid_value: str,
) -> None:
    manifest = {
        "skills": {
            "typed-skill": {
                "inputs": [
                    {"name": "count", "type": "integer", "required": True},
                    {"name": "enabled", "type": "boolean", "required": True},
                ]
            }
        }
    }
    monkeypatch.setattr(
        binding_module,
        "load_bundled_manifest",
        lambda: manifest,
    )
    invocation = BoundStepInvocation(
        step_name="typed",
        tool_name="run_skill",
        mode=BindingMode.RECIPE,
        skill_name="typed-skill",
        mcp_kwargs=(
            _present("skill_command", "/typed-skill"),
            _present("cwd", "/tmp"),
        ),
        skill_inputs=(
            _present(
                "count",
                "${{ context.count }}",
                origin=BoundValueOrigin.CONTEXT,
                dependencies=("count",),
            ),
            _present(
                "enabled",
                "${{ context.enabled }}",
                origin=BoundValueOrigin.CONTEXT,
                dependencies=("enabled",),
            ),
        ),
    )
    snapshot = build_recipe_execution_snapshot(
        recipe_name="demo",
        content_hash=_HASH_A,
        composite_hash=_HASH_B,
        projection=RecipeBindingProjection({"typed": invocation}),
        execution_id="execution-1",
    )
    store = DefaultAuditCycleHeadStore()
    resolver = DefaultInputPreflightResolver(
        allowed_root=Path("/tmp"),
        head_store=store,
    )
    from autoskillit.core import InstalledRecipeExecution

    installed = InstalledRecipeExecution(
        snapshot=snapshot,
        runtime_binding_digests={},
        audit_cycle_heads=store,
        input_preflight_resolver=resolver,
    )
    values: dict[str, str | int | float | bool] = {"count": 3, "enabled": False}
    values[invalid_name] = invalid_value

    with pytest.raises(RecipeExecutionAdmissionError) as exc_info:
        bind_attested_runtime_invocation(
            installed,
            execution_id="execution-1",
            step_name="typed",
            template_digest=snapshot.templates["typed"].template_digest,
            skill_command="/typed-skill",
            skill_inputs=values,
            actual_mcp_kwargs={
                "skill_command": "/typed-skill",
                "cwd": "/tmp",
            },
        )
    assert exc_info.value.code == "recipe_execution_input_type"


def test_head_publication_is_monotonic_compare_and_swap(tmp_path: Path) -> None:
    store = DefaultAuditCycleHeadStore()
    first = _authority(
        tmp_path,
        generation="execution-1",
        round_=1,
        parent=None,
        verdict=AuditVerdict.NO_GO,
    )
    first_head = store.publish(
        first,
        expected_parent_digest=None,
        expected_round=0,
    )
    successor = _authority(
        tmp_path,
        generation="execution-1",
        round_=2,
        parent=first.authority_digest,
        verdict=AuditVerdict.GO,
    )
    with pytest.raises(AuditCycleHeadConflict, match="compare-and-swap"):
        store.publish(
            successor,
            expected_parent_digest=_HASH_A,
            expected_round=1,
        )
    terminal = store.publish(
        successor,
        expected_parent_digest=first_head.current_authority_digest,
        expected_round=1,
        authorized_successor_part_id="part-b",
    )
    assert terminal.current_authority_digest == successor.authority_digest
    assert terminal.authorized_successor_part_id == "part-b"


def test_head_publication_does_not_mask_unexpected_verifier_faults(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = DefaultAuditCycleHeadStore()
    first = _authority(
        tmp_path,
        generation="execution-1",
        round_=1,
        parent=None,
        verdict=AuditVerdict.NO_GO,
    )
    store.publish(first, expected_parent_digest=None, expected_round=0)
    successor = _authority(
        tmp_path,
        generation="execution-1",
        round_=2,
        parent=first.authority_digest,
        verdict=AuditVerdict.GO,
    )

    def fail_unexpectedly(*_args: object) -> None:
        raise RuntimeError("verifier implementation fault")

    monkeypatch.setattr(AuditCycleVerifier, "verify_successor", fail_unexpectedly)

    with pytest.raises(RuntimeError, match="implementation fault"):
        store.publish(
            successor,
            expected_parent_digest=first.authority_digest,
            expected_round=1,
        )


class AuditCycleLifecycleStateMachine(RuleBasedStateMachine):
    """Model trusted-head monotonicity across retries, parts, and generations."""

    def __init__(self) -> None:
        super().__init__()
        self.artifact_root = Path("/virtual/autoskillit-audit-cycle-state-machine")
        self.store = DefaultAuditCycleHeadStore()
        self.generation_index = 0
        self.generation = "execution-0"
        self.part_id = "part-a"
        self.model_heads: dict[tuple[str, str], AuditCycleHead] = {}
        self.authorities: dict[str, AuditCycleAuthority] = {}
        self.reports: dict[tuple[str, str], PlanDispositionReport] = {}
        self.report_history: list[PlanDispositionReport] = []
        self.history: list[AuditCycleAuthority] = []

    @initialize()
    def initialize_model(self) -> None:
        self.store.clear_all()
        self.generation_index = 0
        self.generation = "execution-0"
        self.part_id = "part-a"
        self.model_heads.clear()
        self.authorities.clear()
        self.reports.clear()
        self.report_history.clear()
        self.history.clear()

    def _model_key(self) -> tuple[str, str]:
        return self.generation, self.part_id

    @staticmethod
    def _round_trip(authority: AuditCycleAuthority) -> AuditCycleAuthority:
        payload = json.loads(authority.canonical_bytes)
        return AuditCycleAuthority.from_dict(payload)

    @rule(verdict=st.sampled_from((AuditVerdict.NO_GO, AuditVerdict.GO)))
    def publish_current_part(self, verdict: AuditVerdict) -> None:
        current = self.model_heads.get(self._model_key())
        if current is not None and current.verdict is AuditVerdict.GO:
            return
        authority = self._round_trip(
            _authority(
                self.artifact_root,
                generation=self.generation,
                round_=1 if current is None else current.audit_round + 1,
                parent=None if current is None else current.current_authority_digest,
                verdict=verdict,
                part_id=self.part_id,
            )
        )
        successor = f"{self.part_id}-successor" if verdict is AuditVerdict.GO else None
        expected_head = AuditCycleHead(
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
            authorized_successor_part_id=successor,
        )
        published_head = self.store.publish(
            authority,
            expected_parent_digest=(None if current is None else current.current_authority_digest),
            expected_round=0 if current is None else current.audit_round,
            authorized_successor_part_id=successor,
        )
        assert published_head == expected_head
        self.model_heads[self._model_key()] = expected_head
        self.authorities[authority.authority_digest] = authority
        self.reports.pop(self._model_key(), None)
        self.history.append(authority)

    @precondition(
        lambda self: (
            (head := self.model_heads.get(self._model_key())) is not None
            and head.verdict is AuditVerdict.NO_GO
        )
    )
    @rule()
    def publish_or_salvage_plan_report(self) -> None:
        head = self.model_heads[self._model_key()]
        authority = self.authorities[head.current_authority_digest]
        report = PlanDispositionReport.from_dict(json.loads(_report(authority).canonical_bytes))
        self.reports[self._model_key()] = report
        self.report_history.append(report)

    @precondition(
        lambda self: (
            (head := self.model_heads.get(self._model_key())) is not None
            and head.verdict is AuditVerdict.GO
        )
    )
    @rule()
    def terminal_go_rejects_retry(self) -> None:
        current = self.model_heads[self._model_key()]
        successor = self._round_trip(
            _authority(
                self.artifact_root,
                generation=self.generation,
                round_=current.audit_round + 1,
                parent=current.current_authority_digest,
                verdict=AuditVerdict.NO_GO,
                part_id=self.part_id,
            )
        )
        with pytest.raises(AuditCycleHeadConflict, match="terminal GO"):
            self.store.publish(
                successor,
                expected_parent_digest=current.current_authority_digest,
                expected_round=current.audit_round,
            )

    @precondition(lambda self: bool(self.history))
    @rule()
    def stale_replay_and_tamper_reject(self) -> None:
        current = self.model_heads.get(self._model_key())
        if current is not None:
            replay = self.history[0]
            with pytest.raises(AuditCycleHeadConflict):
                self.store.publish(
                    replay,
                    expected_parent_digest=current.current_authority_digest,
                    expected_round=current.audit_round,
                )
        tampered = self.history[-1].to_dict()
        tampered["authority_digest"] = _HASH_A
        with pytest.raises(ValueError, match="digest"):
            AuditCycleAuthority.from_dict(tampered)

    @precondition(lambda self: bool(self.report_history))
    @rule()
    def swapped_report_never_admits(self) -> None:
        head = self.model_heads.get(self._model_key())
        current_report = self.reports.get(self._model_key())
        if head is None or head.verdict is not AuditVerdict.NO_GO:
            return
        authority = self.authorities[head.current_authority_digest]
        stale = next(
            (
                report
                for report in self.report_history
                if report.parent_authority_digest != authority.authority_digest
            ),
            None,
        )
        if stale is None:
            return
        decision = InventoryAdmissionEvaluator().evaluate(
            authority=authority,
            trusted_head=head,
            report=stale,
            expected_generation=self.generation,
            expected_plan_set_id="plans-1",
            expected_scope_id="scope-1",
            expected_part_id=self.part_id,
            current_plan_ref=stale.current_plan_ref,
            inventory_requirement_ids=("REQ-001",),
            current_plan_text=_plan_text(authority.audit_round),
        )
        assert decision.status is AdmissionStatus.REJECT
        assert self.reports.get(self._model_key()) is current_report

    @precondition(
        lambda self: (
            (head := self.model_heads.get(self._model_key())) is not None
            and head.verdict is AuditVerdict.GO
            and head.authorized_successor_part_id is not None
        )
    )
    @rule()
    def advance_to_authorized_sibling_part(self) -> None:
        current = self.model_heads[self._model_key()]
        assert current.authorized_successor_part_id is not None
        self.part_id = current.authorized_successor_part_id

    @rule()
    def replace_execution_generation(self) -> None:
        old_generation = self.generation
        self.store.clear_generation(old_generation)
        self.model_heads = {
            key: head for key, head in self.model_heads.items() if key[0] != old_generation
        }
        self.reports = {
            key: report for key, report in self.reports.items() if key[0] != old_generation
        }
        self.generation_index += 1
        self.generation = f"execution-{self.generation_index}"
        self.part_id = "part-a"
        self.history.clear()

    @rule()
    def close_and_reset(self) -> None:
        self.store.clear_all()
        self.model_heads.clear()
        self.reports.clear()
        self.history.clear()
        self.part_id = "part-a"

    @invariant()
    def trusted_heads_match_independent_model(self) -> None:
        for (generation, part_id), expected in self.model_heads.items():
            assert (
                self.store.get(
                    execution_generation=generation,
                    plan_set_id="plans-1",
                    scope_id="scope-1",
                    part_id=part_id,
                )
                == expected
            )

    @invariant()
    def launch_decision_matches_current_authority_model(self) -> None:
        head = self.model_heads.get(self._model_key())
        authority = self.authorities[head.current_authority_digest] if head is not None else None
        expected_part = self.part_id
        if head is None:
            predecessor = next(
                (
                    candidate
                    for (generation, _part), candidate in self.model_heads.items()
                    if generation == self.generation
                    and candidate.verdict is AuditVerdict.GO
                    and candidate.authorized_successor_part_id == self.part_id
                ),
                None,
            )
            if predecessor is not None:
                head = predecessor
                authority = self.authorities[head.current_authority_digest]
        report = self.reports.get(self._model_key())
        decision = InventoryAdmissionEvaluator().evaluate(
            authority=authority,
            trusted_head=head,
            report=report,
            expected_generation=self.generation,
            expected_plan_set_id="plans-1",
            expected_scope_id="scope-1",
            expected_part_id=expected_part,
            current_plan_ref=report.current_plan_ref if report is not None else None,
            inventory_requirement_ids=("REQ-001",) if report is not None else (),
            current_plan_text=(
                _plan_text(authority.audit_round)
                if authority is not None and report is not None
                else ""
            ),
        )
        if authority is None or authority.verdict is AuditVerdict.GO:
            assert decision.status is AdmissionStatus.OMIT
        elif report is None:
            assert decision.status is AdmissionStatus.REJECT
            assert decision.reason is AdmissionReason.AUTHORITY_WITHOUT_REPORT
        else:
            assert decision.status is AdmissionStatus.PASS


TestAuditCycleLifecycle = AuditCycleLifecycleStateMachine.TestCase
TestAuditCycleLifecycle.settings = settings(
    max_examples=20,
    stateful_step_count=25,
    deadline=None,
)


def test_omit_preflight_performs_zero_artifact_reads(tmp_path: Path) -> None:
    resolver = DefaultInputPreflightResolver(
        allowed_root=tmp_path,
        head_store=DefaultAuditCycleHeadStore(),
    )
    reads = 0

    def fail_reader(*args, **kwargs):
        nonlocal reads
        reads += 1
        raise AssertionError("OMIT must not read inventory or authority artifacts")

    resolver._verifier._reader = fail_reader  # noqa: SLF001
    result = resolver.resolve(
        VerifiedInputPreflightRequest(
            execution_generation="execution-1",
            step_name="dry",
            skill_name="dry-walkthrough",
            plan_path=str(tmp_path / "plan.md"),
            audit_cycle_path=None,
            plan_disposition_path=None,
        )
    )
    assert result.decision.status.value == "OMIT"
    assert reads == 0


def test_preflight_never_derives_expected_identity_from_supplied_authority(
    tmp_path: Path,
) -> None:
    store = DefaultAuditCycleHeadStore()
    authority = _authority(
        tmp_path,
        generation="execution-1",
        round_=1,
        parent=None,
        verdict=AuditVerdict.GO,
    )
    store.publish(authority, expected_parent_digest=None, expected_round=0)
    authority_path = tmp_path / "authority.json"
    authority_path.write_bytes(authority.canonical_bytes)
    resolver = DefaultInputPreflightResolver(allowed_root=tmp_path, head_store=store)

    missing = resolver.resolve(
        VerifiedInputPreflightRequest(
            execution_generation="execution-1",
            step_name="dry",
            skill_name="dry-walkthrough",
            plan_path=str(tmp_path / "plan.md"),
            audit_cycle_path=str(authority_path),
            plan_disposition_path=None,
        )
    )
    wrong_plan_set = resolver.resolve(
        VerifiedInputPreflightRequest(
            execution_generation="execution-1",
            step_name="dry",
            skill_name="dry-walkthrough",
            plan_path=str(tmp_path / "plan.md"),
            audit_cycle_path=str(authority_path),
            plan_disposition_path=None,
            expected_plan_set_id="plans-other",
            expected_scope_id=authority.scope_id,
            expected_part_id=authority.part_id,
        )
    )

    assert missing.decision.reason is AdmissionReason.INTERNAL_ERROR
    assert wrong_plan_set.decision.reason is AdmissionReason.PLAN_SET_MISMATCH


def test_real_preflight_rejects_terminal_go_with_disposition_report(tmp_path: Path) -> None:
    store = DefaultAuditCycleHeadStore()
    authority = _authority(
        tmp_path,
        generation="execution-1",
        round_=1,
        parent=None,
        verdict=AuditVerdict.GO,
    )
    store.publish(authority, expected_parent_digest=None, expected_round=0)
    authority_path = tmp_path / "authority.json"
    authority_path.write_bytes(authority.canonical_bytes)
    report_path = tmp_path / "disposition.json"
    report_path.write_text("{}")
    resolver = DefaultInputPreflightResolver(allowed_root=tmp_path, head_store=store)

    result = resolver.resolve(
        VerifiedInputPreflightRequest(
            execution_generation="execution-1",
            step_name="dry",
            skill_name="dry-walkthrough",
            plan_path=str(tmp_path / "plan.md"),
            audit_cycle_path=str(authority_path),
            plan_disposition_path=str(report_path),
            expected_plan_set_id=authority.plan_set_id,
            expected_scope_id=authority.scope_id,
            expected_part_id=authority.part_id,
        )
    )

    assert result.decision.status is AdmissionStatus.REJECT
    assert result.decision.reason is AdmissionReason.DISPOSITION_MISMATCH


def test_runtime_binding_digest_rejects_replaced_execution(
    tool_ctx_kitchen_open,
) -> None:
    first = build_recipe_execution_snapshot(
        recipe_name="demo",
        content_hash=_HASH_A,
        composite_hash=_HASH_B,
        projection=_projection(),
        execution_id="execution-1",
    )
    install_recipe_execution(tool_ctx_kitchen_open, snapshot=first)
    replacement = build_recipe_execution_snapshot(
        recipe_name="demo",
        content_hash=_HASH_A,
        composite_hash=_HASH_B,
        projection=_projection(),
        execution_id="execution-2",
    )
    active = install_recipe_execution(tool_ctx_kitchen_open, snapshot=replacement)

    with pytest.raises(RecipeExecutionAdmissionError) as exc_info:
        record_runtime_binding_digest(
            tool_ctx_kitchen_open,
            execution_id="execution-1",
            step_name="dry",
            digest=_HASH_A,
        )

    assert exc_info.value.code == "recipe_execution_replaced"
    assert get_recipe_execution(tool_ctx_kitchen_open) is active
    assert dict(active.runtime_binding_digests) == {}


@pytest.mark.anyio
async def test_runtime_attestation_rejects_before_executor(
    tool_ctx_kitchen_open,
    tmp_path: Path,
) -> None:
    snapshot = build_recipe_execution_snapshot(
        recipe_name="demo",
        content_hash=_HASH_A,
        composite_hash=_HASH_B,
        projection=_projection(),
        execution_id="execution-1",
    )
    install_recipe_execution(tool_ctx_kitchen_open, snapshot=snapshot)
    calls_before = len(tool_ctx_kitchen_open.runner.call_args_list)
    result = json.loads(
        await run_skill(
            "/dry-walkthrough",
            str(tmp_path),
            step_name="dry",
            recipe_execution_id="execution-1",
            invocation_template_digest="sha256:" + "f" * 64,
            skill_inputs={
                "plan_path": str(tmp_path / "plan path;$(nope).md"),
                "issue_url": "",
                "audit_cycle_path": False,
                "plan_disposition_path": 0,
            },
        )
    )
    assert result["success"] is False
    assert result["stage"] == "preflight:recipe_execution"
    assert "invocation_template_digest_mismatch" in result["error"]
    assert len(tool_ctx_kitchen_open.runner.call_args_list) == calls_before


@pytest.mark.anyio
async def test_dynamic_recipe_skill_step_executes_without_template_attestation(
    tool_ctx_kitchen_open,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = "%%ORDER_UP::12345678%%"
    monkeypatch.setattr(
        "uuid.uuid4",
        lambda: SimpleNamespace(hex="12345678000000000000000000000000"),
    )
    tool_ctx_kitchen_open.write_expected_resolver = None
    projection = RecipeBindingProjection(
        {
            "fanout": BoundStepInvocation(
                step_name="fanout",
                tool_name="run_skill",
                mode=BindingMode.RECIPE,
                skill_name=None,
                mcp_kwargs=(
                    _present("skill_command", "Choose the applicable audit skills"),
                    _present("cwd", str(tmp_path)),
                ),
                skill_inputs=(),
                failures=(),
            )
        }
    )
    snapshot = build_recipe_execution_snapshot(
        recipe_name="demo",
        content_hash=_HASH_A,
        composite_hash=_HASH_B,
        projection=projection,
        execution_id="execution-1",
    )
    install_recipe_execution(tool_ctx_kitchen_open, snapshot=snapshot)
    assert snapshot.templates == {}
    assert snapshot.dynamic_skill_step_names == frozenset({"fanout"})
    tool_ctx_kitchen_open.runner.push(_make_result(returncode=1))
    tool_ctx_kitchen_open.runner.push(
        _make_result(
            0,
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "result": f"done\n{marker}",
                    "session_id": "session-1",
                }
            ),
            "",
        )
    )
    calls_before = len(tool_ctx_kitchen_open.runner.call_args_list)

    result = json.loads(
        await run_skill(
            "/dry-walkthrough",
            str(tmp_path),
            step_name="fanout",
            skill_inputs={"plan_path": str(tmp_path / "plan.md"), "issue_url": ""},
        )
    )

    assert result["success"] is True
    assert len(tool_ctx_kitchen_open.runner.call_args_list) > calls_before
    installed = get_recipe_execution(tool_ctx_kitchen_open)
    assert installed is not None
    assert dict(installed.runtime_binding_digests) == {}


@pytest.mark.anyio
async def test_runtime_attestation_executes_bound_prompt_and_records_digest(
    tool_ctx_kitchen_open,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = "%%ORDER_UP::12345678%%"
    monkeypatch.setattr(
        "uuid.uuid4",
        lambda: SimpleNamespace(hex="12345678000000000000000000000000"),
    )
    tool_ctx_kitchen_open.write_expected_resolver = None
    snapshot = build_recipe_execution_snapshot(
        recipe_name="demo",
        content_hash=_HASH_A,
        composite_hash=_HASH_B,
        projection=_preflight_projection(),
        execution_id="execution-1",
    )
    installed = install_recipe_execution(tool_ctx_kitchen_open, snapshot=snapshot)

    class RecordingResolver:
        def __init__(self, wrapped: InputPreflightResolver) -> None:
            self._wrapped = wrapped
            self.result: VerifiedInputPreflightResult | None = None

        def resolve(
            self,
            request: VerifiedInputPreflightRequest,
        ) -> VerifiedInputPreflightResult:
            self.result = self._wrapped.resolve(request)
            return self.result

    recording_resolver = RecordingResolver(installed.input_preflight_resolver)
    installed = replace(
        installed,
        input_preflight_resolver=recording_resolver,
    )
    tool_ctx_kitchen_open.active_recipe_execution = installed
    tool_ctx_kitchen_open.runner.push(_make_result(returncode=1))
    tool_ctx_kitchen_open.runner.push(
        _make_result(
            0,
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "result": f"done\n{marker}",
                    "session_id": "session-1",
                }
            ),
            "",
        )
    )
    calls_before = len(tool_ctx_kitchen_open.runner.call_args_list)
    plan_path = str(tmp_path / "plan.md")

    result = json.loads(
        await run_skill(
            "/dry-walkthrough",
            str(tmp_path),
            step_name="dry",
            recipe_execution_id="execution-1",
            invocation_template_digest=snapshot.templates["dry"].template_digest,
            skill_inputs={"plan_path": plan_path, "issue_url": ""},
        )
    )

    assert result["success"] is True
    assert len(tool_ctx_kitchen_open.runner.call_args_list) > calls_before
    cmd = tool_ctx_kitchen_open.runner.call_args_list[-1][0]
    prompt_index = cmd.index("--print") + 1 if "--print" in cmd else cmd.index("-p") + 1
    prompt = cmd[prompt_index]
    assert "AUTOSKILLIT_BOUND_INVOCATION_V1" in prompt
    assert f'"value":"{plan_path}"' in prompt
    recorded = get_recipe_execution(tool_ctx_kitchen_open)
    assert recorded is not None
    assert recording_resolver.result is not None
    expected_digest = compute_runtime_binding_digest(
        execution_id="execution-1",
        step_name="dry",
        template_digest=snapshot.templates["dry"].template_digest,
        bound_inputs=(("plan_path", plan_path), ("issue_url", "")),
        actual_mcp_kwargs={
            "skill_command": "/dry-walkthrough",
            "cwd": str(tmp_path),
            "model": "",
            "step_name": "dry",
            "recipe_execution_id": "execution-1",
            "invocation_template_digest": snapshot.templates["dry"].template_digest,
            "step_provider": "",
            "order_id": "",
            "output_dir": "",
            "resume_session_id": "",
            "closure_authority_path": "",
            "closure_authority_hash": "",
            "closure_plan_paths": "",
            "closure_base_sha": "",
            "closure_diff_sha": "",
            "closure_target_sha": "",
        },
        preflight=recording_resolver.result,
    )
    assert recorded.runtime_binding_digests["dry"] == expected_digest


@pytest.mark.anyio
async def test_preflight_rejects_before_executor(
    tool_ctx_kitchen_open,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RejectingResolver:
        def resolve(self, request):
            assert request.expected_plan_set_id == "plans-1"
            assert request.expected_scope_id == "scope-1"
            assert request.expected_part_id == "part-a"
            return VerifiedInputPreflightResult(
                InventoryAdmissionDecision.reject(
                    AdmissionReason.PLAN_MISMATCH,
                    "verified plan mismatch",
                )
            )

    snapshot = build_recipe_execution_snapshot(
        recipe_name="demo",
        content_hash=_HASH_A,
        composite_hash=_HASH_B,
        projection=_preflight_projection(),
        execution_id="execution-1",
    )
    installed = install_recipe_execution(tool_ctx_kitchen_open, snapshot=snapshot)
    monkeypatch.setattr(
        tool_ctx_kitchen_open,
        "active_recipe_execution",
        replace(
            installed,
            input_preflight_resolver=RejectingResolver(),
            preflight_identities={"dry": ("plans-1", "scope-1", "part-a")},
        ),
    )
    monkeypatch.setattr(
        tool_ctx_kitchen_open,
        "skill_contract_resolver",
        lambda command: SimpleNamespace(input_preflight="audit_cycle_inventory"),
    )
    calls_before = len(tool_ctx_kitchen_open.runner.call_args_list)
    result = json.loads(
        await run_skill(
            "/dry-walkthrough",
            str(tmp_path),
            step_name="dry",
            recipe_execution_id="execution-1",
            invocation_template_digest=snapshot.templates["dry"].template_digest,
            skill_inputs={
                "plan_path": str(tmp_path / "plan.md"),
                "issue_url": "",
            },
        )
    )
    assert result["success"] is False
    assert "input_preflight_plan_mismatch" in result["error"]
    assert len(tool_ctx_kitchen_open.runner.call_args_list) == calls_before
