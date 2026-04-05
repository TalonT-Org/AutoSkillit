"""Contract tests for plan-experiment SKILL.md — data provenance lifecycle."""

from pathlib import Path

SKILL_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "src"
    / "autoskillit"
    / "skills_extended"
    / "plan-experiment"
    / "SKILL.md"
)


def test_data_manifest_in_frontmatter_schema() -> None:
    text = SKILL_PATH.read_text()
    assert "data_manifest" in text


def test_data_manifest_required_fields() -> None:
    text = SKILL_PATH.read_text()
    after_manifest = text.lower().split("data_manifest")[1][:2000]
    for field in ("source_type", "acquisition", "verification", "hypothesis"):
        assert field in after_manifest, f"data_manifest missing field: {field}"


def test_directive_data_acquisition_requirement() -> None:
    text = SKILL_PATH.read_text()
    lower = text.lower()
    assert "directive" in lower
    assert "acquisition" in lower
