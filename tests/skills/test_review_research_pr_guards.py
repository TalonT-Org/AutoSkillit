"""Behavioral guards for review-research-pr/SKILL.md.

Tests enforce research lens coverage, inconclusive-results contract,
verdict mechanics, and GitHub Reviews API posting requirements.
"""

from pathlib import Path

SKILL_PATH = (
    Path(__file__).parent.parent.parent
    / "src"
    / "autoskillit"
    / "skills_extended"
    / "review-research-pr"
    / "SKILL.md"
)


def _text() -> str:
    return SKILL_PATH.read_text()


def test_review_research_pr_has_research_category() -> None:
    """Frontmatter must declare categories: [research]."""
    text = _text()
    assert "categories:" in text
    # Verify 'research' appears on a categories line, not just anywhere in the file
    assert any("research" in line for line in text.splitlines() if "categories:" in line), (
        "Frontmatter categories: field must include 'research'"
    )


def test_review_research_pr_has_all_seven_research_lenses() -> None:
    """All 7 research audit dimension names must appear in SKILL.md."""
    text = _text()
    research_lenses = [
        "methodology",
        "reproducibility",
        "report-quality",
        "statistical-rigor",
        "isolation",
        "data-integrity",
        "slop",
    ]
    for lens in research_lenses:
        assert lens in text, f"review-research-pr/SKILL.md missing lens: {lens!r}"


def test_review_research_pr_does_not_include_deletion_regression() -> None:
    """deletion_regression lens must not appear — it is a production-code concept."""
    assert "deletion_regression" not in _text(), (
        "review-research-pr must not include the deletion_regression audit dimension. "
        "Research PRs do not have a base-branch deletion contract to enforce."
    )


def test_review_research_pr_treats_inconclusive_as_valid() -> None:
    """SKILL.md must state inconclusive results are valid, not deficiencies."""
    lower = _text().lower()
    assert "inconclusive" in lower, (
        "review-research-pr/SKILL.md must mention 'inconclusive' results"
    )
    assert any(
        kw in lower
        for kw in ["not flag", "valid", "valid outcome", "not a deficiency", "do not flag"]
    ), (
        "review-research-pr/SKILL.md must state that inconclusive results are valid "
        "outcomes not to be flagged as deficiencies."
    )


def test_review_research_pr_finding_schema_includes_requires_decision() -> None:
    """Subagent finding schema must include requires_decision field."""
    assert "requires_decision" in _text()


def test_review_research_pr_verdict_emits_on_final_line() -> None:
    """Skill must instruct emitting verdict= on the final output line."""
    assert "verdict=" in _text(), (
        "review-research-pr/SKILL.md must instruct emitting 'verdict=' so the "
        "recipe capture block can extract it."
    )


def test_review_research_pr_has_lnnn_markers_in_subagent_prompt() -> None:
    """Subagent prompts must instruct use of [LNNN] markers for line numbers."""
    assert "[LNNN]" in _text(), (
        "review-research-pr/SKILL.md subagent prompts must instruct subagents to use "
        "[LNNN] markers for line numbers — not compute them independently."
    )


def test_review_research_pr_uses_reviews_api() -> None:
    """SKILL.md must prescribe the GitHub Reviews API for inline comment posting."""
    import re

    text = _text()
    assert re.search(r"pulls/[^/\s]+/reviews", text), (
        "review-research-pr/SKILL.md must prescribe the GitHub Reviews API "
        "(/repos/{owner}/{repo}/pulls/{n}/reviews) for inline comment posting."
    )


def test_data_scope_dimension_exists() -> None:
    """data-scope must be listed as a review dimension."""
    assert "data-scope" in _text(), (
        "review-research-pr/SKILL.md must include 'data-scope' as an audit dimension"
    )


def test_eight_review_dimensions() -> None:
    """All 8 research audit dimensions must appear in SKILL.md."""
    text = _text()
    dimensions = [
        "methodology",
        "reproducibility",
        "report-quality",
        "statistical-rigor",
        "isolation",
        "data-integrity",
        "slop",
        "data-scope",
    ]
    for dim in dimensions:
        assert dim in text, f"review-research-pr/SKILL.md missing dimension: {dim!r}"
