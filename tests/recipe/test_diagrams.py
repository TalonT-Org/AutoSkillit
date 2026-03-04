"""Tests for recipe/diagrams.py — DG-1 through DG-30."""

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

_COMPLEX_RECIPE_YAML = """\
name: complex-recipe
description: A recipe with loops, on_result, skip_when_false, and retries
summary: investigate -> fix -> test loop
ingredients:
  task:
    description: What to investigate
    required: true
  auto_fix:
    description: Whether to auto-fix
    required: false
    default: "true"
steps:
  investigate:
    tool: run_skill
    with:
      skill_command: "/autoskillit:investigate ${{ inputs.task }}"
      cwd: "."
    on_success: classify
    on_failure: escalate
  classify:
    tool: run_skill
    with:
      skill_command: "/autoskillit:make-plan"
      cwd: "."
    on_result:
      - when: result.verdict == "simple"
        route: fix
      - when: result.verdict == "complex"
        route: plan
      - route: escalate
    on_failure: escalate
  plan:
    tool: run_skill
    with:
      skill_command: "/autoskillit:make-plan"
      cwd: "."
    on_success: fix
    on_failure: escalate
  fix:
    tool: run_skill
    skip_when_false: inputs.auto_fix
    with:
      skill_command: "/autoskillit:implement-worktree-no-merge"
      cwd: "."
    on_success: test
    on_failure: escalate
    retries: 2
    on_exhausted: escalate
  test:
    tool: test_check
    with:
      worktree_path: "."
    on_success: done
    on_failure: fix
  done:
    action: stop
    message: "All tests pass."
  escalate:
    action: stop
    message: "Escalated to human."
kitchen_rules:
  - "Use AutoSkillit tools only"
  - "Never modify files outside the worktree"
"""


@pytest.fixture
def sample_recipe_yaml(tmp_path: Path) -> Path:
    path = tmp_path / "my-recipe.yaml"
    path.write_text(_SAMPLE_RECIPE_YAML)
    return path


@pytest.fixture
def complex_recipe_yaml(tmp_path: Path) -> Path:
    path = tmp_path / "complex-recipe.yaml"
    path.write_text(_COMPLEX_RECIPE_YAML)
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
    """DG-3: diagram contains exact recipe name header."""
    recipes_dir = tmp_path / "recipes"
    generate_recipe_diagram(sample_recipe_yaml, recipes_dir)
    content = (recipes_dir / "diagrams" / f"{sample_recipe_yaml.stem}.md").read_text()
    assert "## my-recipe" in content


def test_generate_route_table(tmp_path: Path, sample_recipe_yaml: Path) -> None:
    """DG-4: diagram contains success/failure route indicators."""
    recipes_dir = tmp_path / "recipes"
    generate_recipe_diagram(sample_recipe_yaml, recipes_dir)
    content = (recipes_dir / "diagrams" / f"{sample_recipe_yaml.stem}.md").read_text()
    assert "✓" in content
    assert "✗" in content


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


# ---------------------------------------------------------------------------
# DG-12 through DG-20: Structural content assertions (simple recipe)
# ---------------------------------------------------------------------------


def test_generate_contains_recipe_name(tmp_path: Path, sample_recipe_yaml: Path) -> None:
    """DG-12: diagram contains exact recipe name, not just '## '."""
    recipes_dir = tmp_path / "recipes"
    content = generate_recipe_diagram(sample_recipe_yaml, recipes_dir)
    assert "## my-recipe" in content


def test_generate_contains_description(tmp_path: Path, sample_recipe_yaml: Path) -> None:
    """DG-13: diagram contains recipe description text."""
    recipes_dir = tmp_path / "recipes"
    content = generate_recipe_diagram(sample_recipe_yaml, recipes_dir)
    assert "A test recipe for diagram generation" in content


def test_generate_contains_flow_summary(tmp_path: Path, sample_recipe_yaml: Path) -> None:
    """DG-14: diagram contains flow summary."""
    recipes_dir = tmp_path / "recipes"
    content = generate_recipe_diagram(sample_recipe_yaml, recipes_dir)
    assert "**Flow:**" in content
    assert "step1 -> done" in content


def test_generate_contains_step_names(tmp_path: Path, sample_recipe_yaml: Path) -> None:
    """DG-15: diagram graph section contains step names."""
    recipes_dir = tmp_path / "recipes"
    content = generate_recipe_diagram(sample_recipe_yaml, recipes_dir)
    # Extract graph section
    graph_start = content.index("### Graph")
    graph_end = content.index("### Ingredients")
    graph_section = content[graph_start:graph_end]
    assert "step1" in graph_section


def test_generate_contains_tool_names(tmp_path: Path, sample_recipe_yaml: Path) -> None:
    """DG-16: diagram contains tool name for step1."""
    recipes_dir = tmp_path / "recipes"
    content = generate_recipe_diagram(sample_recipe_yaml, recipes_dir)
    assert "run_skill" in content


def test_generate_contains_routes(tmp_path: Path, sample_recipe_yaml: Path) -> None:
    """DG-17: diagram contains route targets done and escalate."""
    recipes_dir = tmp_path / "recipes"
    content = generate_recipe_diagram(sample_recipe_yaml, recipes_dir)
    graph_start = content.index("### Graph")
    graph_end = content.index("### Ingredients")
    graph_section = content[graph_start:graph_end]
    assert "done" in graph_section
    assert "escalate" in graph_section


