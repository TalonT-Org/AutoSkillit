"""Server materialization tests for plan-set authority artifacts."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import PlanSetBindingMode, PlanSetBindRequest, PlanSetRejectReason
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
