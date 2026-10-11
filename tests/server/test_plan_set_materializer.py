"""Server materialization tests for plan-set authority artifacts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoskillit.core import (
    PlanSetBindingMode,
    PlanSetBindRequest,
    PlanSetRejectReason,
    verify_plan_set_authority,
)
from autoskillit.server._plan_set_materializer import DefaultPlanSetMaterializer
from autoskillit.server.recipe._recipe_execution import build_bound_child_prompt

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium]

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "plan_set"


class _GitHub:
    async def fetch_issue(self, *_args: object, **_kwargs: object) -> dict[str, object]:
        return {
            "success": True,
            "issue_number": 1,
            "body": (_FIXTURES / "split_r1_r2" / "issue.md").read_text(),
        }


def _plan_part(
    allocation_rows: str,
    *,
    before_table: str = "",
    steps: str = "### Step 1.1: R1\nImplement R1.",
) -> str:
    return f"""# Plan part
{steps}

## Issue Requirement Allocation
{before_table}
| Requirement ID | Allocation | Implementation Step |
| --- | --- | --- |
{allocation_rows}
"""


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


def _coverage_failure_case(
    case: str,
) -> tuple[tuple[str, ...], str, tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    expected_gaps: tuple[str, ...]
    expected_errors: tuple[str, ...] = ()
    expected_forms: tuple[str, ...] = ()

    if case == "evidence":
        part_texts = (
            _plan_part(
                "| R1 | owned | Steps 1, 2 |\n| R2 (Add fn) | owned | Step 1.2 |",
                steps=("### Step 1.1: R1\nImplement R1.\n\n### Step 1.2: R2\nImplement R2."),
            ),
        )
        issue_url = "owner/repo#1"
        expected_gaps = (
            "R1",
            "R2 (Add fn)",
            "Steps 1, 2",
            "Step 1.1",
            "Step 1.2",
        )
        expected_errors = ("allocation_step_missing", "allocation_step_unreferenced")
        expected_forms = ("Expected exactly one key such as Step 2.1",)
    elif case == "table":
        sentence = "The plan explanation appears before the allocation table."
        part_texts = (_plan_part("| R1 | owned | Step 1.1 |", before_table=sentence),)
        issue_url = "owner/repo#1"
        expected_gaps = (sentence,)
        expected_forms = ("table rows must be pipe-delimited",)
    elif case == "row":
        part_texts = (_plan_part("| R1 | partial | Step 1.1 |"),)
        issue_url = "owner/repo#1"
        expected_gaps = ("R1", "partial")
        expected_forms = ("use owned or shared",)
    elif case == "two_parts":
        part_texts = (
            _plan_part(
                "| R1 | owned | Step 1.1 |",
                before_table="The first part has prose before its table.",
            ),
            _plan_part("| R2 | partial | Step 1.1 |"),
        )
        issue_url = "owner/repo#1"
        expected_gaps = (
            "The first part has prose before its table.",
            "R2",
            "partial",
        )
        expected_forms = ("table rows must be pipe-delimited", "use owned or shared")
    elif case == "part_without_obligations":
        part_texts = (_plan_part(""),)
        issue_url = ""
        expected_gaps = ("part_without_obligations",)
    else:
        fixture = _FIXTURES / "missing_r2"
        part_texts = tuple(
            (fixture / name).read_text(encoding="utf-8") for name in ("part_a.md", "part_b.md")
        )
        issue_url = "owner/repo#1"
        expected_gaps = ("R2 [unassigned]",)

    return part_texts, issue_url, expected_gaps, expected_errors, expected_forms


@pytest.mark.parametrize(
    "case",
    ("evidence", "table", "row", "two_parts", "part_without_obligations", "unassigned"),
)
@pytest.mark.asyncio
async def test_coverage_failures_return_feedback(tmp_path: Path, case: str) -> None:
    part_texts, issue_url, expected_gaps, expected_errors, expected_forms = _coverage_failure_case(
        case
    )

    part_names = (
        ("part_a.md", "part_b.md")
        if case == "unassigned"
        else tuple(f"{case}_part_{index}.md" for index in range(1, len(part_texts) + 1))
    )
    part_paths = tuple(tmp_path / name for name in part_names)
    for path, text in zip(part_paths, part_texts, strict=True):
        path.write_text(text, encoding="utf-8")
    request = PlanSetBindRequest(
        plan_parts_raw="\n".join(str(path) for path in part_paths),
        allowed_root=tmp_path,
        issue_url=issue_url,
        parent_authority_path="",
        seal=True,
        step_name="bind",
        execution_generation="generation",
        kitchen_id="kitchen",
        dispatch_id="",
        binding_mode=PlanSetBindingMode.RECIPE,
    )

    result = await DefaultPlanSetMaterializer(_GitHub()).bind(request)

    assert result.reason is PlanSetRejectReason.COVERAGE_FAILED
    assert result.coverage_gaps.strip()
    if case != "unassigned":
        for path in part_paths:
            assert str(path) in result.coverage_gaps
    for fragment in expected_gaps:
        assert fragment in result.coverage_gaps
    for fragment in expected_errors:
        assert fragment in result.error
    for fragment in expected_forms:
        assert fragment in result.coverage_gaps
    if expected_forms:
        assert "Write a corrected plan part to a new file" in result.coverage_gaps
        assert "keep the same requirements allocated" in result.coverage_gaps


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
    assert "R2 [unassigned]" in result.coverage_gaps
    assert not list((tmp_path / "plan-set-authority").rglob("*.json"))


@pytest.mark.asyncio
async def test_failed_seal_preserves_open_parent_for_gap_replan(tmp_path: Path) -> None:
    fixture = _FIXTURES / "missing_r2"
    for name in ("part_a.md", "part_b.md"):
        (tmp_path / name).write_text((fixture / name).read_text(), encoding="utf-8")
    request = PlanSetBindRequest(
        plan_parts_raw=f"{tmp_path / 'part_a.md'}\n{tmp_path / 'part_b.md'}",
        allowed_root=tmp_path,
        issue_url="owner/repo#1",
        parent_authority_path="",
        seal=False,
        step_name="bind",
        execution_generation="generation",
        kitchen_id="kitchen",
        dispatch_id="",
        binding_mode=PlanSetBindingMode.RECIPE,
    )
    materializer = DefaultPlanSetMaterializer(_GitHub())
    parent = await materializer.bind(request)
    assert parent.success
    before = set((tmp_path / "plan-set-authority").rglob("*.json"))

    failed = await materializer.bind(
        PlanSetBindRequest(
            plan_parts_raw=request.plan_parts_raw,
            allowed_root=tmp_path,
            issue_url="",
            parent_authority_path=parent.plan_set_authority_path,
            seal=True,
            step_name="seal",
            execution_generation="generation",
            kitchen_id="kitchen",
            dispatch_id="",
            binding_mode=PlanSetBindingMode.RECIPE,
        )
    )
    assert failed.reason is PlanSetRejectReason.COVERAGE_FAILED
    assert "R2 [unassigned]" in failed.coverage_gaps
    assert failed.plan_set_authority_path == parent.plan_set_authority_path
    assert failed.plan_set_authority_digest == parent.plan_set_authority_digest
    assert set((tmp_path / "plan-set-authority").rglob("*.json")) == before


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
    assert admitted.evidence.to_dict()["status"] == "admitted"
    assert admitted.evidence.revision == 2
    assert admitted.evidence.part_ordinal == 1
    assert admitted.evidence.part_count == 2
    assert admitted.evidence.part_suffix == "A"
    assert tuple(item.requirement_id for item in admitted.evidence.assigned_requirements) == (
        "R1",
    )
    prompt = build_bound_child_prompt(
        "/autoskillit:dry-walkthrough",
        (("plan_path", str(parts[0])),),
        None,
        plan_set_preflight=admitted.evidence,
    )
    payload = json.loads(prompt.split("AUTOSKILLIT_BOUND_INVOCATION_V1\n", 1)[1])
    assert payload["verified_plan_set_preflight"]["status"] == "admitted"
    assert payload["verified_plan_set_preflight"]["part_count"] == 2


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
        expected_binding_mode=PlanSetBindingMode.STANDALONE,
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
