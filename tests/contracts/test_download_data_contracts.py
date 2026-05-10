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
    assert any("research" in lines[j] for j in range(cats_idx + 1, min(cats_idx + 5, len(lines))))


def test_skill_md_has_output_section() -> None:
    """SKILL.md must have an ## Output section."""
    text = SKILL_PATH.read_text()
    assert "## Output" in text


def test_skill_md_emits_verdict_token() -> None:
    """Output section must document verdict = PASS|FAIL."""
    text = SKILL_PATH.read_text()
    assert "verdict =" in text
    assert "PASS" in text
    assert "FAIL" in text


def test_skill_md_emits_download_report_token() -> None:
    """Output section must document download_report token."""
    text = SKILL_PATH.read_text()
    assert "download_report =" in text


def test_skill_reads_data_manifest() -> None:
    """Skill must read data_manifest from the experiment plan."""
    text = SKILL_PATH.read_text()
    assert "data_manifest" in text


def test_skill_filters_external_and_gitignored() -> None:
    """Skill must filter for source_type external and gitignored entries."""
    text = SKILL_PATH.read_text().lower()
    assert "external" in text
    assert "gitignored" in text


def test_skill_executes_acquisition_commands() -> None:
    """Skill must execute acquisition commands."""
    text = SKILL_PATH.read_text().lower()
    assert "acquisition" in text


def test_skill_respects_depends_on() -> None:
    """Skill must respect depends_on ordering."""
    text = SKILL_PATH.read_text().lower()
    assert "depends_on" in text


def test_skill_writes_download_report() -> None:
    """Skill must write a download report before emitting verdict."""
    text = SKILL_PATH.read_text()
    assert "download_report" in text


def test_skill_verifies_downloads() -> None:
    """Skill must verify downloads after execution."""
    text = SKILL_PATH.read_text().lower()
    assert "verification" in text or "verify" in text


def test_skill_has_stale_threshold_mention() -> None:
    """Skill documentation must reference the stale_threshold context."""
    text = SKILL_PATH.read_text().lower()
    assert "stale" in text or "14400" in text


def test_skill_categories_in_frontmatter() -> None:
    """Frontmatter must define categories before hooks block."""
    text = SKILL_PATH.read_text()
    cats_idx = text.find("categories:")
    hooks_idx = text.find("hooks:")
    assert cats_idx != -1 and hooks_idx != -1 and cats_idx < hooks_idx
