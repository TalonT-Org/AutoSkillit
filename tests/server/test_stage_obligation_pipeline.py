"""Real binding and audit admission retain publication work for the recipe gate."""

from __future__ import annotations

import json
import subprocess

import pytest

from autoskillit.core import PlanSetPreflightRequest, RecipeExecutionId, resolve_temp_dir
from autoskillit.recipe.cmd_rpc import _cmd_rpc_merge as merge
from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.server._plan_set_materializer import DefaultPlanSetMaterializer
from autoskillit.server.recipe._plan_set_preflight import DefaultPlanSetPreflightResolver
from autoskillit.server.recipe._recipe_execution import get_recipe_execution
from autoskillit.server.tools import tools_execution
from autoskillit.server.tools.tools_audit_artifacts import write_audit_semantic_result
from autoskillit.server.tools.tools_execution import run_skill
from autoskillit.server.tools.tools_plan_set import bind_plan_set
from tests.server._helpers import _skill_ok
from tests.server._pipeline_test_helpers import _ack_direct_run_skill_result
from tests.server._plan_set_test_helpers import PLAN_SET_FIXTURE, PlanSetGitHub
from tests.server.test_audit_admission_production_seam import _install_attested_recipe

pytestmark = [pytest.mark.layer("server"), pytest.mark.anyio, pytest.mark.medium]


async def _bind_publication(tmp_path, tool_ctx, monkeypatch):
    work_dir = tmp_path / "worktree"
    root = resolve_temp_dir(work_dir, tool_ctx.config.workspace.temp_dir)
    root.mkdir(parents=True)
    fixture = PLAN_SET_FIXTURE.parent / "publication_pr"
    part = root / "plan.md"
    part.write_text((fixture / "part_a.md").read_text())
    monkeypatch.setattr(
        tool_ctx,
        "plan_set_materializer",
        DefaultPlanSetMaterializer(PlanSetGitHub(fixture_dir=fixture)),
    )
    bound = json.loads(
        await bind_plan_set(
            plan_parts=str(part),
            cwd=str(work_dir),
            issue_url="owner/repo#1",
            open_pr="true",
            auto_merge="false",
            step_name="bind_plan_set",
        )
    )
    assert bound["success"] is True, bound
    return work_dir, part, bound


async def test_code_audit_go_then_publication_discharge(
    tmp_path, monkeypatch, tool_ctx_kitchen_open
):
    credential, step = await _install_attested_recipe(monkeypatch, tmp_path, tool_ctx_kitchen_open)
    work_dir, part, bound = await _bind_publication(tmp_path, tool_ctx_kitchen_open, monkeypatch)
    installed = get_recipe_execution(tool_ctx_kitchen_open)
    assert installed is not None
    implementation = DefaultPlanSetPreflightResolver(
        RecipeExecutionId(installed.snapshot.execution_id),
        tool_ctx_kitchen_open.kitchen_id,
    ).resolve(
        PlanSetPreflightRequest(
            execution_generation=installed.snapshot.execution_id,
            expected_kitchen_id=tool_ctx_kitchen_open.kitchen_id,
            step_name="implement",
            skill_name="implement-worktree-no-merge",
            plan_path=str(part),
            plan_set_authority_path=bound["plan_set_authority_path"],
        ),
        allowed_root=part.parent,
    )
    assert implementation.accepted and implementation.evidence is not None
    assert [r.requirement_id for r in implementation.evidence.assigned_requirements] == [
        "R1",
        "R2",
    ]
    assert "stage_obligations" not in implementation.evidence.to_dict()
    deviation = part.parent / "deviations.json"
    deviation.write_text("{}\n")
    prompts = []
    monkeypatch.setattr(tools_execution, "is_feature_enabled", lambda *a, **kw: True)

    async def child(command, _cwd, **kwargs):
        payload = json.loads(command.split("AUTOSKILLIT_BOUND_INVOCATION_V1\n", 1)[1])
        prompts.append(payload)
        assert payload["verified_plan_set_preflight"]["stage_obligations"] == [
            {"requirement_id": "R3", "obligation": "pr_opened"},
            {"requirement_id": "R4", "obligation": "pr_left_unmerged"},
        ]
        submission = payload["audit_semantic_submission"]
        written = json.loads(
            await write_audit_semantic_result(
                reservation_handle=submission["reservation_handle"],
                audited_plan_refs=submission["audited_plan_refs"],
                assessments=[
                    {
                        "requirement_id": rid,
                        "requirement_text": text,
                        "assessment": "COVERED",
                        "evidence_summary": "Delivered code and focused tests.",
                    }
                    for rid, text in [
                        ("R1", "Implement the requested change."),
                        ("R2", "Test the change."),
                    ]
                ],
                verdict="GO",
            )
        )
        assert written["success"] is True, written
        result = _skill_ok("semantic publication")
        result.outcome_fields = {
            "audit_semantic_result_path": written["audit_semantic_result_path"]
        }
        return result

    monkeypatch.setattr(tool_ctx_kitchen_open.executor, "run", child)
    args = step["with"]
    published = json.loads(
        await run_skill(
            skill_command=args["skill_command"],
            cwd=str(work_dir),
            step_name=args["step_name"],
            output_dir=args["output_dir"],
            recipe_execution_id=credential["execution_id"],
            invocation_template_digest=credential["invocation_template_digests"]["audit_impl"],
            skill_inputs={
                "all_plan_paths": str(part),
                "branch_name": "impl-publication",
                "base_branch": "develop",
                "deviation_manifest_path": str(deviation),
                "prior_audit_cycle_path": "",
                "plan_set_authority_path": bound["plan_set_authority_path"],
            },
            closure_plan_paths=str(part),
            closure_base_sha="impl-publication",
        )
    )
    assert len(prompts) == 1, published
    assert published["success"] is True and published["audit_verdict"] == "GO", published
    _ack_direct_run_skill_result(tool_ctx_kitchen_open, published)
    state = {"value": "OPEN"}
    monkeypatch.setattr(
        merge,
        "run_gh",
        lambda *a, **kw: subprocess.CompletedProcess(
            [],
            0,
            json.dumps({"state": state["value"], "baseRefName": "main"}),
        ),
    )
    verify = getattr(merge, "verify_stage_obligations")
    gate_args = (
        bound["plan_set_authority_path"],
        bound["plan_set_authority_digest"],
        str(work_dir),
        "main",
        "42",
    )
    assert verify(*gate_args)["success"] == "true"
    state["value"] = "MERGED"
    failed = verify(*gate_args)
    assert failed["success"] == "false" and "pr_left_unmerged" in failed["detail"]


async def test_zero_change_gate_refuses_to_close_publication_obligation(
    tmp_path,
    monkeypatch,
    tool_ctx_kitchen_open,
):
    await _install_attested_recipe(monkeypatch, tmp_path, tool_ctx_kitchen_open)
    work_dir, _, bound = await _bind_publication(tmp_path, tool_ctx_kitchen_open, monkeypatch)
    calls = []
    monkeypatch.setattr(merge, "run_gh", lambda *a, **kw: calls.append(a))
    result = getattr(merge, "verify_stage_obligations")(
        bound["plan_set_authority_path"],
        bound["plan_set_authority_digest"],
        str(work_dir),
        "main",
    )
    assert result["success"] == "false"
    assert "no pull request was published" in result["detail"]
    assert calls == []
    recipe = load_recipe(builtin_recipes_dir() / "remediation.yaml")
    routes = recipe.steps["pr_gate_no_changes"].on_result
    assert routes is not None
    assert next(c.route for c in routes.conditions if c.when is None) == "release_issue_failure"
