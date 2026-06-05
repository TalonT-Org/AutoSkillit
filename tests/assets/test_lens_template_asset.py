"""T2-P4-A2 / T2-P4-A1 — Tradition manifest schema and lens skill template asset tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.medium]

_ASSETS_DIR = Path(__file__).resolve().parents[2] / "src" / "autoskillit" / "assets"


def test_lens_skill_template_exists_and_has_required_sections() -> None:
    path = _ASSETS_DIR / "lens-skill-template.md"
    assert path.exists(), f"lens-skill-template.md not found at {path}"
    text = path.read_text()
    for section in [
        "## Arguments",
        "## Critical Constraints",
        "## Analysis Workflow",
        "## Output Template",
        "## Pre-Diagram Checklist",
        "## Related Skills",
    ]:
        assert section in text, f"Missing section: {section}"


def test_lens_skill_template_has_required_variables() -> None:
    path = _ASSETS_DIR / "lens-skill-template.md"
    assert path.exists(), f"lens-skill-template.md not found at {path}"
    text = path.read_text()
    for var in ["{family}", "{slug}", "{output_prefix}", "{parent_skill}"]:
        assert var in text, f"Missing template variable: {var}"


def test_tradition_manifest_schema_exists_and_is_valid_json() -> None:
    path = _ASSETS_DIR / "tradition-manifest-schema" / "tradition-manifest.schema.json"
    assert path.exists(), f"Schema not found at {path}"
    parsed = json.loads(path.read_text())
    assert "$schema" in parsed
    assert "properties" in parsed
