"""Contract tests for review-design SKILL.md — orchestration dispatch, output tokens,
on_context_limit, and retained Critical Constraints.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.medium]

SKILL_MD = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "autoskillit"
    / "skills_extended"
    / "review-design"
    / "SKILL.md"
)


@pytest.fixture(scope="module")
def skill_text() -> str:
    return SKILL_MD.read_text()


def skill_text_between(start_heading: str, end_heading: str, text: str) -> str:
    """Extract SKILL.md text between two headings (start inclusive, end exclusive)."""
    pattern = re.escape(start_heading) + r".*?(?=" + re.escape(end_heading) + r")"
    m = re.search(pattern, text, re.DOTALL)
    assert m, f"Could not find section '{start_heading}' before '{end_heading}' in SKILL.md"
    return m.group(0)


# ── Output token format ──────────────────────────────────────────────────────


def test_output_tokens_all_four_present(skill_text: str) -> None:
    """All four output tokens must be named in the SKILL.md."""
    for token in ["verdict", "experiment_type", "evaluation_dashboard", "revision_guidance"]:
        assert token in skill_text, f"Output token {token!r} not found"


def test_revision_guidance_only_on_revise(skill_text: str) -> None:
    """revision_guidance must be documented as written only when verdict=REVISE."""
    assert "revision_guidance" in skill_text
    assert "REVISE" in skill_text
    lines_with_guidance = [line for line in skill_text.splitlines() if "revision_guidance" in line]
    combined = "\n".join(lines_with_guidance)
    assert "REVISE" in combined or "revise" in combined.lower(), (
        "revision_guidance must be tied to REVISE verdict in its description"
    )


# ── Arguments ─────────────────────────────────────────────────────────────────


def test_scope_report_argument_documented(skill_text: str) -> None:
    """SKILL.md must document scope_report_path in the Arguments section."""
    args_text = skill_text_between("## Arguments", "## Critical Constraints", skill_text)
    assert "scope_report_path" in args_text, (
        "scope_report_path must be documented within the ## Arguments section"
    )


# ── Orchestration dispatch ────────────────────────────────────────────────────


_DISPATCH_XFAIL = pytest.mark.xfail(
    reason="Forward-looking: passes after review-design SKILL.md decomposition",
    strict=True,
)


@_DISPATCH_XFAIL
def test_dispatch_to_classify_experiment_type(skill_text: str) -> None:
    """review-design must name classify-experiment-type as a dispatch target."""
    assert "classify-experiment-type" in skill_text, (
        "review-design/SKILL.md must reference classify-experiment-type as its "
        "experiment classification dispatch target"
    )


@_DISPATCH_XFAIL
def test_dispatch_to_apply_review_dimensions(skill_text: str) -> None:
    """review-design must name apply-review-dimensions as a dispatch target."""
    assert "apply-review-dimensions" in skill_text, (
        "review-design/SKILL.md must reference apply-review-dimensions as its "
        "dimensional analysis dispatch target"
    )


# ── Context limit and gating ─────────────────────────────────────────────────


def test_on_context_limit_verdict_stop_fallback(skill_text: str) -> None:
    """Context Limit Behavior section must be present and document verdict=STOP fallback."""
    assert "Context Limit Behavior" in skill_text, (
        "review-design/SKILL.md must have a ## Context Limit Behavior section"
    )
    ctx_section = skill_text_between("## Context Limit Behavior", "## Workflow", skill_text)
    assert "STOP" in ctx_section, (
        "Context Limit Behavior section must document verdict=STOP as safe fallback"
    )


def test_review_design_input_gating(skill_text: str) -> None:
    """review_design ingredient gating must be referenced in the SKILL.md."""
    assert "review_design" in skill_text, (
        "review-design/SKILL.md must reference the review_design ingredient "
        "as its input gating mechanism"
    )
