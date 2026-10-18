"""Allocation parsing is strict and step evidence is local to one part."""

from __future__ import annotations

import pytest

from autoskillit.core import PlanSetRejectReason, parse_part_allocation, verify_allocation_evidence

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def _plan_with_allocation(body: str, requirement_id: str, implementation_step: str) -> str:
    return f"""{body}

## Issue Requirement Allocation
| Requirement ID | Allocation | Implementation Step |
| --- | --- | --- |
| {requirement_id} | owned | {implementation_step} |
"""


def _part_with_allocations(body: str, rows: str) -> str:
    return f"""{body}

## Issue Requirement Allocation
| Requirement ID | Allocation | Implementation Step |
| --- | --- | --- |
{rows}
"""


@pytest.mark.parametrize(
    ("body", "requirement_id", "implementation_step", "reason", "detail_parts"),
    (
        pytest.param(
            "### Step 1: R1\nImplement the requirement.",
            "R1",
            "Step 1",
            None,
            (),
            id="bare-id-in-step",
        ),
        pytest.param(
            "### Step 1.1: t\nImplement R1.",
            "R1",
            "Step 1.1",
            None,
            (),
            id="decimal-heading",
        ),
        pytest.param(
            "### Step 3\nImplement R1.",
            "R1",
            "Step 3",
            None,
            (),
            id="heading-without-title",
        ),
        pytest.param(
            "1. Step 1: Implement R1.",
            "R1",
            "Step 1",
            PlanSetRejectReason.ALLOCATION_STEP_MISSING,
            ("Step 1", "no step heading", "found"),
            id="numbered-list-is-not-a-heading",
        ),
        pytest.param(
            "### Step 1: t\n\n- Step 1.1: Implement R1.",
            "R1",
            "Step 1.1",
            PlanSetRejectReason.ALLOCATION_STEP_MISSING,
            ("Step 1.1", "Step 1"),
            id="listed-substep-is-not-a-heading",
        ),
        pytest.param(
            "#### Step 1.1: R1",
            "R1",
            "Step 1.1",
            PlanSetRejectReason.ALLOCATION_STEP_MISSING,
            (),
            id="four-hash-heading",
        ),
        pytest.param(
            "### Step 1 — R1",
            "R1",
            "Step 1",
            PlanSetRejectReason.ALLOCATION_STEP_MISSING,
            (),
            id="em-dash-title-separator",
        ),
        pytest.param(
            "### Step 1.1 - R1",
            "R1",
            "Step 1.1",
            PlanSetRejectReason.ALLOCATION_STEP_MISSING,
            (),
            id="hyphen-title-separator",
        ),
        pytest.param(
            "### Step 1.1: R1\nImplement R1.\n### Step 1.2: R1\nAlso implement R1.",
            "R1",
            "Steps 1, 2",
            PlanSetRejectReason.ALLOCATION_STEP_MISSING,
            ("Steps 1, 2", "Step 1.1", "Step 1.2"),
            id="step-range-cell",
        ),
        pytest.param(
            "### Step 1.1: R1\nImplement R1.\n### Step 1.2: R1\nAlso implement R1.",
            "R1",
            "Step 1.1, Step 1.2",
            PlanSetRejectReason.ALLOCATION_STEP_MISSING,
            ("Step 1.1, Step 1.2", "Step 1.1", "Step 1.2"),
            id="comma-separated-keys",
        ),
        pytest.param(
            "### Step 1.1: R1\nImplement R1.",
            "R1",
            "1.1",
            PlanSetRejectReason.ALLOCATION_STEP_MISSING,
            ("1.1", "Step 1.1"),
            id="bare-decimal-key",
        ),
        pytest.param(
            "### Step 1.1: R1\nImplement R1.",
            "R1",
            "`Step 1.1`",
            PlanSetRejectReason.ALLOCATION_STEP_MISSING,
            ("`Step 1.1`", "Step 1.1"),
            id="backticked-key",
        ),
        pytest.param(
            "### Step 1.1: R1\nImplement R1.",
            "R1",
            "Step 99.9",
            PlanSetRejectReason.ALLOCATION_STEP_MISSING,
            ("Step 99.9", "Step 1.1"),
            id="unknown-step-key",
        ),
        pytest.param(
            "### Step 1.1: implementation\nThe step omits the identifier.",
            "R1",
            "Step 1.1",
            PlanSetRejectReason.ALLOCATION_STEP_UNREFERENCED,
            ("R1", "Step 1.1", "step's own text"),
            id="step-omits-requirement-id",
        ),
        pytest.param(
            "### Step 1.1: P-1\nImplement P-1.",
            "P-1 (Add fn)",
            "Step 1.1",
            PlanSetRejectReason.ALLOCATION_STEP_UNREFERENCED,
            ("P-1 (Add fn)", "Step 1.1"),
            id="annotated-id-parentheses",
        ),
        pytest.param(
            "### Step 1.1: P-1\nImplement P-1.",
            "P-1: Add fn",
            "Step 1.1",
            PlanSetRejectReason.ALLOCATION_STEP_UNREFERENCED,
            ("P-1: Add fn", "Step 1.1"),
            id="annotated-id-colon",
        ),
    ),
)
def test_allocation_evidence_reports_cells_and_found_step_keys(
    body: str,
    requirement_id: str,
    implementation_step: str,
    reason: PlanSetRejectReason | None,
    detail_parts: tuple[str, ...],
) -> None:
    markdown = _plan_with_allocation(body, requirement_id, implementation_step)
    rows = parse_part_allocation(markdown, part_key="P1")

    defects = verify_allocation_evidence(markdown, rows)

    if reason is None:
        assert defects == ()
        return
    assert len(defects) == 1
    found_id, found_reason, detail = defects[0]
    assert found_id == requirement_id
    assert found_reason is reason
    for part in detail_parts:
        assert part in detail
    if "no step heading" in detail_parts:
        lowered = detail.lower()
        assert "no step heading" in lowered
        assert "found" in lowered


