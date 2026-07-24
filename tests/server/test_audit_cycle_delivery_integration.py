"""Attested recipe delivery, runtime binding, and trusted audit-head lifecycle."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from autoskillit.core import (
    AdmissionReason,
    ArtifactRef,
    AuditAssessment,
    AuditAssessmentRow,
    AuditCycleAuthority,
    AuditVerdict,
    BindingMode,
    BoundStepInvocation,
    BoundValue,
    BoundValueOrigin,
    BoundValueState,
    InventoryAdmissionDecision,
    RecipeBindingProjection,
    VerifiedInputPreflightRequest,
    VerifiedInputPreflightResult,
)
from autoskillit.server._recipe_delivery import (
    complete_finalized_recipe_response,
    finalize_recipe_delivery,
    load_recipe_artifact,
)
from autoskillit.server._recipe_execution import (
    AuditCycleHeadConflict,
    DefaultAuditCycleHeadStore,
    DefaultInputPreflightResolver,
    bind_attested_runtime_invocation,
    build_bound_child_prompt,
    build_recipe_execution_snapshot,
    get_recipe_execution,
    install_recipe_execution,
)
from autoskillit.server.tools.tools_execution import run_skill

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
            _present("audit_cycle_path", False),
            _present("plan_disposition_path", 0),
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


def _artifact(path: Path, digest: str) -> ArtifactRef:
    return ArtifactRef(
        locator=str(path),
        media_type="application/json",
        schema_version=1,
        byte_size=1,
        content_digest=digest,
    )


def _authority(
    tmp_path: Path,
    *,
    generation: str,
    round_: int,
    parent: str | None,
    verdict: AuditVerdict,
) -> AuditCycleAuthority:
    assessment = AuditAssessmentRow.create(
        requirement_id="REQ-001",
        requirement_text="requirement",
        assessment=AuditAssessment.COVERED,
        evidence_summary="covered",
    )
    return AuditCycleAuthority.create(
        execution_generation=generation,
        cycle_id="cycle-1",
        plan_set_id="plans-1",
        scope_id="scope-1",
        part_id="part-a",
        audit_round=round_,
        parent_authority_digest=parent,
        audited_plan_refs=(_artifact(tmp_path / "plan.md", _HASH_A),),
        inventory_ref=_artifact(tmp_path / "inventory.json", _HASH_B),
        assessments=(assessment,),
        verdict=verdict,
        remediation_ref=(
            _artifact(tmp_path / "remediation.md", _HASH_A)
            if verdict is AuditVerdict.NO_GO
            else None
        ),
        generated_at="2026-07-23T00:00:00Z",
    )


def test_delivery_persists_and_installs_matching_execution(
    minimal_ctx,
) -> None:
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


def test_transformed_delivery_never_installs_execution(minimal_ctx) -> None:
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
    assert complete_finalized_recipe_response(finalized, "bounded replacement") == (
        "bounded replacement"
    )
    assert get_recipe_execution(minimal_ctx) is None


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
        "audit_cycle_path": False,
        "plan_disposition_path": 0,
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
        },
    )
    prompt = build_bound_child_prompt("/dry-walkthrough", bound, None)
    encoded = json.loads(prompt.split("AUTOSKILLIT_BOUND_INVOCATION_V1\n", 1)[1])
    assert encoded["skill_inputs"] == [
        {"name": "plan_path", "value": values["plan_path"]},
        {"name": "issue_url", "value": ""},
        {"name": "audit_cycle_path", "value": False},
        {"name": "plan_disposition_path", "value": 0},
    ]


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
async def test_preflight_rejects_before_executor(
    tool_ctx_kitchen_open,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RejectingResolver:
        def resolve(self, request):
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
        replace(installed, input_preflight_resolver=RejectingResolver()),
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
