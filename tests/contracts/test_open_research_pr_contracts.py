"""Contract tests for open-research-pr skill — structural invariants."""

from pathlib import Path

SKILL = Path(__file__).parents[2] / "src/autoskillit/skills_extended/open-research-pr/SKILL.md"


def test_skill_file_exists():
    assert SKILL.exists()


def test_documents_all_required_pr_body_sections():
    text = SKILL.read_text()
    for section in [
        "## Recommendation",
        "## Experiment Design",
        "## Key Results",
        "## Methodology",
        "## What We Learned",
    ]:
        assert section in text, f"PR body must include {section}"


def test_documents_exp_lens_invocation():
    text = SKILL.read_text()
    assert "exp-lens" in text
    assert "Skill tool" in text


def test_validated_diagrams_with_node_check():
    text = SKILL.read_text()
    assert "validated_diagrams" in text
    assert any(kw in text for kw in ["treatment", "outcome", "hypothesis"])


def test_anti_prose_guard_in_lens_loop():
    text = SKILL.read_text()
    lower = text.lower()
    assert "for each" in lower and "exp-lens" in lower
    assert any(
        phrase in lower
        for phrase in [
            "do not output",
            "no prose",
            "immediately proceed",
            "no inter-lens prose",
            "without narrative",
        ]
    )


def test_experiment_status_badge_documented():
    text = SKILL.read_text()
    assert "CONCLUSIVE_POSITIVE" in text
    assert "CONCLUSIVE_NEGATIVE" in text
    assert "INCONCLUSIVE" in text
    assert "FAILED" in text


def test_full_report_links_in_pr_body():
    text = SKILL.read_text()
    assert "report_path" in text
    assert "experiment-plan" in text or "experiment_plan" in text


def test_closing_issue_documented_as_optional():
    text = SKILL.read_text()
    assert "closing_issue" in text
    assert "optional" in text.lower() or "[closing_issue]" in text


def test_graceful_degradation_when_gh_unavailable():
    text = SKILL.read_text()
    lower = text.lower()
    assert any(
        phrase in lower for phrase in ["graceful", "unavailable", "not available", "exit 0"]
    )


def test_lens_selection_table_present():
    text = SKILL.read_text()
    assert "benchmark" in text
    assert "causal" in text or "causal_inference" in text


def test_output_contract():
    text = SKILL.read_text()
    assert "pr_url" in text
    assert "%%ORDER_UP%%" in text
