"""Server materialization tests for plan-set authority artifacts."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from autoskillit.core import (
    PlanSetBindingMode,
    PlanSetBindRequest,
    PlanSetBindResult,
    PlanSetRejectReason,
    verify_plan_set_authority,
)
from autoskillit.server import _plan_set_materializer as materializer_module
from autoskillit.server._plan_set_materializer import DefaultPlanSetMaterializer
from autoskillit.server.recipe._recipe_execution import build_bound_child_prompt
from tests.server._plan_set_test_helpers import PlanSetGitHub

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium]

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "plan_set"
_PUBLICATION_FIXTURE = _FIXTURES / "publication_pr"
_GitHub = PlanSetGitHub


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


def _write_publication_part(tmp_path: Path) -> Path:
    part = tmp_path / "part_a.md"
    part.write_text((_PUBLICATION_FIXTURE / "part_a.md").read_text(), encoding="utf-8")
    return part


def _publication_request(
    tmp_path: Path,
    part: Path,
    *,
    issue_url: str = "owner/repo#1",
    parent_authority_path: str = "",
    prior_authority_path: str = "",
    seal: bool = True,
    open_pr: bool = False,
    auto_merge: bool = False,
    binding_mode: PlanSetBindingMode = PlanSetBindingMode.RECIPE,
    execution_generation: str = "generation",
) -> PlanSetBindRequest:
    return PlanSetBindRequest(
        plan_parts_raw=str(part),
        allowed_root=tmp_path,
        issue_url=issue_url,
        parent_authority_path=parent_authority_path,
        seal=seal,
        step_name="bind",
        execution_generation=execution_generation,
        kitchen_id="kitchen",
        dispatch_id="",
        binding_mode=binding_mode,
        open_pr=open_pr,
        auto_merge=auto_merge,
        prior_authority_path=prior_authority_path,
    )


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
        expected_forms = ("use owned, shared or stage",)
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
        expected_forms = (
            "table rows must be pipe-delimited",
            "use owned, shared or stage",
        )
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


@pytest.mark.parametrize(
    ("open_pr", "auto_merge", "unavailable"),
    [
        (True, False, ()),
        (False, False, ("R3 → pr_opened", "R4 → pr_left_unmerged")),
        (True, True, ("R4 → pr_left_unmerged",)),
    ],
)
@pytest.mark.asyncio
async def test_sealed_publication_obligations_require_available_stages(
    tmp_path: Path,
    open_pr: bool,
    auto_merge: bool,
    unavailable: tuple[str, ...],
) -> None:
    part = _write_publication_part(tmp_path)
    result = await DefaultPlanSetMaterializer(_GitHub(fixture_dir=_PUBLICATION_FIXTURE)).bind(
        _publication_request(
            tmp_path,
            part,
            open_pr=open_pr,
            auto_merge=auto_merge,
        )
    )

    if unavailable:
        assert result.success is False
        assert result.reason is PlanSetRejectReason.STAGE_OBLIGATION_UNAVAILABLE
        assert result.plan_set_authority_path == ""
        for line in unavailable:
            assert line in result.error
        if auto_merge:
            assert "R3 → pr_opened" not in result.error
        assert not list((tmp_path / "plan-set-authority").rglob("*.json"))
    else:
        assert result.success is True
        assert result.plan_set_state == "sealed"
        assert Path(result.plan_set_authority_path).is_file()


@pytest.mark.parametrize(
    "binding_mode",
    (PlanSetBindingMode.RECIPE, PlanSetBindingMode.STANDALONE),
)
@pytest.mark.asyncio
async def test_publication_obligations_without_recipe_arguments_are_unavailable(
    tmp_path: Path, binding_mode: PlanSetBindingMode
) -> None:
    part = _write_publication_part(tmp_path)

    result = await DefaultPlanSetMaterializer(_GitHub(fixture_dir=_PUBLICATION_FIXTURE)).bind(
        _publication_request(tmp_path, part, binding_mode=binding_mode)
    )

    assert result.success is False
    assert result.reason is PlanSetRejectReason.STAGE_OBLIGATION_UNAVAILABLE
    assert "R3 → pr_opened" in result.error
    assert "R4 → pr_left_unmerged" in result.error


@pytest.mark.asyncio
async def test_open_bind_does_not_check_publication_availability(tmp_path: Path) -> None:
    part = _write_publication_part(tmp_path)

    result = await DefaultPlanSetMaterializer(_GitHub(fixture_dir=_PUBLICATION_FIXTURE)).bind(
        _publication_request(tmp_path, part, seal=False)
    )

    assert result.success is True
    assert result.plan_set_state == "open"


@pytest.mark.asyncio
async def test_identical_sealed_parent_rebind_checks_current_publication_flags(
    tmp_path: Path,
) -> None:
    part = _write_publication_part(tmp_path)
    materializer = DefaultPlanSetMaterializer(_GitHub(fixture_dir=_PUBLICATION_FIXTURE))
    initial = await materializer.bind(_publication_request(tmp_path, part, open_pr=True))
    assert initial.success is True

    rebound = await materializer.bind(
        _publication_request(
            tmp_path,
            part,
            issue_url="",
            parent_authority_path=initial.plan_set_authority_path,
            open_pr=True,
            auto_merge=True,
        )
    )

    assert rebound.success is False
    assert rebound.reason is PlanSetRejectReason.STAGE_OBLIGATION_UNAVAILABLE
    assert "R4 → pr_left_unmerged" in rebound.error
    assert "R3 → pr_opened" not in rebound.error
    assert rebound.plan_set_authority_path == initial.plan_set_authority_path
    assert rebound.plan_set_authority_digest == initial.plan_set_authority_digest


@pytest.mark.parametrize("predecessor", ("parent", "prior"))
@pytest.mark.asyncio
async def test_rebind_cannot_drop_a_sealed_stage_obligation(
    tmp_path: Path, predecessor: str
) -> None:
    part = _write_publication_part(tmp_path)
    materializer = DefaultPlanSetMaterializer(_GitHub(fixture_dir=_PUBLICATION_FIXTURE))
    initial = await materializer.bind(_publication_request(tmp_path, part, open_pr=True))
    assert initial.success is True

    part.write_text(
        part.read_text(encoding="utf-8")
        .replace(
            "### Step 1.2: Validate R2\nAdd regression tests for R2.",
            "### Step 1.2: Validate R2 and implement R4\n"
            "Add regression tests for R2 and implement R4.",
        )
        .replace(
            "| R4 | stage | pr_left_unmerged |",
            "| R4 | owned | Step 1.2 |",
        ),
        encoding="utf-8",
    )
    if predecessor == "parent":
        request = _publication_request(
            tmp_path,
            part,
            issue_url="",
            parent_authority_path=initial.plan_set_authority_path,
            open_pr=True,
        )
    else:
        request = _publication_request(
            tmp_path,
            part,
            prior_authority_path=initial.plan_set_authority_path,
            open_pr=True,
        )

    renewed = await materializer.bind(request)

    assert renewed.success is False
    assert renewed.reason is PlanSetRejectReason.COVERAGE_FAILED
    assert "pr_left_unmerged was sealed earlier in this run" in renewed.coverage_gaps
    assert renewed.plan_set_authority_path == initial.plan_set_authority_path
    assert renewed.plan_set_authority_digest == initial.plan_set_authority_digest


@pytest.mark.asyncio
async def test_prior_authority_from_another_generation_is_rejected(tmp_path: Path) -> None:
    part = _write_publication_part(tmp_path)
    materializer = DefaultPlanSetMaterializer(_GitHub(fixture_dir=_PUBLICATION_FIXTURE))
    foreign = await materializer.bind(
        _publication_request(
            tmp_path,
            part,
            execution_generation="other-generation",
            open_pr=True,
        )
    )
    assert foreign.success is True

    rejected = await materializer.bind(
        _publication_request(
            tmp_path,
            part,
            prior_authority_path=foreign.plan_set_authority_path,
            open_pr=True,
        )
    )

    assert rejected.success is False
    assert rejected.reason is PlanSetRejectReason.EXECUTION_GENERATION
    assert rejected.plan_set_authority_path == ""


def _edit_rebind_part(part: Path, case: str) -> None:
    edits = {
        "coverage": (("| R2 | owned | Step 1.2 |\n", ""),),
        "continuity": (
            (
                "### Step 1.2: Validate R2\nAdd regression tests for R2.",
                "### Step 1.2: Validate R2 and implement R4\n"
                "Add regression tests for R2 and implement R4.",
            ),
            ("| R4 | stage | pr_left_unmerged |", "| R4 | owned | Step 1.2 |"),
        ),
        "artifact_write": (("# Publication-aware plan", "# Updated publication-aware plan"),),
    }
    content = part.read_text(encoding="utf-8")
    for old, new in edits.get(case, ()):
        content = content.replace(old, new)
    part.write_text(content, encoding="utf-8")


@pytest.mark.parametrize(
    ("case", "expected_reason"),
    (
        ("no_parts", PlanSetRejectReason.NO_PARTS),
        ("issue_fetch", PlanSetRejectReason.ISSUE_FETCH_FAILED),
        ("part_read", None),
        ("coverage", PlanSetRejectReason.COVERAGE_FAILED),
        ("availability", PlanSetRejectReason.STAGE_OBLIGATION_UNAVAILABLE),
        ("continuity", PlanSetRejectReason.COVERAGE_FAILED),
        ("artifact_write", PlanSetRejectReason.INTERNAL),
        ("caught_exception", PlanSetRejectReason.INTERNAL),
    ),
)
@pytest.mark.asyncio
async def test_failed_rebind_preserves_the_verified_predecessor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    expected_reason: PlanSetRejectReason | None,
) -> None:
    part = _write_publication_part(tmp_path)
    fixture_client = _GitHub(fixture_dir=_PUBLICATION_FIXTURE)
    materializer = DefaultPlanSetMaterializer(fixture_client)
    predecessor = await materializer.bind(_publication_request(tmp_path, part, open_pr=True))
    assert predecessor.success is True
    _edit_rebind_part(part, case)

    issue_url = "owner/repo#1"
    plan_parts_raw = str(part)
    open_pr = True
    if case == "no_parts":
        plan_parts_raw = " \n"
        issue_url = ""
    elif case == "issue_fetch":
        materializer = DefaultPlanSetMaterializer(
            _GitHub(success=False, fixture_dir=_PUBLICATION_FIXTURE)
        )
    elif case == "part_read":
        plan_parts_raw = str(tmp_path / "missing.md")
        issue_url = ""
    elif case == "availability":
        open_pr = False
    elif case == "caught_exception":

        async def fail_bind(*_args: object, **_kwargs: object) -> PlanSetBindResult:
            raise RuntimeError("injected bind failure")

        monkeypatch.setattr(materializer, "_bind", fail_bind)
    if case == "artifact_write":

        def fail_write(*_args: object, **_kwargs: object) -> None:
            raise OSError("injected artifact write failure")

        monkeypatch.setattr(materializer_module, "write_canonical_versioned_json", fail_write)

    request = _publication_request(
        tmp_path,
        part,
        issue_url=issue_url,
        prior_authority_path=predecessor.plan_set_authority_path,
        open_pr=open_pr,
    )
    request = replace(request, plan_parts_raw=plan_parts_raw)

    failed = await materializer.bind(request)

    assert failed.success is False
    assert failed.plan_set_authority_path == predecessor.plan_set_authority_path
    assert failed.plan_set_authority_digest == predecessor.plan_set_authority_digest
    if expected_reason is not None:
        assert failed.reason is expected_reason


@pytest.mark.asyncio
async def test_failed_first_bind_has_no_authority_reference(tmp_path: Path) -> None:
    result = await DefaultPlanSetMaterializer(_GitHub()).bind(
        PlanSetBindRequest(
            plan_parts_raw="",
            allowed_root=tmp_path,
            issue_url="",
            parent_authority_path="",
            seal=True,
            step_name="bind",
            execution_generation="generation",
            kitchen_id="kitchen",
            dispatch_id="",
            binding_mode=PlanSetBindingMode.RECIPE,
        )
    )

    assert result.success is False
    assert result.reason is PlanSetRejectReason.NO_PARTS
    assert result.plan_set_authority_path == ""
    assert result.plan_set_authority_digest == ""


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
