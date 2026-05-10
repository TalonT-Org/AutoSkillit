"""Contract tests for download-data SKILL.md — external dataset acquisition step."""

from pathlib import Path

import pytest

SKILL_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "src"
    / "autoskillit"
    / "skills_extended"
    / "download-data"
    / "SKILL.md"
)


@pytest.fixture(scope="module")
def skill_text() -> str:
    return SKILL_PATH.read_text()


def test_skill_md_exists() -> None:
    """download-data SKILL.md must exist at skills_extended/download-data/SKILL.md."""
    assert SKILL_PATH.exists()


def test_skill_name_matches_directory(skill_text: str) -> None:
    """Frontmatter name field must be 'download-data'."""
    assert "name: download-data" in skill_text


def test_skill_categories_include_research(skill_text: str) -> None:
    """Frontmatter categories must include 'research'."""
    lines = skill_text.splitlines()
    cats_idx = next((i for i, line in enumerate(lines) if "categories:" in line), None)
    assert cats_idx is not None, "categories: block not found in frontmatter"
    assert any("research" in lines[j] for j in range(cats_idx, min(cats_idx + 5, len(lines))))


def test_skill_md_has_output_section(skill_text: str) -> None:
    """SKILL.md must have an ## Output section."""
    assert "## Output" in skill_text


def test_skill_md_emits_verdict_token(skill_text: str) -> None:
    """Output section must document verdict = PASS|FAIL."""
    assert "verdict =" in skill_text
    assert "PASS" in skill_text
    assert "FAIL" in skill_text


def test_skill_md_emits_download_report_token(skill_text: str) -> None:
    """Output section must document download_report token."""
    assert "download_report =" in skill_text


def test_skill_reads_data_manifest(skill_text: str) -> None:
    """Skill must read data_manifest from the experiment plan."""
    assert "data_manifest" in skill_text


def test_skill_filters_external_and_gitignored(skill_text: str) -> None:
    """Skill must filter for source_type external and gitignored entries."""
    lower = skill_text.lower()
    assert "external" in lower
    assert "gitignored" in lower


def test_skill_executes_acquisition_commands(skill_text: str) -> None:
    """Skill must execute acquisition commands."""
    assert "acquisition" in skill_text.lower()


def test_skill_respects_depends_on(skill_text: str) -> None:
    """Skill must respect depends_on ordering."""
    assert "depends_on" in skill_text.lower()


def test_skill_writes_download_report(skill_text: str) -> None:
    """Skill must write a download report before emitting verdict."""
    assert "download_report" in skill_text


def test_skill_verifies_downloads(skill_text: str) -> None:
    """Skill must verify downloads after execution."""
    lower = skill_text.lower()
    assert "verification" in lower or "verify" in lower


def test_skill_has_stale_threshold_mention(skill_text: str) -> None:
    """Skill documentation must reference the stale_threshold context."""
    lower = skill_text.lower()
    assert "stale" in lower or "14400" in lower


def test_skill_categories_in_frontmatter(skill_text: str) -> None:
    """Frontmatter must define categories before hooks block."""
    cats_idx = skill_text.find("categories:")
    hooks_idx = skill_text.find("hooks:")
    assert cats_idx != -1 and hooks_idx != -1 and cats_idx < hooks_idx