def test_generate_contains_ingredient_values(tmp_path: Path, sample_recipe_yaml: Path) -> None:
    """DG-18: diagram ingredients section contains ingredient details."""
    recipes_dir = tmp_path / "recipes"
    content = generate_recipe_diagram(sample_recipe_yaml, recipes_dir)
    ingredients_start = content.index("### Ingredients")
    ingredients_section = content[ingredients_start:]
    assert "task" in ingredients_section
    assert "What to do" in ingredients_section
    assert "yes" in ingredients_section


def test_generate_contains_terminal_steps(tmp_path: Path, sample_recipe_yaml: Path) -> None:
    """DG-19: diagram contains terminal step messages."""
    recipes_dir = tmp_path / "recipes"
    content = generate_recipe_diagram(sample_recipe_yaml, recipes_dir)
    assert "Done." in content
    assert "Failed." in content


def test_generate_contains_kitchen_rules(tmp_path: Path, sample_recipe_yaml: Path) -> None:
    """DG-20: diagram contains kitchen rules."""
    recipes_dir = tmp_path / "recipes"
    content = generate_recipe_diagram(sample_recipe_yaml, recipes_dir)
    assert "Use AutoSkillit tools only" in content


# ---------------------------------------------------------------------------
# DG-21: Visual format assertion
# ---------------------------------------------------------------------------


def test_generate_produces_visual_flow(tmp_path: Path, sample_recipe_yaml: Path) -> None:
    """DG-21: graph section uses vertical box-drawing character for visual flow.

    The route table format uses only horizontal ─ characters. The visual flow
    renderer uses │ (U+2502) for the vertical spine connecting steps.
    """
    recipes_dir = tmp_path / "recipes"
    content = generate_recipe_diagram(sample_recipe_yaml, recipes_dir)
    graph_start = content.index("### Graph")
    graph_end = content.index("### Ingredients")
    graph_section = content[graph_start:graph_end]
    assert "│" in graph_section, (
        "Graph section must contain vertical box-drawing character │ (U+2502). "
        "The current route table format does not produce this character."
    )


# ---------------------------------------------------------------------------
# DG-22: Format version in diagram
# ---------------------------------------------------------------------------


def test_generate_embeds_format_version(tmp_path: Path, sample_recipe_yaml: Path) -> None:
    """DG-22: diagram embeds a format version marker for rendering logic staleness."""
    recipes_dir = tmp_path / "recipes"
    content = generate_recipe_diagram(sample_recipe_yaml, recipes_dir)
    assert "autoskillit-diagram-format:" in content


# ---------------------------------------------------------------------------
# DG-23 through DG-27: Complex recipe topology tests
# ---------------------------------------------------------------------------


def test_complex_recipe_back_edge_marker(tmp_path: Path, complex_recipe_yaml: Path) -> None:
    """DG-23: back-edge (loop) from test->fix is marked in diagram."""
    recipes_dir = tmp_path / "recipes"
    content = generate_recipe_diagram(complex_recipe_yaml, recipes_dir)
    graph_start = content.index("### Graph")
    graph_end = content.index("### Ingredients")
    graph_section = content[graph_start:graph_end]
    # The test step routes on_failure back to fix (earlier step) — should be marked as back-edge
    assert "↑" in graph_section, "Back-edge from test→fix must be marked with ↑"


def test_complex_recipe_on_result_conditions(tmp_path: Path, complex_recipe_yaml: Path) -> None:
    """DG-24: on_result conditions and their target steps appear in diagram."""
    recipes_dir = tmp_path / "recipes"
    content = generate_recipe_diagram(complex_recipe_yaml, recipes_dir)
    # Check that the predicate conditions appear
    assert "simple" in content
    assert "complex" in content
    # Check that route targets appear
    assert "fix" in content
    assert "plan" in content


def test_complex_recipe_optional_step(tmp_path: Path, complex_recipe_yaml: Path) -> None:
    """DG-25: optional step (skip_when_false) is visually distinguished."""
    recipes_dir = tmp_path / "recipes"
    content = generate_recipe_diagram(complex_recipe_yaml, recipes_dir)
    # The fix step has skip_when_false: inputs.auto_fix
    # It should show the condition label
    assert "auto_fix" in content


def test_complex_recipe_retry_info(tmp_path: Path, complex_recipe_yaml: Path) -> None:
    """DG-26: retry count and exhaustion route appear in diagram."""
    recipes_dir = tmp_path / "recipes"
    content = generate_recipe_diagram(complex_recipe_yaml, recipes_dir)
    assert "2" in content  # retries: 2
    assert "escalate" in content  # on_exhausted target


def test_complex_recipe_multiple_kitchen_rules(tmp_path: Path, complex_recipe_yaml: Path) -> None:
    """DG-27: all kitchen rules appear in diagram."""
    recipes_dir = tmp_path / "recipes"
    content = generate_recipe_diagram(complex_recipe_yaml, recipes_dir)
    assert "Use AutoSkillit tools only" in content
    assert "Never modify files outside the worktree" in content


# ---------------------------------------------------------------------------
# DG-28: Staleness with format version
# ---------------------------------------------------------------------------


def test_check_staleness_detects_format_version_mismatch(
    tmp_path: Path, sample_recipe_yaml: Path
) -> None:
    """DG-28: staleness detection catches format version changes."""
    recipes_dir = tmp_path / "recipes"
    generate_recipe_diagram(sample_recipe_yaml, recipes_dir)

    # Tamper with the format version in the generated diagram
    diagram_path = recipes_dir / "diagrams" / f"{sample_recipe_yaml.stem}.md"
    content = diagram_path.read_text()
    tampered = content.replace("autoskillit-diagram-format:", "autoskillit-diagram-format: v999")
    diagram_path.write_text(tampered)

    assert check_diagram_staleness(sample_recipe_yaml.stem, recipes_dir, sample_recipe_yaml)
