"""Aggregate coverage and per-part isolation compose on a split issue."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import (
    CoverageStatus,
    PlanPartRef,
    PlanSetAuthority,
    PlanSetBindingMode,
    PlanSetState,
    assigned_requirements,
    compute_bytes_hash,
    evaluate_coverage,
    extract_requirement_inventory,
    parse_part_allocation,
)

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]

_ROOT = Path(__file__).resolve().parents[2]
_FIXTURE = _ROOT / "tests/fixtures/plan_set/split_r1_r2"
_SKILL = _ROOT / "src/autoskillit/skills_extended/dry-walkthrough/SKILL.md"


def test_part_a_receives_only_r1_after_set_level_coverage_passes() -> None:
    extraction = extract_requirement_inventory((_FIXTURE / "issue.md").read_text(), issue_number=1)
    paths = (_FIXTURE / "part_a.md", _FIXTURE / "part_b.md")
    rows = tuple(
        parse_part_allocation(path.read_text(), part_key=f"P{ordinal}")
        for ordinal, path in enumerate(paths, 1)
    )
    coverage = evaluate_coverage(
        extraction.mode,
        extraction.requirements,
        {f"P{ordinal}": item for ordinal, item in enumerate(rows, 1)},
    )
    assert coverage.status is CoverageStatus.PASS
    authority = PlanSetAuthority.create(
        binding_mode=PlanSetBindingMode.RECIPE,
        execution_generation="execution",
        kitchen_id="kitchen",
        dispatch_id="",
        plan_set_authority_id="planset-composition",
        revision=1,
        parent_authority_digest=None,
        state=PlanSetState.SEALED,
        inventory_mode=extraction.mode,
        issue=None,
        requirements=extraction.requirements,
        parts=tuple(
            PlanPartRef(
                ordinal,
                f"P{ordinal}",
                "AB"[ordinal - 1],
                str(path),
                path.stat().st_size,
                compute_bytes_hash(path.read_bytes()),
            )
            for ordinal, path in enumerate(paths, 1)
        ),
        allocations=rows[0] + rows[1],
        coverage=coverage,
        unparsed_marker_lines=(),
        generated_at="2026-09-18T00:00:00Z",
    )
    assert tuple(item.requirement_id for item in assigned_requirements(authority, "P1")) == ("R1",)

    skill = _SKILL.read_text(encoding="utf-8")
    mode_a = skill.split("**A. Plan-set authority mode.", 1)[1].split("**B.", 1)[0]
    assert "each assigned requirement" in mode_a
    assert "every remediation/requirement item enumerated in the source issue" not in mode_a
