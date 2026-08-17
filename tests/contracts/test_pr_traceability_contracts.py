"""Cross-skill contract tests for requirement traceability across PR lifecycle skills."""

from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.medium]

SKILLS_DIR = Path(__file__).parents[2] / "src/autoskillit/skills_extended"


def _read(skill_name: str) -> str:
    path = SKILLS_DIR / skill_name / "SKILL.md"
    if not path.exists():
        pytest.skip(f"{skill_name}/SKILL.md not found")
    return path.read_text()


def test_pipeline_summary_includes_requirements_from_issue():
    """pipeline-summary must document extracting and embedding requirements from linked issue."""
    text = _read("pipeline-summary")
    normalized = text.lower()
    has_req = "## requirements" in normalized or "# requirements" in normalized
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
    checked = 0
    for skill_name in ["prepare-issue", "open-pr", "pipeline-summary"]:
        text = _read(skill_name)
        if "requirements" in text.lower():
            assert "## Requirements" in text, (
                f"{skill_name}/SKILL.md references requirements but uses wrong header format"
            )
            checked += 1
    assert checked > 0, "No skills with requirements section found — test is vacuous"


def test_pr_skills_bind_exact_body_bytes_to_source_issue_identity():
    prepare = _read("prepare-pr")
    compose = _read("compose-pr")
    integration = _read("open-integration-pr")

    assert "source_issue_url" in prepare
    assert "https://github.com/{owner}/{repo}/issues/{closing_issue}" in prepare
    assert '"schema_version": 1' in compose
    assert '"body_sha256"' in compose
    assert '"closing_issue"' in compose
    assert '"source_issue_url"' in compose
    assert 'with_suffix(".metadata.json")' in compose
    assert '"body_sha256"' in integration
    assert '"source_issue_urls"' in integration
    assert 'with_suffix(".metadata.json")' in integration


def test_issue_4293_delivery_url_is_retained_in_both_pr_paths():
    required_url = "https://github.com/TalonT-Org/AutoSkillit/issues/4293"

    assert required_url in _read("compose-pr")
    assert required_url in _read("open-integration-pr")
