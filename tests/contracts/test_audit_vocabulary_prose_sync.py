"""Keep audit-assessment prose synchronized with the admitted enum."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from autoskillit.core import AuditAssessment

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SLICE_AUDITOR = _REPO_ROOT / "src/autoskillit/agents/audit-impl-slice-auditor.md"
_AUDIT_IMPL_SKILL = _REPO_ROOT / "src/autoskillit/skills_extended/audit-impl/SKILL.md"
_DEVIATION_EVALUATOR = _REPO_ROOT / "src/autoskillit/agents/audit-impl-deviation-evaluator.md"
_VERDICT_LABEL = re.compile(r"^- `([A-Z_]+)` —", re.MULTILINE)


def _section(text: str, start_heading: str, end_heading: str) -> str:
    start = text.index(start_heading) + len(start_heading)
    end = text.index(end_heading, start)
    return text[start:end]


def _verdict_labels(text: str) -> set[str]:
    return set(_VERDICT_LABEL.findall(text))


def test_slice_auditor_output_labels_match_the_enum_exactly() -> None:
    output_format = _section(
        _SLICE_AUDITOR.read_text(encoding="utf-8"),
        "## Output Format",
        "## Verdict",
    )

    assert _verdict_labels(output_format) == {member.value for member in AuditAssessment}


def test_audit_impl_step_three_labels_match_the_enum_exactly() -> None:
    step_three = _section(
        _AUDIT_IMPL_SKILL.read_text(encoding="utf-8"),
        "### Step 3 — Audit via Parallel Subagents (SINGLE MESSAGE)",
        "### Step 3.5 — Deviation Evaluation",
    )

    assert _verdict_labels(step_three) == {member.value for member in AuditAssessment}


def test_deviation_evaluator_does_not_hardcode_a_subset_of_blocking_labels() -> None:
    blocking = {member.value for member in AuditAssessment if member.blocking}
    mentioned = {
        label for label in blocking if label in _DEVIATION_EVALUATOR.read_text(encoding="utf-8")
    }

    assert mentioned == blocking, (
        f"deviation evaluator must enumerate every blocking label verbatim "
        f"(missing: {sorted(blocking - mentioned)!r})"
    )


def test_verdict_label_parser_rejects_a_missing_label_fixture(tmp_path: Path) -> None:
    fixture = tmp_path / "missing-label.md"
    fixture.write_text(
        "- `COVERED` — covered\n- `MISSING` — missing\n",
        encoding="utf-8",
    )

    with pytest.raises(AssertionError):
        assert _verdict_labels(fixture.read_text(encoding="utf-8")) == {
            member.value for member in AuditAssessment
        }
