"""Tests for recipe/diagrams.py — DG-1 through DG-11."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.recipe.diagrams import (
    check_diagram_staleness,
    diagram_stale_to_suggestions,
    generate_recipe_diagram,
    load_recipe_diagram,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_SAMPLE_RECIPE_YAML = """\
name: my-recipe
description: A test recipe for diagram generation
summary: step1 -> done
ingredients:
  task:
    description: What to do
    required: true
steps:
  step1:
    tool: run_skill
    with:
      skill_command: "/autoskillit:investigate ${{ inputs.task }}"
      cwd: "."
    on_success: done
    on_failure: escalate
  done:
    action: stop
    message: "Done."
  escalate:
    action: stop
    message: "Failed."
kitchen_rules:
  - "Use AutoSkillit tools only"
"""


@pytest.fixture
def sample_recipe_yaml(tmp_path: Path) -> Path:
    path = tmp_path / "my-recipe.yaml"
    path.write_text(_SAMPLE_RECIPE_YAML)
    return path


# ---------------------------------------------------------------------------
# DG-1 through DG-5: generate_recipe_diagram
# ---------------------------------------------------------------------------


def test_generate_creates_file(tmp_path: Path, sample_recipe_yaml: Path) -> None:
    """DG-1: generate_recipe_diagram writes diagrams/{name}.md."""
    recipes_dir = tmp_path / "recipes"
    generate_recipe_diagram(sample_recipe_yaml, recipes_dir)
    assert (recipes_dir / "diagrams" / f"{sample_recipe_yaml.stem}.md").exists()


def test_generate_embeds_hash_comment(tmp_path: Path, sample_recipe_yaml: Path) -> None:
    """DG-2: diagram begins with hash comment."""
    recipes_dir = tmp_path / "recipes"
    generate_recipe_diagram(sample_recipe_yaml, recipes_dir)
    content = (recipes_dir / "diagrams" / f"{sample_recipe_yaml.stem}.md").read_text()
    assert content.startswith("<!-- autoskillit-recipe-hash: sha256:")


def test_generate_content_has_name(tmp_path: Path, sample_recipe_yaml: Path) -> None:
    """DG-3: diagram contains recipe name header."""
    recipes_dir = tmp_path / "recipes"
    generate_recipe_diagram(sample_recipe_yaml, recipes_dir)
    content = (recipes_dir / "diagrams" / f"{sample_recipe_yaml.stem}.md").read_text()
    assert "## " in content  # name header present


def test_generate_route_table(tmp_path: Path, sample_recipe_yaml: Path) -> None:
    """DG-4: diagram contains route table headers."""
    recipes_dir = tmp_path / "recipes"
    generate_recipe_diagram(sample_recipe_yaml, recipes_dir)
    content = (recipes_dir / "diagrams" / f"{sample_recipe_yaml.stem}.md").read_text()
    assert "✓ success" in content
    assert "✗ failure" in content


def test_generate_ingredients_table(tmp_path: Path, sample_recipe_yaml: Path) -> None:
    """DG-5: diagram contains ingredients table."""
    recipes_dir = tmp_path / "recipes"
    generate_recipe_diagram(sample_recipe_yaml, recipes_dir)
    content = (recipes_dir / "diagrams" / f"{sample_recipe_yaml.stem}.md").read_text()
    assert "### Ingredients" in content


# ---------------------------------------------------------------------------
# DG-6 through DG-7: load_recipe_diagram
# ---------------------------------------------------------------------------


def test_load_returns_none_when_missing(tmp_path: Path) -> None:
    """DG-6: load_recipe_diagram returns None when diagram missing."""
    assert load_recipe_diagram("no-such-recipe", tmp_path / "recipes") is None


def test_load_returns_content(tmp_path: Path, sample_recipe_yaml: Path) -> None:
    """DG-7: load_recipe_diagram returns diagram string when file exists."""
    recipes_dir = tmp_path / "recipes"
    generate_recipe_diagram(sample_recipe_yaml, recipes_dir)
    content = load_recipe_diagram(sample_recipe_yaml.stem, recipes_dir)
    assert content is not None
    assert "<!-- autoskillit-recipe-hash:" in content


# ---------------------------------------------------------------------------
# DG-8 through DG-10: check_diagram_staleness
# ---------------------------------------------------------------------------


def test_check_staleness_fresh(tmp_path: Path, sample_recipe_yaml: Path) -> None:
    """DG-8: check_diagram_staleness returns False when hash matches."""
    recipes_dir = tmp_path / "recipes"
    generate_recipe_diagram(sample_recipe_yaml, recipes_dir)
    assert (
        check_diagram_staleness(sample_recipe_yaml.stem, recipes_dir, sample_recipe_yaml) is False
    )


def test_check_staleness_modified_recipe(tmp_path: Path, sample_recipe_yaml: Path) -> None:
    """DG-9: check_diagram_staleness returns True when recipe modified after generation."""
    recipes_dir = tmp_path / "recipes"
    generate_recipe_diagram(sample_recipe_yaml, recipes_dir)
    # Mutate recipe file after generation
    sample_recipe_yaml.write_text(sample_recipe_yaml.read_text() + "\n# modified\n")
    assert (
        check_diagram_staleness(sample_recipe_yaml.stem, recipes_dir, sample_recipe_yaml) is True
    )


def test_check_staleness_missing_diagram(tmp_path: Path, sample_recipe_yaml: Path) -> None:
    """DG-10: check_diagram_staleness returns True when diagram file missing."""
    assert (
        check_diagram_staleness(sample_recipe_yaml.stem, tmp_path / "recipes", sample_recipe_yaml)
        is True
    )


# ---------------------------------------------------------------------------
# DG-11: diagram_stale_to_suggestions
# ---------------------------------------------------------------------------


def test_stale_to_suggestions_format() -> None:
    """DG-11: diagram_stale_to_suggestions returns correct MCP suggestion shape."""
    suggestions = diagram_stale_to_suggestions("my-recipe")
    assert len(suggestions) == 1
    assert suggestions[0]["rule"] == "stale-diagram"
    assert suggestions[0]["severity"] == "warning"
