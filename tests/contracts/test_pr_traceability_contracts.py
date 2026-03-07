"""Cross-skill contract tests for requirement traceability across PR lifecycle skills."""

from pathlib import Path

import pytest

SKILLS_DIR = Path(__file__).parents[2] / "src/autoskillit/skills"


def _read(skill_name: str) -> str:
    path = SKILLS_DIR / skill_name / "SKILL.md"
    if not path.exists():
        pytest.skip(f"{skill_name}/SKILL.md not found")
    return path.read_text()


def test_pipeline_summary_includes_requirements_from_issue():
    """pipeline-summary must document extracting and embedding requirements from linked issue."""
    text = _read("pipeline-summary")
    has_req = "## Requirements" in text or "requirements" in text.lower()
    has_issue_fetch = "gh issue view" in text or "closing_issue" in text
    assert has_req, "pipeline-summary must reference requirements"
    assert has_issue_fetch, (
        "pipeline-summary must fetch issue content (for requirements extraction)"
    )


def test_pipeline_summary_pr_body_includes_requirements():
    """pipeline-summary PR body must include requirements section."""
    text = _read("pipeline-summary")
    pr_section = text[text.find("gh pr create") :] if "gh pr create" in text else text
    assert "requirements" in pr_section.lower() or "## Requirements" in pr_section


def test_analyze_prs_surfaces_requirements_in_analysis_plan():
    """analyze-prs must document extracting requirements from PR bodies into analysis plan."""
    text = _read("analyze-prs")
    assert "## Requirements" in text or "requirements" in text.lower()
    assert "pr_analysis_plan" in text or "analysis plan" in text.lower()


def test_merge_pr_includes_requirements_in_conflict_report():
    """merge-pr conflict report must include requirements section for make-plan context."""
    text = _read("merge-pr")
    assert "requirements" in text.lower()
    assert "conflict" in text.lower() and "report" in text.lower()


def test_requirements_section_header_consistent_across_skills():
    """All skills must use identical ## Requirements section header — no variation."""
    for skill_name in ["prepare-issue", "triage-issues", "open-pr", "pipeline-summary"]:
        path = SKILLS_DIR / skill_name / "SKILL.md"
        if not path.exists():
            continue
        text = path.read_text()
        if "requirements" in text.lower():
            assert "## Requirements" in text, (
                f"{skill_name}/SKILL.md references requirements but uses wrong header format"
            )


def test_req_id_format_consistent_across_skills():
    """All skills generating or consuming requirements must use REQ-{GRP}-NNN format."""
    generation_skills = ["prepare-issue", "triage-issues"]
    for skill_name in generation_skills:
        path = SKILLS_DIR / skill_name / "SKILL.md"
        if not path.exists():
            continue
        text = path.read_text()
        assert "REQ-" in text, f"{skill_name}/SKILL.md must reference REQ- identifier format"
