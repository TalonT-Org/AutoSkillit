"""Tests that all arch-lens skills have an ## Arguments section and context_path handling."""
from pathlib import Path
import pytest

SKILLS_DIR = Path(__file__).parents[2] / "src/autoskillit/skills_extended"
ARCH_LENS_SLUGS = [
    "c4-container", "concurrency", "data-lineage", "deployment", "development",
    "error-resilience", "module-dependency", "operational", "process-flow",
    "repository-access", "scenarios", "security", "state-lifecycle",
]


@pytest.mark.parametrize("slug", ARCH_LENS_SLUGS)
def test_arch_lens_has_arguments_section(slug):
    path = SKILLS_DIR / f"arch-lens-{slug}/SKILL.md"
    assert path.exists(), f"arch-lens-{slug}/SKILL.md missing"
    text = path.read_text()
    assert "## Arguments" in text, f"arch-lens-{slug} missing ## Arguments section"


@pytest.mark.parametrize("slug", ARCH_LENS_SLUGS)
def test_arch_lens_documents_context_path_arg(slug):
    path = SKILLS_DIR / f"arch-lens-{slug}/SKILL.md"
    text = path.read_text()
    assert "context_path" in text, f"arch-lens-{slug} must document context_path argument"


@pytest.mark.parametrize("slug", ARCH_LENS_SLUGS)
def test_arch_lens_has_step_0_for_context(slug):
    path = SKILLS_DIR / f"arch-lens-{slug}/SKILL.md"
    text = path.read_text()
    assert "Step 0" in text, f"arch-lens-{slug} must have Step 0 for context reading"
