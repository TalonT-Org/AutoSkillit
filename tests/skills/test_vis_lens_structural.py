"""Structural assertions for P0 vis-lens skills."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

SKILLS_DIR = Path(__file__).parents[2] / "src/autoskillit/skills_extended"

VIS_LENS_SLUGS = [
    "chart-select",
    "uncertainty",
    "antipattern",
    "methodology-norms",
    "always-on",
    "multi-compare",
    "temporal",
    "color-access",
    "figure-table",
    "caption-annot",
    "story-arc",
    "reproducibility",
]

COMPOSITE_SLUGS = {"always-on"}  # emits yaml:spec-index instead of yaml:figure-spec


def _read(slug: str) -> str:
    path = SKILLS_DIR / f"vis-lens-{slug}" / "SKILL.md"
    assert path.exists(), f"vis-lens-{slug}/SKILL.md is missing"
    return path.read_text()


def _frontmatter(text: str) -> dict:
    """Parse YAML frontmatter between the first pair of '---' delimiters."""
    lines = text.splitlines()
    if lines[0].strip() != "---":
        return {}
    end = next(i for i, ln in enumerate(lines[1:], 1) if ln.strip() == "---")
    return yaml.safe_load("\n".join(lines[1:end]))


@pytest.mark.parametrize("slug", VIS_LENS_SLUGS)
def test_skill_md_exists(slug: str) -> None:
    path = SKILLS_DIR / f"vis-lens-{slug}" / "SKILL.md"
    assert path.exists(), f"vis-lens-{slug}/SKILL.md missing"


@pytest.mark.parametrize("slug", VIS_LENS_SLUGS)
def test_has_arguments_section(slug: str) -> None:
    assert "## Arguments" in _read(slug), f"vis-lens-{slug} missing ## Arguments section"


@pytest.mark.parametrize("slug", VIS_LENS_SLUGS)
def test_documents_context_path(slug: str) -> None:
    assert "context_path" in _read(slug), f"vis-lens-{slug} must document context_path"


@pytest.mark.parametrize("slug", VIS_LENS_SLUGS)
def test_documents_experiment_plan_path(slug: str) -> None:
    assert "experiment_plan_path" in _read(slug), (
        f"vis-lens-{slug} must document experiment_plan_path"
    )


@pytest.mark.parametrize("slug", VIS_LENS_SLUGS)
def test_has_step_0(slug: str) -> None:
    assert "Step 0" in _read(slug), f"vis-lens-{slug} must have Step 0 for argument parsing"


@pytest.mark.parametrize("slug", VIS_LENS_SLUGS)
def test_diagram_path_in_constraints(slug: str) -> None:
    text = _read(slug)
    assert "diagram_path" in text, f"vis-lens-{slug} must mention diagram_path in constraints"


@pytest.mark.parametrize("slug", VIS_LENS_SLUGS)
def test_figure_spec_or_spec_index_in_output(slug: str) -> None:
    text = _read(slug)
    if slug in COMPOSITE_SLUGS:
        assert "yaml:spec-index" in text, (
            f"vis-lens-{slug} (composite) must contain yaml:spec-index in output template"
        )
    else:
        assert "yaml:figure-spec" in text, (
            f"vis-lens-{slug} must contain yaml:figure-spec in output template"
        )


@pytest.mark.parametrize("slug", VIS_LENS_SLUGS)
def test_vis_spec_prefix_in_output_path(slug: str) -> None:
    assert "vis_spec_" in _read(slug), f"vis-lens-{slug} output path must use vis_spec_ prefix"


@pytest.mark.parametrize("slug", VIS_LENS_SLUGS)
def test_frontmatter_categories(slug: str) -> None:
    fm = _frontmatter(_read(slug))
    assert fm.get("categories") == ["vis-lens"], (
        f"vis-lens-{slug} frontmatter must have categories: [vis-lens]"
    )


@pytest.mark.parametrize("slug", VIS_LENS_SLUGS)
def test_frontmatter_activate_deps(slug: str) -> None:
    fm = _frontmatter(_read(slug))
    assert fm.get("activate_deps") == ["mermaid"], (
        f"vis-lens-{slug} frontmatter must have activate_deps: [mermaid]"
    )