def test_allocation_rejects_prose_before_table_with_line_context() -> None:
    sentence = "The implementation plan starts below."
    markdown = f"""### Step 1.1: R1
Implement R1.

## Issue Requirement Allocation
{sentence}
| Requirement ID | Allocation | Implementation Step |
| --- | --- | --- |
| R1 | owned | Step 1.1 |
"""

    with pytest.raises(ValueError) as error:
        parse_part_allocation(markdown, part_key="P1")

    assert "pipe" in str(error.value).lower()
    assert sentence in str(error.value)


def test_allocation_rejects_escaped_pipe_and_quotes_raw_row() -> None:
    raw_row = r"| R1 | owned \| shared | Step 1.1 |"
    markdown = f"""### Step 1.1: R1
Implement R1.

## Issue Requirement Allocation
| Requirement ID | Allocation | Implementation Step |
| --- | --- | --- |
{raw_row}
"""

    with pytest.raises(ValueError) as error:
        parse_part_allocation(markdown, part_key="P1")

    assert "3 columns" in str(error.value)
    assert raw_row in str(error.value)


@pytest.mark.parametrize(
    ("row", "row_identifier"),
    (
        ("| R1 | partial | Step 1.1 |", "R1"),
        ("| R1 | owned |  |", "R1"),
        ("|  | owned | Step 1.1 |", "|  | owned | Step 1.1 |"),
    ),
)
def test_allocation_rejects_invalid_rows_with_row_identity(row: str, row_identifier: str) -> None:
    markdown = f"""### Step 1.1: R1
Implement R1.

## Issue Requirement Allocation
| Requirement ID | Allocation | Implementation Step |
| --- | --- | --- |
{row}
"""

    with pytest.raises(ValueError) as error:
        parse_part_allocation(markdown, part_key="P1")

    assert row_identifier in str(error.value)


def test_allocation_rejects_duplicate_requirement_id() -> None:
    markdown = """### Step 1.1: R1
Implement R1.

## Issue Requirement Allocation
| Requirement ID | Allocation | Implementation Step |
| --- | --- | --- |
| R1 | owned | Step 1.1 |
| R1 | shared | Step 1.1 |
"""

    with pytest.raises(ValueError) as error:
        parse_part_allocation(markdown, part_key="P1")

    assert "R1" in str(error.value)


def test_stage_allocation_parses_obligation_in_third_cell() -> None:
    from autoskillit.core import StageObligation

    rows = parse_part_allocation(
        _part_with_allocations("# Plan", "| P-10 | stage | pr_opened |"), part_key="P1"
    )

    assert len(rows) == 1
    assert rows[0].allocation.value == "stage"
    assert rows[0].implementation_step is None
    assert rows[0].stage_obligation is StageObligation.PR_OPENED


def test_stage_allocation_rejects_unknown_obligation_with_supported_keys() -> None:
    markdown = _part_with_allocations("# Plan", "| P-10 | stage | Step 4 |")

    with pytest.raises(ValueError) as error:
        parse_part_allocation(markdown, part_key="P1")

    message = str(error.value)
    assert "pr_opened" in message
    assert "pr_left_unmerged" in message


def test_requirement_can_have_code_and_stage_allocations() -> None:
    from autoskillit.core import StageObligation

    rows = parse_part_allocation(
        _part_with_allocations(
            "# Plan",
            "| R3 | owned | Step 2 |\n| R3 | stage | pr_opened |",
        ),
        part_key="P1",
    )

    assert len(rows) == 2
    assert rows[0].implementation_step == "Step 2"
    assert rows[1].implementation_step is None
    assert rows[1].stage_obligation is StageObligation.PR_OPENED


def test_duplicate_stage_obligation_pair_is_rejected() -> None:
    markdown = _part_with_allocations(
        "# Plan",
        "| R3 | stage | pr_opened |\n| R3 | stage | pr_opened |",
    )

    with pytest.raises(ValueError) as error:
        parse_part_allocation(markdown, part_key="P1")

    message = str(error.value)
    assert "duplicate" in message.lower()
    assert "R3" in message
    assert "pr_opened" in message


def test_stage_allocation_cited_by_step_is_rejected() -> None:
    markdown = _part_with_allocations(
        "### Step 4: Open PR\nRequirements: P-10\nOpen a pull request.",
        "| P-10 | stage | pr_opened |",
    )
    rows = parse_part_allocation(markdown, part_key="P1")

    defects = verify_allocation_evidence(markdown, rows)

    assert len(defects) == 1
    requirement_id, reason, detail = defects[0]
    assert requirement_id == "P-10"
    assert reason.value == "allocation_stage_cited_by_step"
    assert "P-10" in detail


@pytest.mark.parametrize(("stage_id", "step_citation"), (("P-1", "P-10"), ("P-10", "P-1")))
def test_stage_allocation_citation_uses_requirement_id_boundaries(
    stage_id: str, step_citation: str
) -> None:
    markdown = _part_with_allocations(
        f"### Step 4: Open PR\nRequirements: {step_citation}\nOpen a pull request.",
        f"| {stage_id} | stage | pr_opened |",
    )
    rows = parse_part_allocation(markdown, part_key="P1")

    assert verify_allocation_evidence(markdown, rows) == ()
