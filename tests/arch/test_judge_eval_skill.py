"""Structural integrity tests for the judge-eval skill."""

from pathlib import Path

import pytest

from autoskillit.core.io import load_yaml

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SKILL_DIR = _PROJECT_ROOT / ".autoskillit" / "skills" / "judge-eval"
_SKILL_FILE = _SKILL_DIR / "SKILL.md"


def test_judge_eval_skill_exists() -> None:
    """judge-eval SKILL.md exists at .autoskillit/skills/judge-eval/SKILL.md."""
    assert _SKILL_FILE.is_file(), ".autoskillit/skills/judge-eval/SKILL.md must exist"


def test_judge_eval_frontmatter_name() -> None:
    """Frontmatter name field matches directory name."""
    source = _SKILL_FILE.read_text()
    parts = source.split("---", 2)
    assert len(parts) >= 3, "SKILL.md must have YAML frontmatter"
    fm = load_yaml(parts[1])
    assert isinstance(fm, dict), "frontmatter must parse to dict"
    assert fm.get("name") == "judge-eval"


def test_judge_eval_categories_include_eval() -> None:
    """Frontmatter categories includes eval."""
    source = _SKILL_FILE.read_text()
    parts = source.split("---", 2)
    assert len(parts) >= 3, "SKILL.md must have YAML frontmatter"
    fm = load_yaml(parts[1])
    assert "eval" in fm.get("categories", [])


def test_judge_eval_skill_handles_criteria_types() -> None:
    """judge-eval SKILL.md must contain instructions for handling criteria with a type field.

    Specifically, type: 'recall' criteria must fail on empty output.
    """
    source = _SKILL_FILE.read_text()
    assert "`type` field" in source, "SKILL.md must reference the 'type' field on criteria"
    assert "`recall`" in source, "SKILL.md must mention 'recall' criterion type"
    assert "`precision`" in source, "SKILL.md must mention 'precision' criterion type"
    assert "empty output" in source.lower(), "SKILL.md must address empty output behavior"
