"""Structural assertions for arch-lens skills."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core.io import load_yaml

SKILLS_DIR = Path(__file__).parents[2] / "src/autoskillit/skills_extended"

ARCH_LENS_SLUGS = [
    "c4-container",
    "concurrency",
    "data-lineage",
    "deployment",
    "development",
    "error-resilience",
    "module-dependency",
    "operational",
    "process-flow",
    "repository-access",
    "scenarios",
    "security",
    "state-lifecycle",
]

pytestmark = [pytest.mark.layer("skills"), pytest.mark.medium]


def _read(slug: str) -> str:
    path = SKILLS_DIR / f"arch-lens-{slug}" / "SKILL.md"
    assert path.exists(), f"arch-lens-{slug}/SKILL.md is missing"
    return path.read_text()


def _frontmatter(text: str) -> dict:
    """Parse YAML frontmatter between the first pair of '---' delimiters."""
    lines = text.splitlines()
    if lines[0].strip() != "---":
        return {}
    end = next(i for i, ln in enumerate(lines[1:], 1) if ln.strip() == "---")
    return load_yaml("\n".join(lines[1:end]))


@pytest.mark.parametrize("slug", ARCH_LENS_SLUGS)
def test_skill_md_exists(slug: str) -> None:
    path = SKILLS_DIR / f"arch-lens-{slug}" / "SKILL.md"
    assert path.exists(), f"arch-lens-{slug}/SKILL.md missing"


@pytest.mark.parametrize("slug", ARCH_LENS_SLUGS)
def test_has_arguments_section(slug: str) -> None:
    assert "## Arguments" in _read(slug), f"arch-lens-{slug} missing ## Arguments section"


@pytest.mark.parametrize("slug", ARCH_LENS_SLUGS)
def test_documents_context_path(slug: str) -> None:
    assert "context_path" in _read(slug), f"arch-lens-{slug} must document context_path"


@pytest.mark.parametrize("slug", ARCH_LENS_SLUGS)
def test_has_step_0(slug: str) -> None:
    assert "Step 0" in _read(slug), f"arch-lens-{slug} must have Step 0 for argument parsing"


@pytest.mark.parametrize("slug", ARCH_LENS_SLUGS)
def test_diagram_path_token(slug: str) -> None:
    assert "diagram_path" in _read(slug), f"arch-lens-{slug} must mention diagram_path"


@pytest.mark.parametrize("slug", ARCH_LENS_SLUGS)
def test_arch_diag_prefix_in_output_path(slug: str) -> None:
    assert "arch_diag_" in _read(slug), f"arch-lens-{slug} output path must use arch_diag_ prefix"


@pytest.mark.parametrize("slug", ARCH_LENS_SLUGS)
def test_frontmatter_categories(slug: str) -> None:
    fm = _frontmatter(_read(slug))
    assert fm.get("categories") == ["arch-lens"], (
        f"arch-lens-{slug} frontmatter must have categories: [arch-lens]"
    )


@pytest.mark.parametrize("slug", ARCH_LENS_SLUGS)
def test_frontmatter_activate_deps(slug: str) -> None:
    fm = _frontmatter(_read(slug))
    assert fm.get("activate_deps") == ["mermaid"], (
        f"arch-lens-{slug} frontmatter must have activate_deps: [mermaid]"
    )


@pytest.mark.parametrize("slug", ARCH_LENS_SLUGS)
def test_mermaid_load_instruction(slug: str) -> None:
    text = _read(slug)
    assert "LOAD" in text and "mermaid" in text, (
        f"arch-lens-{slug} must contain mandatory mermaid skill LOAD instruction"
    )


@pytest.mark.parametrize("slug", ARCH_LENS_SLUGS)
def test_autoskillit_temp_write_path(slug: str) -> None:
    assert "{{AUTOSKILLIT_TEMP}}" in _read(slug), (
        f"arch-lens-{slug} must use {{{{AUTOSKILLIT_TEMP}}}} in write path"
    )
