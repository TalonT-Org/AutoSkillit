"""Server plan-set preflight binds verified evidence into a child invocation."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from autoskillit.core import (
    PlanSetBindingMode,
    PlanSetBindRequest,
    PlanSetPreflightRequest,
    PlanSetRejectReason,
    PreflightKind,
    RecipeExecutionId,
)
from autoskillit.server._plan_set_materializer import DefaultPlanSetMaterializer
from autoskillit.server.recipe._plan_set_preflight import DefaultPlanSetPreflightResolver
from autoskillit.server.recipe._recipe_execution import (
    RecipeExecutionAdmissionError,
    build_bound_child_prompt,
    resolve_attested_input_preflight,
)
from tests.server.test_plan_set_materializer import _FIXTURES, _GitHub

pytestmark = [pytest.mark.layer("server"), pytest.mark.anyio, pytest.mark.medium]


async def _authority(tmp_path: Path, *, sealed: bool = True) -> tuple[str, Path]:
    fixture = _FIXTURES / "split_r1_r2"
    parts = (tmp_path / "part_a.md", tmp_path / "part_b.md")
    for part in parts:
        part.write_text((fixture / part.name).read_text(), encoding="utf-8")
    bound = await DefaultPlanSetMaterializer(_GitHub()).bind(
        PlanSetBindRequest(
            plan_parts_raw="\n".join(str(part) for part in parts),
            allowed_root=tmp_path,
            issue_url="owner/repo#1",
            parent_authority_path="",
            seal=sealed,
            step_name="bind",
            execution_generation="generation",
            kitchen_id="kitchen",
            dispatch_id="",
            binding_mode=PlanSetBindingMode.RECIPE,
        )
    )
    assert bound.success
    return bound.plan_set_authority_path, parts[0]


async def test_sealed_resolver_and_prompt_emit_assigned_subset(tmp_path: Path) -> None:
    assert PreflightKind("plan_set_coverage") is PreflightKind.PLAN_SET_COVERAGE
    path, part = await _authority(tmp_path)
    resolver = DefaultPlanSetPreflightResolver(RecipeExecutionId("generation"), "kitchen")
    verified = resolver.resolve(
        PlanSetPreflightRequest(
            execution_generation="generation",
            expected_kitchen_id="kitchen",
            step_name="verify",
            skill_name="dry-walkthrough",
            plan_path=str(part),
            plan_set_authority_path=path,
        ),
        allowed_root=tmp_path,
    )
    assert verified.accepted and verified.evidence is not None
    prompt = build_bound_child_prompt(
        "/autoskillit:dry-walkthrough",
        (("plan_path", str(part)),),
        None,
        plan_set_preflight=verified.evidence,
    )
    payload = json.loads(prompt.split("AUTOSKILLIT_BOUND_INVOCATION_V1\n", 1)[1])
    evidence = payload["verified_plan_set_preflight"]
    assert evidence["status"] == "admitted"
    assert [row["requirement_id"] for row in evidence["assigned_requirements"]] == ["R1"]


async def test_unsealed_authority_is_rejected(tmp_path: Path) -> None:
    path, part = await _authority(tmp_path, sealed=False)
    result = DefaultPlanSetPreflightResolver(RecipeExecutionId("generation"), "kitchen").resolve(
        PlanSetPreflightRequest(
            execution_generation="generation",
            expected_kitchen_id="kitchen",
            step_name="verify",
            skill_name="dry-walkthrough",
            plan_path=str(part),
            plan_set_authority_path=path,
        ),
        allowed_root=tmp_path,
    )
    assert result.reason is PlanSetRejectReason.AUTHORITY_NOT_SEALED


async def test_absent_authority_omits_evidence_and_kitchen_error_is_namespaced(
    tmp_path: Path,
) -> None:
    path, part = await _authority(tmp_path)
    resolver = DefaultPlanSetPreflightResolver(RecipeExecutionId("generation"), "kitchen")
    tool_ctx = SimpleNamespace(
        kitchen_id="other-kitchen",
        skill_contract_resolver=lambda _command: SimpleNamespace(
            input_preflight=("plan_set_coverage",)
        ),
    )
    installed = SimpleNamespace(plan_set_preflight_resolver=resolver)
    template = SimpleNamespace(invocation=SimpleNamespace(skill_name="dry-walkthrough"))
    bound = (("plan_path", str(part)), ("plan_set_authority_path", ""))
    result = resolve_attested_input_preflight(
        tool_ctx,
        installed,
        skill_command="/autoskillit:dry-walkthrough",
        execution_id="generation",
        step_name="verify",
        template=template,
        bound_inputs=bound,
        allowed_root=tmp_path,
    )
    assert result.plan_set is None
    with pytest.raises(RecipeExecutionAdmissionError) as exc_info:
        resolve_attested_input_preflight(
            tool_ctx,
            installed,
            skill_command="/autoskillit:dry-walkthrough",
            execution_id="generation",
            step_name="verify",
            template=template,
            bound_inputs=(("plan_path", str(part)), ("plan_set_authority_path", path)),
            allowed_root=tmp_path,
        )
    assert exc_info.value.code == "input_preflight_plan_set_kitchen_mismatch"
