"""Server materialization tests for plan-set authority artifacts."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import (
    PlanSetBindingMode,
    PlanSetBindRequest,
    PlanSetRejectReason,
    verify_plan_set_authority,
)
from autoskillit.server._plan_set_materializer import DefaultPlanSetMaterializer

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium]

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "plan_set"


class _GitHub:
    async def fetch_issue(self, *_args: object, **_kwargs: object) -> dict[str, object]:
        return {
            "success": True,
            "issue_number": 1,
            "body": (_FIXTURES / "split_r1_r2" / "issue.md").read_text(),
        }


@pytest.mark.asyncio
async def test_sealed_binding_writes_content_addressed_authority(tmp_path: Path) -> None:
    fixture = _FIXTURES / "split_r1_r2"
    for name in ("part_a.md", "part_b.md"):
        (tmp_path / name).write_text((fixture / name).read_text(), encoding="utf-8")
    request = PlanSetBindRequest(
        plan_parts_raw=f"{tmp_path / 'part_a.md'}\n{tmp_path / 'part_b.md'}",
        allowed_root=tmp_path,
        issue_url="owner/repo#1",
        parent_authority_path="",
        seal=True,
        step_name="bind",
        execution_generation="generation",
        kitchen_id="kitchen",
        dispatch_id="",
        binding_mode=PlanSetBindingMode.RECIPE,
    )

    result = await DefaultPlanSetMaterializer(_GitHub()).bind(request)

    assert result.success is True
    assert result.coverage_status == "pass"
    assert Path(result.plan_set_authority_path).is_file()


@pytest.mark.asyncio
async def test_coverage_failure_writes_no_authority(tmp_path: Path) -> None:
    fixture = _FIXTURES / "missing_r2"
    for name in ("part_a.md", "part_b.md"):
        (tmp_path / name).write_text((fixture / name).read_text(), encoding="utf-8")
    request = PlanSetBindRequest(
        plan_parts_raw=f"{tmp_path / 'part_a.md'}\n{tmp_path / 'part_b.md'}",
        allowed_root=tmp_path,
        issue_url="owner/repo#1",
        parent_authority_path="",
        seal=True,
        step_name="bind",
        execution_generation="generation",
        kitchen_id="kitchen",
        dispatch_id="",
        binding_mode=PlanSetBindingMode.RECIPE,
    )

    result = await DefaultPlanSetMaterializer(_GitHub()).bind(request)

    assert result.success is False
    assert result.reason is PlanSetRejectReason.COVERAGE_FAILED
    assert not list((tmp_path / "plan-set-authority").rglob("*.json"))


@pytest.mark.asyncio
async def test_renewal_rebinds_walkthrough_edits_before_implementation(tmp_path: Path) -> None:
    fixture = _FIXTURES / "split_r1_r2"
    parts = [tmp_path / name for name in ("part_a.md", "part_b.md")]
    for part in parts:
        part.write_text((fixture / part.name).read_text(), encoding="utf-8")
    request = PlanSetBindRequest(
        plan_parts_raw="\n".join(str(part) for part in parts),
        allowed_root=tmp_path,
        issue_url="owner/repo#1",
        parent_authority_path="",
        seal=True,
        step_name="bind",
        execution_generation="generation",
        kitchen_id="kitchen",
        dispatch_id="",
        binding_mode=PlanSetBindingMode.RECIPE,
    )
    materializer = DefaultPlanSetMaterializer(_GitHub())
    initial = await materializer.bind(request)
    assert initial.success
    parts[0].write_text(
        parts[0]
        .read_text()
        .replace(
            "## Issue Requirement Allocation",
            "Walkthrough clarification for R1.\n\n## Issue Requirement Allocation",
        )
    )

    def verify(path: str):
        return verify_plan_set_authority(
            path,
            allowed_root=tmp_path,
            expected_execution_generation="generation",
            expected_kitchen_id="kitchen",
            expected_binding_mode=PlanSetBindingMode.RECIPE,
            current_plan_path=parts[0],
            require_sealed=True,
        )

    stale = verify(initial.plan_set_authority_path)
    assert stale.reason is PlanSetRejectReason.PART_CONTENT_CHANGED

    renewed = await materializer.bind(
        PlanSetBindRequest(
            plan_parts_raw=request.plan_parts_raw,
            allowed_root=tmp_path,
            issue_url="",
            parent_authority_path=initial.plan_set_authority_path,
            seal=True,
            step_name="renew",
            execution_generation="generation",
            kitchen_id="kitchen",
            dispatch_id="",
            binding_mode=PlanSetBindingMode.RECIPE,
        )
    )
    assert renewed.success
    assert renewed.plan_set_authority_digest != initial.plan_set_authority_digest
    admitted = verify(renewed.plan_set_authority_path)
    assert admitted.accepted
    assert admitted.authority is not None and admitted.authority.revision == 2
    assert admitted.evidence is not None
    assert tuple(item.requirement_id for item in admitted.evidence.assigned_requirements) == (
        "R1",
    )


@pytest.mark.asyncio
async def test_verifier_rejects_foreign_kitchen_and_standalone_binding(tmp_path: Path) -> None:
    fixture = _FIXTURES / "split_r1_r2"
    for name in ("part_a.md", "part_b.md"):
        (tmp_path / name).write_text((fixture / name).read_text(), encoding="utf-8")
    request = PlanSetBindRequest(
        plan_parts_raw=f"{tmp_path / 'part_a.md'}\n{tmp_path / 'part_b.md'}",
        allowed_root=tmp_path,
        issue_url="owner/repo#1",
        parent_authority_path="",
        seal=True,
        step_name="bind",
        execution_generation="generation",
        kitchen_id="kitchen",
        dispatch_id="",
        binding_mode=PlanSetBindingMode.STANDALONE,
    )
    bound = await DefaultPlanSetMaterializer(_GitHub()).bind(request)
    assert bound.success
    foreign = verify_plan_set_authority(
        bound.plan_set_authority_path,
        allowed_root=tmp_path,
        expected_execution_generation="generation",
        expected_kitchen_id="other-kitchen",
        current_plan_path=tmp_path / "part_a.md",
        require_sealed=True,
    )
    assert foreign.reason is PlanSetRejectReason.KITCHEN_ID
    standalone = verify_plan_set_authority(
        bound.plan_set_authority_path,
        allowed_root=tmp_path,
        expected_execution_generation="generation",
        expected_kitchen_id="kitchen",
        expected_binding_mode=PlanSetBindingMode.RECIPE,
        current_plan_path=tmp_path / "part_a.md",
        require_sealed=True,
    )
    assert standalone.reason is PlanSetRejectReason.BINDING_MODE
