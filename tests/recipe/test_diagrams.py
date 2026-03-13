"""Tests for recipe/diagrams.py — DG-1 through DG-30."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.recipe.diagrams import (
    DIAGRAM_SECTION_SEPARATOR,
    RecipeDiagram,
    build_recipe_diagram,
    check_diagram_staleness,
    diagram_stale_to_suggestions,
    generate_recipe_diagram,
    load_recipe_diagram,
)


def _extract_graph_section(content: str) -> str:
    """Extract the ### Graph section content."""
    start = content.index("### Graph")
    end = content.index("### Inputs")
    return content[start:end]


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


def test_generate_route_table(tmp_path: Path, complex_recipe_yaml: Path) -> None:
    """DG-4: diagram uses spec-compliant route format markers."""
    recipes_dir = tmp_path / "recipes"
    content = generate_recipe_diagram(complex_recipe_yaml, recipes_dir).render_markdown()
    assert "← only if" in content, "Optional step notation '← only if' must appear."
    assert "(retry ×" in content, "Retry notation '(retry ×N)' must appear."


def test_generate_ingredients_table(tmp_path: Path, sample_recipe_yaml: Path) -> None:
    """DG-5: diagram contains Inputs table (renamed from Ingredients)."""
    recipes_dir = tmp_path / "recipes"
    generate_recipe_diagram(sample_recipe_yaml, recipes_dir)
    content = (recipes_dir / "diagrams" / f"{sample_recipe_yaml.stem}.md").read_text()
    assert "### Inputs" in content


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
    content = generate_recipe_diagram(sample_recipe_yaml, recipes_dir).render_markdown()
    assert "## my-recipe" in content


def test_generate_contains_description(tmp_path: Path, sample_recipe_yaml: Path) -> None:
    """DG-13: diagram contains recipe description text."""
    recipes_dir = tmp_path / "recipes"
    content = generate_recipe_diagram(sample_recipe_yaml, recipes_dir).render_markdown()
    assert "A test recipe for diagram generation" in content


def test_generate_contains_flow_summary(tmp_path: Path, sample_recipe_yaml: Path) -> None:
    """DG-14: diagram contains flow summary."""
    recipes_dir = tmp_path / "recipes"
    content = generate_recipe_diagram(sample_recipe_yaml, recipes_dir).render_markdown()
    assert "**Flow:**" in content
    assert "step1 -> done" in content


def test_generate_contains_step_names(tmp_path: Path, sample_recipe_yaml: Path) -> None:
    """DG-15: diagram graph section contains step names."""
    recipes_dir = tmp_path / "recipes"
    content = generate_recipe_diagram(sample_recipe_yaml, recipes_dir).render_markdown()
    # Extract graph section
    graph_start = content.index("### Graph")
    graph_end = content.index("### Inputs")
    graph_section = content[graph_start:graph_end]
    assert "step1" in graph_section


def test_generate_contains_tool_names(tmp_path: Path, sample_recipe_yaml: Path) -> None:
    """DG-16: diagram contains tool name for step1."""
    recipes_dir = tmp_path / "recipes"
    content = generate_recipe_diagram(sample_recipe_yaml, recipes_dir).render_markdown()
    assert "run_skill" in content


def test_generate_contains_routes(tmp_path: Path, sample_recipe_yaml: Path) -> None:
    """DG-17: diagram contains route targets done and escalate."""
    recipes_dir = tmp_path / "recipes"
    content = generate_recipe_diagram(sample_recipe_yaml, recipes_dir).render_markdown()
    graph_start = content.index("### Graph")
    graph_end = content.index("### Inputs")
    graph_section = content[graph_start:graph_end]
    assert "done" in graph_section
    assert "escalate" in graph_section


def test_generate_contains_ingredient_values(tmp_path: Path, sample_recipe_yaml: Path) -> None:
    """DG-18: diagram Inputs section contains ingredient details."""
    recipes_dir = tmp_path / "recipes"
    content = generate_recipe_diagram(sample_recipe_yaml, recipes_dir).render_markdown()
    inputs_start = content.index("### Inputs")
    inputs_section = content[inputs_start:]
    assert "task" in inputs_section
    assert "What to do" in inputs_section


def test_generate_contains_terminal_steps(tmp_path: Path, sample_recipe_yaml: Path) -> None:
    """DG-19: diagram contains terminal step messages."""
    recipes_dir = tmp_path / "recipes"
    content = generate_recipe_diagram(sample_recipe_yaml, recipes_dir).render_markdown()
    assert "Done." in content
    assert "Failed." in content


def test_generate_contains_kitchen_rules(tmp_path: Path, sample_recipe_yaml: Path) -> None:
    """DG-20: diagram contains kitchen rules."""
    recipes_dir = tmp_path / "recipes"
    content = generate_recipe_diagram(sample_recipe_yaml, recipes_dir).render_markdown()
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
    content = generate_recipe_diagram(sample_recipe_yaml, recipes_dir).render_markdown()
    graph_start = content.index("### Graph")
    graph_end = content.index("### Inputs")
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
    content = generate_recipe_diagram(sample_recipe_yaml, recipes_dir).render_markdown()
    assert "autoskillit-diagram-format:" in content


# ---------------------------------------------------------------------------
# DG-23 through DG-27: Complex recipe topology tests
# ---------------------------------------------------------------------------


def test_complex_recipe_back_edge_marker(tmp_path: Path, complex_recipe_yaml: Path) -> None:
    """DG-23: back-edge (loop) from test->fix is marked in diagram."""
    recipes_dir = tmp_path / "recipes"
    content = generate_recipe_diagram(complex_recipe_yaml, recipes_dir).render_markdown()
    graph_start = content.index("### Graph")
    graph_end = content.index("### Inputs")
    graph_section = content[graph_start:graph_end]
    # The test step routes on_failure back to fix (earlier step) — should be marked as back-edge
    assert "↑" in graph_section, "Back-edge from test→fix must be marked with ↑"


def test_complex_recipe_on_result_conditions(tmp_path: Path, complex_recipe_yaml: Path) -> None:
    """DG-24: on_result conditions and their target steps appear in diagram."""
    recipes_dir = tmp_path / "recipes"
    content = generate_recipe_diagram(complex_recipe_yaml, recipes_dir).render_markdown()
    # Check that the predicate conditions appear
    assert "simple" in content
    assert "complex" in content
    # Check that route targets appear
    assert "fix" in content
    assert "plan" in content


def test_complex_recipe_optional_step(tmp_path: Path, complex_recipe_yaml: Path) -> None:
    """DG-25: optional step (skip_when_false) uses bracket+arrow notation."""
    recipes_dir = tmp_path / "recipes"
    content = generate_recipe_diagram(complex_recipe_yaml, recipes_dir).render_markdown()
    # The fix step has skip_when_false: inputs.auto_fix
    assert "[fix]" in content, "Optional step must appear as [fix] in bracket notation."
    assert "← only if" in content, "Optional step must use '← only if' annotation."
    assert "⟨skip if" not in content, "'⟨skip if' notation must not appear."


def test_complex_recipe_retry_info(tmp_path: Path, complex_recipe_yaml: Path) -> None:
    """DG-26: retry count appears as parenthetical on the step name line."""
    recipes_dir = tmp_path / "recipes"
    content = generate_recipe_diagram(complex_recipe_yaml, recipes_dir).render_markdown()
    # The fix step has retries: 2 — must appear as (retry ×2) on the same line as [fix]
    assert "(retry ×2)" in content, "Retry annotation '(retry ×2)' must appear for fix step."
    assert "escalate" in content  # on_exhausted target


def test_complex_recipe_multiple_kitchen_rules(tmp_path: Path, complex_recipe_yaml: Path) -> None:
    """DG-27: all kitchen rules appear in diagram."""
    recipes_dir = tmp_path / "recipes"
    content = generate_recipe_diagram(complex_recipe_yaml, recipes_dir).render_markdown()
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


# ---------------------------------------------------------------------------
# T1 through T10: Structural immunity tests
# ---------------------------------------------------------------------------


_INFRA_RECIPE_YAML = """\
name: infra-test
description: Recipe with infrastructure capture step
summary: capture -> main -> done
ingredients:
  task:
    description: What to do
    required: true
steps:
  capture_sha:
    tool: run_cmd
    with:
      cmd: "git rev-parse HEAD"
      cwd: "."
    capture:
      base_sha: "${{ result.stdout | trim }}"
    on_success: main_step
    on_failure: escalate
    note: "Captures the base SHA before implementation begins."
  main_step:
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
"""

_CONTEXT_LIMIT_RECIPE_YAML = """\
name: ctx-limit-test
description: Recipe with on_context_limit routing
summary: implement -> done
ingredients:
  task:
    description: What to do
    required: true
steps:
  implement:
    tool: run_skill
    with:
      skill_command: "/autoskillit:implement-worktree-no-merge ${{ inputs.task }}"
      cwd: "."
    on_context_limit: retry_step
    on_success: done
    on_failure: escalate
  retry_step:
    tool: run_skill
    with:
      skill_command: "/autoskillit:retry-worktree ${{ inputs.task }}"
      cwd: "."
    on_success: done
    on_failure: escalate
  done:
    action: stop
    message: "Done."
  escalate:
    action: stop
    message: "Failed."
"""

_RETRY3_RECIPE_YAML = """\
name: retry3-test
description: Recipe with retry count of 3
summary: implement -> done
ingredients:
  task:
    description: What to do
    required: true
steps:
  implement:
    tool: run_skill
    with:
      skill_command: "/autoskillit:implement-worktree-no-merge ${{ inputs.task }}"
      cwd: "."
    retries: 3
    on_exhausted: escalate
    on_success: done
    on_failure: escalate
  done:
    action: stop
    message: "Done."
  escalate:
    action: stop
    message: "Failed."
"""

_BOOL_INGREDIENT_RECIPE_YAML = """\
name: bool-test
description: Recipe with boolean and auto-detect ingredients
summary: step -> done
ingredients:
  flag_off:
    description: A flag defaulting to false
    default: "false"
  flag_on:
    description: A flag defaulting to true
    default: "true"
  auto:
    description: Auto-detect source
    default: ""
steps:
  step:
    tool: run_skill
    with:
      skill_command: "/autoskillit:investigate"
      cwd: "."
    on_success: done
    on_failure: escalate
  done:
    action: stop
    message: "Done."
  escalate:
    action: stop
    message: "Failed."
"""


@pytest.fixture
def infra_recipe_yaml(tmp_path: Path) -> Path:
    path = tmp_path / "infra-test.yaml"
    path.write_text(_INFRA_RECIPE_YAML)
    return path


@pytest.fixture
def ctx_limit_recipe_yaml(tmp_path: Path) -> Path:
    path = tmp_path / "ctx-limit-test.yaml"
    path.write_text(_CONTEXT_LIMIT_RECIPE_YAML)
    return path


@pytest.fixture
def retry3_recipe_yaml(tmp_path: Path) -> Path:
    path = tmp_path / "retry3-test.yaml"
    path.write_text(_RETRY3_RECIPE_YAML)
    return path


@pytest.fixture
def bool_ingredient_recipe_yaml(tmp_path: Path) -> Path:
    path = tmp_path / "bool-test.yaml"
    path.write_text(_BOOL_INGREDIENT_RECIPE_YAML)
    return path


def test_diagram_hides_infrastructure_steps(tmp_path: Path, infra_recipe_yaml: Path) -> None:
    """T1: infrastructure run_cmd capture steps are hidden from the graph section."""
    recipes_dir = tmp_path / "recipes"
    content = generate_recipe_diagram(infra_recipe_yaml, recipes_dir).render_markdown()
    graph_start = content.index("### Graph")
    graph_end = content.index("### Inputs")
    graph_section = content[graph_start:graph_end]
    assert "capture_sha" not in graph_section, (
        "Infrastructure step 'capture_sha' must be hidden from the graph section."
    )


def test_diagram_shows_on_context_limit_routes(
    tmp_path: Path, ctx_limit_recipe_yaml: Path
) -> None:
    """T2: on_context_limit route appears in graph section."""
    recipes_dir = tmp_path / "recipes"
    content = generate_recipe_diagram(ctx_limit_recipe_yaml, recipes_dir).render_markdown()
    graph_start = content.index("### Graph")
    graph_end = content.index("### Inputs")
    graph_section = content[graph_start:graph_end]
    assert "retry_step" in graph_section, (
        "on_context_limit target 'retry_step' must appear in the graph section."
    )


def test_diagram_optional_step_notation_uses_bracket_and_arrow(
    tmp_path: Path, complex_recipe_yaml: Path
) -> None:
    """T3: optional steps use [step] bracket notation with '← only if', not ⟨skip if⟩."""
    recipes_dir = tmp_path / "recipes"
    content = generate_recipe_diagram(complex_recipe_yaml, recipes_dir).render_markdown()
    graph_start = content.index("### Graph")
    graph_end = content.index("### Inputs")
    graph_section = content[graph_start:graph_end]
    assert "[" in graph_section, "Optional steps must use bracket notation [step-name]."
    assert "← only if" in graph_section, "Optional steps must use '← only if' annotation."
    assert "⟨skip if" not in graph_section, (
        "'⟨skip if' prefix notation must not appear — use bracket+arrow instead."
    )


def test_diagram_retry_notation_is_parenthetical_on_step_name(
    tmp_path: Path, retry3_recipe_yaml: Path
) -> None:
    """T4: retry is shown as (retry ×N) on the step name line, not ↺ ×N as a sub-line."""
    recipes_dir = tmp_path / "recipes"
    content = generate_recipe_diagram(retry3_recipe_yaml, recipes_dir).render_markdown()
    assert "(retry ×3)" in content, (
        "Retry count must appear as parenthetical '(retry ×3)' on the step name line."
    )
    assert "↺ ×3" not in content, (
        "'↺ ×3' sub-line retry format must not appear — use parenthetical notation."
    )


def test_diagram_inputs_table_has_three_columns(tmp_path: Path, sample_recipe_yaml: Path) -> None:
    """T5: diagram has ### Inputs with 3-column table (Name, Description, Default)."""
    recipes_dir = tmp_path / "recipes"
    content = generate_recipe_diagram(sample_recipe_yaml, recipes_dir).render_markdown()
    assert "### Inputs" in content, "Section header must be '### Inputs', not '### Ingredients'."
    assert "### Ingredients" not in content, (
        "'### Ingredients' must be replaced with '### Inputs'."
    )
    assert "| Name | Description | Default |" in content, (
        "Inputs table must have exactly 3 columns: Name, Description, Default."
    )
    assert "Required" not in content, "The 'Required' column must not appear in the Inputs table."


def test_diagram_boolean_ingredient_default_rendered_as_off_on(
    tmp_path: Path, bool_ingredient_recipe_yaml: Path
) -> None:
    """T6: boolean-string defaults 'false'→'off' and 'true'→'on' in Inputs table."""
    recipes_dir = tmp_path / "recipes"
    content = generate_recipe_diagram(bool_ingredient_recipe_yaml, recipes_dir).render_markdown()
    inputs_start = content.index("### Inputs")
    inputs_section = content[inputs_start:]
    assert "| off |" in inputs_section or "| off" in inputs_section, (
        "Ingredient with default='false' must render as 'off' in Inputs table."
    )
    assert "| on |" in inputs_section or "| on" in inputs_section, (
        "Ingredient with default='true' must render as 'on' in Inputs table."
    )


def test_diagram_empty_string_default_rendered_as_auto_detect(
    tmp_path: Path, bool_ingredient_recipe_yaml: Path
) -> None:
    """T7: empty-string default renders as 'auto-detect' in Inputs table."""
    recipes_dir = tmp_path / "recipes"
    content = generate_recipe_diagram(bool_ingredient_recipe_yaml, recipes_dir).render_markdown()
    inputs_start = content.index("### Inputs")
    inputs_section = content[inputs_start:]
    assert "auto-detect" in inputs_section, (
        "Ingredient with default='' must render as 'auto-detect' in Inputs table."
    )


def test_extract_routing_edges_covers_on_context_limit() -> None:
    """T8: _extract_routing_edges returns an edge for on_context_limit."""
    from autoskillit.recipe._analysis import _extract_routing_edges  # noqa: PLC0415
    from autoskillit.recipe.schema import RecipeStep

    step = RecipeStep(
        tool="run_skill",
        on_context_limit="resume_step",
        on_success="done",
        on_failure="escalate",
    )
    edges = _extract_routing_edges(step)
    targets = [e.target for e in edges]
    assert "resume_step" in targets, (
        "_extract_routing_edges must return an edge for on_context_limit='resume_step'."
    )


def test_extract_routing_edges_covers_all_routing_fields() -> None:
    """T9: _extract_routing_edges covers all RecipeStep routing fields (completeness invariant)."""
    from autoskillit.recipe._analysis import _extract_routing_edges  # noqa: PLC0415
    from autoskillit.recipe.schema import RecipeStep, StepResultCondition, StepResultRoute

    step = RecipeStep(
        tool="run_skill",
        on_success="step_success",
        on_failure="step_failure",
        on_context_limit="step_context_limit",
        on_exhausted="step_exhausted",
        on_result=StepResultRoute(
            conditions=[
                StepResultCondition(route="step_result_cond", when="result.x == 1"),
            ]
        ),
    )
    edges = _extract_routing_edges(step)
    targets = {e.target for e in edges}
    assert "step_success" in targets, "on_success must be covered"
    assert "step_failure" in targets, "on_failure must be covered"
    assert "step_context_limit" in targets, "on_context_limit must be covered"
    assert "step_exhausted" in targets, "on_exhausted must be covered"
    assert "step_result_cond" in targets, "on_result.conditions[].route must be covered"


def test_bundled_implementation_diagram_matches_spec_structure() -> None:
    """T10: bundled implementation diagram uses spec-compliant v3 format."""
    import autoskillit

    pkg_root = Path(autoskillit.__file__).parent
    diagram_path = pkg_root / "recipes" / "diagrams" / "implementation.md"
    assert diagram_path.exists(), f"Bundled diagram not found: {diagram_path}"
    content = diagram_path.read_text(encoding="utf-8")

    graph_start = content.index("### Graph")
    graph_end = content.index("### Inputs")
    graph_section = content[graph_start:graph_end]

    # Infrastructure steps must be hidden
    assert "capture_base_sha" not in graph_section, (
        "'capture_base_sha' is an infrastructure step and must not appear in the graph."
    )
    assert "set_merge_target" not in graph_section, (
        "'set_merge_target' is an infrastructure step and must not appear in the graph."
    )

    # FOR EACH block must be present
    assert "FOR EACH" in graph_section.upper(), (
        "Implementation recipe must have a FOR EACH iteration block in the graph."
    )

    # Optional step bracket+arrow notation
    assert "[" in graph_section and "← only if" in graph_section, (
        "Optional steps must use bracket notation with '← only if' annotation."
    )

    # Retry parenthetical notation for retries:0
    assert "(retry ×∞)" in graph_section, (
        "implement step with retries:0 must render as '(retry ×∞)'."
    )

    # Inputs section (not Ingredients)
    assert "### Inputs" in content, "Section header must be '### Inputs'."

    # Boolean defaults rendered as off/on
    inputs_start = content.index("### Inputs")
    inputs_section = content[inputs_start:]
    assert "off" in inputs_section, (
        "Boolean-default ingredients must render as 'off' in Inputs table."
    )


# ---------------------------------------------------------------------------
# T-HC-1, T-SD-1, T-GF-1..6: Horizontal chain, semantic detection, golden-file
# ---------------------------------------------------------------------------

_LOOPING_PLAN_PARTS_YAML = """\
name: looping-parts-test
description: Recipe that iterates over plan_parts
summary: plan -> verify -> implement -> next_or_done loop
ingredients:
  task:
    description: What to do
    required: true
steps:
  plan:
    tool: run_skill
    with:
      skill_command: /autoskillit:make-plan
    retries: 3
    on_success: verify
    on_failure: escalate
    note: |
      plan_parts are built here. For each plan_part, run the full cycle.
  verify:
    tool: run_skill
    with:
      skill_command: /autoskillit:dry-walkthrough
    retries: 3
    on_success: implement
    on_failure: escalate
  implement:
    tool: run_skill
    with:
      skill_command: /autoskillit:implement-worktree
    retries: 0
    on_success: next_or_done
    on_failure: escalate
  next_or_done:
    tool: run_skill
    with:
      skill_command: /autoskillit:smoke-task
    retries: 3
    on_success: verify
    on_failure: escalate
    note: Checks if more plan_parts remain; if so, routes back to verify for the next plan_part.
  escalate:
    action: stop
    message: "Failed."
"""

_CI_POLLING_YAML = """\
name: ci-poll-test
description: Recipe with CI polling retry only
summary: implement -> ci_watch -> done
ingredients:
  task:
    description: What to do
    required: true
steps:
  implement:
    tool: run_skill
    with:
      skill_command: /autoskillit:implement-worktree
    retries: 3
    on_success: ci_watch
    on_failure: escalate
  ci_watch:
    tool: run_cmd
    with:
      cmd: "gh run watch"
      cwd: "."
    retries: 3
    on_success: done
    on_failure: resolve_ci
    note: Polls for GitHub Actions completion. On failure, routes to resolve_ci.
  resolve_ci:
    tool: run_skill
    with:
      skill_command: /autoskillit:resolve-failures
    retries: 2
    on_success: re_push
    on_failure: escalate
  re_push:
    tool: push_to_remote
    with:
      clone_path: "."
      remote_url: "."
      branch: "main"
    retries: 2
    on_success: ci_watch
    on_failure: escalate
    note: Pushes the CI-fixed branch back to the remote. Routes back to ci_watch to verify.
  done:
    action: stop
    message: "Done."
  escalate:
    action: stop
    message: "Failed."
"""


@pytest.fixture
def looping_parts_recipe(tmp_path: Path):  # type: ignore[no-untyped-def]
    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()
    pipeline = recipes_dir / "looping-parts-test.yaml"
    pipeline.write_text(_LOOPING_PLAN_PARTS_YAML)
    return pipeline, recipes_dir


@pytest.fixture
def ci_polling_recipe(tmp_path: Path):  # type: ignore[no-untyped-def]
    recipes_dir = tmp_path / "recipes"
    recipes_dir.mkdir()
    pipeline = recipes_dir / "ci-poll-test.yaml"
    pipeline.write_text(_CI_POLLING_YAML)
    return pipeline, recipes_dir


def test_for_each_inner_layout_is_horizontal_chain(looping_parts_recipe, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    """T-HC-1: Steps inside FOR EACH must use horizontal chain layout (───), not vertical blocks.

    This test will fail as long as _render_visual_flow() uses _append_step() inside the
    FOR EACH block. It passes only when _render_for_each_chain() is implemented.
    """
    pipeline, recipes_dir = looping_parts_recipe
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    content = generate_recipe_diagram(
        pipeline, recipes_dir=recipes_dir, out_dir=out_dir
    ).render_markdown()
    graph = _extract_graph_section(content)

    assert "FOR EACH" in graph.upper(), (
        "Looping recipe with plan_parts notes must have FOR EACH block"
    )
    assert "───" in graph, "FOR EACH steps must be connected by horizontal chain (───)"

    # No vertical routing sub-lines inside the FOR EACH block
    fe_start = graph.index("┌────┤")
    fe_end = graph.index("└────┘") + len("└────┘")
    for_each_block = graph[fe_start:fe_end]
    assert "↓ success →" not in for_each_block, (
        "Vertical step blocks (│  ↓ success →) found inside FOR EACH; "
        "expected horizontal chain layout"
    )
    assert "✗ failure →" not in for_each_block or "─── " in for_each_block, (
        "Failure routes inside FOR EACH must appear as side-legs below a horizontal chain"
    )


def test_ci_polling_loop_does_not_get_for_each_block(ci_polling_recipe, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    """T-SD-1: A recipe whose only back-edge is a CI polling retry must NOT get a FOR EACH block.

    FOR EACH requires plan-iteration intent signaled by 'plan_parts' or 'for each'
    in step note: fields. A structural back-edge alone is not sufficient.
    """
    pipeline, recipes_dir = ci_polling_recipe
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    content = generate_recipe_diagram(
        pipeline, recipes_dir=recipes_dir, out_dir=out_dir
    ).render_markdown()
    graph = _extract_graph_section(content)
    assert "FOR EACH" not in graph.upper(), (
        "CI polling loop (re_push → ci_watch↑) must not be wrapped in a FOR EACH block; "
        "FOR EACH requires plan-iteration intent in note: fields"
    )


@pytest.mark.parametrize(
    "recipe_name",
    [
        "implementation",
        "bugfix-loop",
        "implementation-groups",
        "audit-and-fix",
        "remediation",
        "smoke-test",
        "pr-merge-pipeline",
    ],
)
def test_bundled_diagram_roundtrip(recipe_name: str, tmp_path: Path) -> None:
    """T-GF-1..6: Committed diagram file must exactly match what the renderer generates.

    This is a golden-file regression test. It fails whenever:
    - The renderer logic changes but committed files are not regenerated, OR
    - Committed files are manually edited to differ from renderer output.

    Run `autoskillit recipes render` to regenerate committed files.
    """
    from autoskillit.core.paths import pkg_root  # noqa: PLC0415

    recipes_dir = pkg_root() / "recipes"
    pipeline = recipes_dir / f"{recipe_name}.yaml"
    committed = (recipes_dir / "diagrams" / f"{recipe_name}.md").read_text()

    out_dir = tmp_path / "diagrams"
    out_dir.mkdir()
    generated = generate_recipe_diagram(
        pipeline, recipes_dir=recipes_dir, out_dir=out_dir
    ).render_markdown()

    assert generated == committed, (
        f"Committed {recipe_name}.md differs from renderer output. "
        f"Run: autoskillit recipes render {recipe_name}"
    )


# ---------------------------------------------------------------------------
# T-LBL-1, T-LBL-2, T-BD-1, T-BD-2: Descriptive FOR EACH labels and no-FOR-EACH guards
# ---------------------------------------------------------------------------


def test_implementation_diagram_for_each_label_names_plan_part(tmp_path: Path) -> None:
    """T-LBL-1: implementation.yaml must use 'FOR EACH PLAN PART:' label in diagram."""
    from autoskillit.core.paths import pkg_root  # noqa: PLC0415

    recipes_dir = pkg_root() / "recipes"
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    content = generate_recipe_diagram(
        recipes_dir / "implementation.yaml",
        recipes_dir=recipes_dir,
        out_dir=out_dir,
    ).render_markdown()
    graph = _extract_graph_section(content)
    assert "FOR EACH PLAN PART:" in graph, (
        "implementation.yaml iterates plan_parts; FOR EACH label must say 'FOR EACH PLAN PART:'"
    )


def test_implementation_groups_diagram_for_each_label_names_group(tmp_path: Path) -> None:
    """T-LBL-2: implementation-groups.yaml must use a group-level label in diagram."""
    from autoskillit.core.paths import pkg_root  # noqa: PLC0415

    recipes_dir = pkg_root() / "recipes"
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    content = generate_recipe_diagram(
        recipes_dir / "implementation-groups.yaml",
        recipes_dir=recipes_dir,
        out_dir=out_dir,
    ).render_markdown()
    graph = _extract_graph_section(content)
    assert "FOR EACH GROUP" in graph, (
        "implementation-groups.yaml iterates groups; FOR EACH label must say 'FOR EACH GROUP...'"
    )


def test_audit_and_fix_diagram_has_no_for_each_block(tmp_path: Path) -> None:
    """T-BD-1: audit-and-fix.yaml has no plan_parts iteration; diagram must not have FOR EACH."""
    from autoskillit.core.paths import pkg_root  # noqa: PLC0415

    recipes_dir = pkg_root() / "recipes"
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    content = generate_recipe_diagram(
        recipes_dir / "audit-and-fix.yaml",
        recipes_dir=recipes_dir,
        out_dir=out_dir,
    ).render_markdown()
    graph = _extract_graph_section(content)
    assert "FOR EACH" not in graph.upper(), (
        "audit-and-fix.yaml has no plan_parts iteration (only CI polling); "
        "its diagram must not contain a FOR EACH block"
    )


_CONFIRM_RECIPE_YAML = """\
name: confirm-test
description: Recipe with confirm step
summary: confirm_cleanup -> done
ingredients:
  work_dir:
    description: Clone path
    default: "/tmp/clone"
steps:
  confirm_cleanup:
    action: confirm
    message: "Delete the clone?"
    on_success: delete_clone
    on_failure: done
  delete_clone:
    tool: remove_clone
    with:
      clone_path: "${{ context.work_dir }}"
      keep: "false"
    on_success: done
    on_failure: done
  done:
    action: stop
    message: "Done."
kitchen_rules:
  - "Use AutoSkillit tools only"
"""


def test_confirm_step_rendered_as_decision_point(tmp_path: Path) -> None:
    """DG-C1: confirm steps appear with ❓ prefix and show yes/no routes."""
    recipe_path = tmp_path / "confirm-test.yaml"
    recipe_path.write_text(_CONFIRM_RECIPE_YAML)
    diagram = generate_recipe_diagram(recipe_path, tmp_path).render_markdown()
    assert "❓" in diagram and "confirm" in diagram.lower()
    assert "yes" in diagram.lower() and "no" in diagram.lower()


def test_confirm_step_not_in_terminal_section(tmp_path: Path) -> None:
    """DG-C2: confirm steps must NOT appear in the ⏹ terminal section."""
    recipe_path = tmp_path / "confirm-test.yaml"
    recipe_path.write_text(_CONFIRM_RECIPE_YAML)
    diagram = generate_recipe_diagram(recipe_path, tmp_path).render_markdown()
    # Find the terminal section by locating the separator line (a line of all ─ characters)
    diagram_lines = diagram.splitlines()
    sep_idx = next(
        (
            i
            for i, ln in enumerate(diagram_lines)
            if ln.strip() and all(c == "─" for c in ln.strip())
        ),
        None,
    )
    terminal_section = "\n".join(diagram_lines[sep_idx + 1 :]) if sep_idx is not None else ""
    assert "confirm_cleanup" not in terminal_section


def test_smoke_test_diagram_has_no_for_each_block(tmp_path: Path) -> None:
    """T-BD-2: smoke-test.yaml is a single synthetic task run; diagram must not have FOR EACH."""
    from autoskillit.core.paths import pkg_root  # noqa: PLC0415

    recipes_dir = pkg_root() / "recipes"
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    content = generate_recipe_diagram(
        recipes_dir / "smoke-test.yaml",
        recipes_dir=recipes_dir,
        out_dir=out_dir,
    ).render_markdown()
    graph = _extract_graph_section(content)
    assert "FOR EACH" not in graph.upper(), (
        "smoke-test.yaml has no plan_parts iteration; "
        "its diagram must not contain a FOR EACH block"
    )


# ---------------------------------------------------------------------------
# T-FS-1, T-FS-2: Folded-scalar regression guards
# ---------------------------------------------------------------------------

_FOLDED_SCALAR_RECIPE_YAML = """\
name: test-folded
description: Test recipe for folded scalar regression
summary: Tests folded scalar normalization
version: "1.0"
ingredients:
  source_dir:
    description: >
      Path to the source repository to clone and work in.
      Leave empty to auto-detect from git rev-parse --show-toplevel.
    required: false
    default: ""
  run_name:
    description: >
      Name prefix for this pipeline run.
      Used as the first path component of the branch name.
    required: false
    default: impl
steps: []
"""


def test_diagram_inputs_table_rows_are_single_lines_with_folded_scalars(
    tmp_path: Path,
) -> None:
    """T-FS-1: Table rows built from folded-scalar descriptions must not embed newlines."""
    pipeline = tmp_path / "test-folded.yaml"
    pipeline.write_text(_FOLDED_SCALAR_RECIPE_YAML)
    content = generate_recipe_diagram(
        pipeline, recipes_dir=tmp_path, out_dir=tmp_path
    ).render_markdown()

    inputs_start = content.index("### Inputs")
    inputs_section = content[inputs_start:]
    # Extract data rows (skip header and separator rows)
    rows = [
        line
        for line in inputs_section.splitlines()
        if line.startswith("| ") and not line.startswith("| Name |") and "---" not in line
    ]
    assert rows, "Expected at least one data row in Inputs table"
    for row in rows:
        assert row.count("|") >= 4, (
            f"Malformed table row (likely split by embedded newline): {row!r}"
        )
        assert row.startswith("| ") and row.endswith(" |"), (
            f"Row is not a complete pipe-table row: {row!r}"
        )


@pytest.mark.parametrize(
    "recipe_name",
    [
        "implementation",
        "implementation-groups",
        "audit-and-fix",
        "remediation",
        "bugfix-loop",
        "smoke-test",
    ],
)
def test_bundled_diagram_inputs_table_has_no_embedded_newlines(
    recipe_name: str, tmp_path: Path
) -> None:
    """T-FS-2: Generated diagram Inputs tables must contain only single-line rows.

    This is a correctness guard. It will catch any future regression where
    YAML-sourced strings are not normalized before table interpolation.
    """
    from autoskillit.core.paths import pkg_root  # noqa: PLC0415

    recipes_dir = pkg_root() / "recipes"
    pipeline = recipes_dir / f"{recipe_name}.yaml"
    out_dir = tmp_path / "diagrams"
    out_dir.mkdir()
    content = generate_recipe_diagram(
        pipeline, recipes_dir=recipes_dir, out_dir=out_dir
    ).render_markdown()

    if "### Inputs" not in content:
        return  # recipe has no ingredients — skip
    inputs_start = content.index("### Inputs")
    inputs_section = content[inputs_start:]
    rows = [
        line
        for line in inputs_section.splitlines()
        if line.startswith("| ") and not line.startswith("| Name |") and "---" not in line
    ]
    assert rows, f"No data rows found in inputs table for {recipe_name}"
    for row in rows:
        assert row.count("|") >= 4, (
            f"[{recipe_name}] Malformed table row (likely split by embedded newline): {row!r}"
        )


# ---------------------------------------------------------------------------
# T-GRAPH-1..3: igraph builder contract tests
# ---------------------------------------------------------------------------


_SIMPLE_GRAPH_YAML = (
    "name: simple\nsteps:\n"
    "  step1:\n    tool: run_skill\n    with:\n      skill_command: /foo\n"
    "    on_success: done\n    on_failure: escalate\n"
    "  done:\n    action: stop\n    message: Done.\n"
    "  escalate:\n    action: stop\n    message: Failed.\n"
)


def _load_simple_recipe(tmp_path: Path):  # type: ignore[no-untyped-def]
    """Write _SIMPLE_GRAPH_YAML to a temp file and load it as a Recipe."""
    from autoskillit.recipe.io import load_recipe  # noqa: PLC0415

    yaml_path = tmp_path / "simple.yaml"
    yaml_path.write_text(_SIMPLE_GRAPH_YAML)
    return load_recipe(yaml_path)


def test_build_recipe_graph_is_directed(tmp_path: Path) -> None:
    """T-GRAPH-1: build_recipe_graph returns a directed igraph.Graph."""
    import igraph  # noqa: PLC0415

    from autoskillit.recipe.diagrams import build_recipe_graph  # noqa: PLC0415

    recipe = _load_simple_recipe(tmp_path)
    g = build_recipe_graph(recipe)
    assert isinstance(g, igraph.Graph)
    assert g.is_directed()
    assert g.vcount() == 3  # step1, done, escalate


def test_build_recipe_graph_vertex_attributes(tmp_path: Path) -> None:
    """T-GRAPH-2: build_recipe_graph encodes step attributes as vertex attributes."""
    from autoskillit.recipe.diagrams import build_recipe_graph  # noqa: PLC0415

    recipe = _load_simple_recipe(tmp_path)
    g = build_recipe_graph(recipe)
    names = g.vs["name"]
    assert "step1" in names
    assert "done" in names
    v_done = g.vs.find(name="done")
    assert v_done["is_terminal"] is True
    v_step1 = g.vs.find(name="step1")
    assert v_step1["is_terminal"] is False


def test_build_recipe_graph_edge_types(tmp_path: Path) -> None:
    """T-GRAPH-3: build_recipe_graph encodes routing edge types as edge attributes."""
    from autoskillit.recipe.diagrams import build_recipe_graph  # noqa: PLC0415

    recipe = _load_simple_recipe(tmp_path)
    g = build_recipe_graph(recipe)
    edge_types = set(g.es["edge_type"])
    assert "success" in edge_types
    assert "failure" in edge_types


# ---------------------------------------------------------------------------
# T-NEW-1..5: Spec-correctness tests (Part A — graph model unification)
# ---------------------------------------------------------------------------


def _find_for_each_chain_line(content: str) -> str | None:
    """Return the first horizontal chain line (containing ───) inside a FOR EACH block."""
    in_block = False
    for line in content.splitlines():
        if "┌────┤" in line:
            in_block = True
            continue
        if in_block and "───" in line:
            return line
        if "└────┘" in line:
            break
    return None


def test_classify_steps_excludes_side_legs() -> None:
    """T-NEW-1: igraph-based _classify_steps must return only tight success-path cycle members.

    Side-leg steps reachable only via on_failure or on_context_limit must NOT
    appear in main_chain for implementation.yaml's per-part loop.
    """
    from autoskillit.core.paths import pkg_root  # noqa: PLC0415
    from autoskillit.recipe.diagrams import _classify_steps  # noqa: PLC0415
    from autoskillit.recipe.io import load_recipe  # noqa: PLC0415

    recipe = load_recipe(pkg_root() / "recipes" / "implementation.yaml")
    classification = _classify_steps(recipe)

    # Tight cycle members must be present
    assert "verify" in classification.main_chain
    assert "implement" in classification.main_chain
    assert "test" in classification.main_chain
    assert "merge" in classification.main_chain
    # next_or_done is the back-edge source — force-included in main_chain
    assert "next_or_done" in classification.main_chain

    # Side legs must NOT be in the tight cycle
    assert "retry_worktree" not in classification.main_chain  # on_context_limit only
    assert "fix" not in classification.main_chain  # on_failure only


def test_classify_steps_on_result_back_edge_includes_full_chain(tmp_path: Path) -> None:
    """T-NEW-7: When the path to the back-edge source uses on_result (not on_success),
    all success-reachable steps from start must appear in main_chain.

    Minimal recipe:
        step_a --on_success--> step_b --on_result--> step_c --on_result--> step_a

    g_success contains: step_a → step_b (only — step_b has no on_success)
    subcomponent(step_a, OUT) in g_success = {step_a, step_b}
    subcomponent(step_c, IN) in g_success = {step_c}  (no step has on_success: step_c)
    Intersection = {} — after force-add: main_chain = {step_c} only — WRONG.

    Current code uses subcomponent(OUT) + force-add (no intersection) → CORRECT:
    main_chain = {step_a, step_b, step_c}.

    This test fails if the intersection is ever erroneously applied.
    """
    from autoskillit.recipe.diagrams import _classify_steps  # noqa: PLC0415
    from autoskillit.recipe.io import load_recipe  # noqa: PLC0415

    yaml_path = tmp_path / "on-result-loop.yaml"
    yaml_path.write_text(
        "name: on-result-loop\n"
        "steps:\n"
        "  step_a:\n"
        "    tool: run_skill\n"
        "    with:\n"
        "      skill_command: /foo\n"
        "      cwd: .\n"
        "    on_success: step_b\n"
        "    on_failure: stop1\n"
        "  step_b:\n"
        "    tool: run_skill\n"
        "    with:\n"
        "      skill_command: /bar\n"
        "      cwd: .\n"
        "    on_result:\n"
        "      - route: step_c\n"
        "    on_failure: stop1\n"
        "  step_c:\n"
        "    note: 'for each plan part'\n"
        "    action: route\n"
        "    on_result:\n"
        '      - when: "result.next == more"\n'
        "        route: step_a\n"
        "      - route: stop1\n"
        "  stop1:\n"
        "    action: stop\n"
        "    message: Done.\n"
    )
    recipe = load_recipe(yaml_path)
    classification = _classify_steps(recipe)

    # All steps on the success path from step_a must be in main_chain.
    # If the intersection were applied: subcomponent(step_c, IN) = {step_c} only,
    # intersection = {}, force-add → main_chain = {step_c} — these assertions would fail.
    assert "step_a" in classification.main_chain
    assert "step_b" in classification.main_chain
    assert "step_c" in classification.main_chain  # force-included as back-edge source


def test_for_each_chain_excludes_side_leg_steps(tmp_path: Path) -> None:
    """T-NEW-2: Side-leg steps must not appear as inline ─── chain tokens.

    The horizontal chain inside the FOR EACH block must contain only tight
    success-path cycle steps. retry_worktree (on_context_limit) and fix
    (on_failure) must not be chain tokens.
    """
    from autoskillit.core.paths import pkg_root  # noqa: PLC0415

    recipes_dir = pkg_root() / "recipes"
    content = generate_recipe_diagram(
        recipes_dir / "implementation.yaml", recipes_dir=recipes_dir, out_dir=tmp_path
    ).render_markdown()
    chain_line = _find_for_each_chain_line(content)
    assert chain_line is not None, "No horizontal chain found in FOR EACH block"
    assert "retry_worktree" not in chain_line
    assert "fix" not in chain_line
    assert "next_or_done" not in chain_line
    assert "verify" in chain_line
    assert "implement" in chain_line
    assert "test" in chain_line
    assert "merge" in chain_line


def test_next_or_done_rendered_as_footer_routing_block(tmp_path: Path) -> None:
    """T-NEW-3: next_or_done has on_result_conditions — it must appear as a
    └── footer block with labeled routing paths, not as an inline chain token.
    """
    from autoskillit.core.paths import pkg_root  # noqa: PLC0415

    recipes_dir = pkg_root() / "recipes"
    content = generate_recipe_diagram(
        recipes_dir / "implementation.yaml", recipes_dir=recipes_dir, out_dir=tmp_path
    ).render_markdown()
    graph_section = _extract_graph_section(content)
    assert "└── next_or_done" in graph_section, (
        "next_or_done must appear on a └── footer line, not as an inline chain token"
    )
    assert "→" in graph_section


@pytest.mark.parametrize(
    "recipe_name",
    [
        "implementation",
        "bugfix-loop",
        "smoke-test",
        "remediation",
        "audit-and-fix",
        "implementation-groups",
        "pr-merge-pipeline",
    ],
)
def test_terminal_steps_have_no_emoji(recipe_name: str, tmp_path: Path) -> None:
    """T-NEW-4: Issue #223 requires ASCII only. ⏹ must not appear on terminal steps."""
    from autoskillit.core.paths import pkg_root  # noqa: PLC0415

    recipes_dir = pkg_root() / "recipes"
    content = generate_recipe_diagram(
        recipes_dir / f"{recipe_name}.yaml", recipes_dir=recipes_dir, out_dir=tmp_path
    ).render_markdown()
    assert "⏹" not in content, f"Emoji ⏹ found in {recipe_name} (spec: ASCII only)"


def test_flow_line_omitted_when_summary_empty(tmp_path: Path) -> None:
    """T-NEW-5: When summary is absent, **Flow:** must not appear.

    Emitting '**Flow:** ' (trailing space, blank content) is not in the SKILL.md spec.
    """
    yaml_path = tmp_path / "no-summary.yaml"
    yaml_path.write_text(
        "name: no-summary\nsteps:\n"
        "  step1:\n    tool: run_skill\n    with:\n      skill_command: /foo\n"
        "    on_success: done\n    on_failure: escalate\n"
        "  done:\n    action: stop\n    message: Done.\n"
        "  escalate:\n    action: stop\n    message: Failed.\n"
    )
    content = generate_recipe_diagram(
        yaml_path, recipes_dir=tmp_path, out_dir=tmp_path / "out"
    ).render_markdown()
    assert "**Flow:**" not in content


@pytest.mark.parametrize(
    "recipe_name",
    [
        "implementation",
        "bugfix-loop",
        "smoke-test",
        "remediation",
        "audit-and-fix",
        "implementation-groups",
        "pr-merge-pipeline",
    ],
)
def test_bundled_recipe_ingredient_descriptions_are_single_phrase(
    recipe_name: str, tmp_path: Path
) -> None:
    """T-NEW-6: Spec 'Keep descriptions short — one phrase, not a sentence.'

    Every ingredient description rendered into the diagram Inputs table must be
    ≤ 80 characters.  Multi-sentence folded-YAML scalars must NOT appear verbatim.
    """
    from autoskillit.core.paths import pkg_root  # noqa: PLC0415

    recipes_dir = pkg_root() / "recipes"
    content = generate_recipe_diagram(
        recipes_dir / f"{recipe_name}.yaml", recipes_dir=recipes_dir, out_dir=tmp_path
    ).render_markdown()
    in_inputs = False
    desc_col_idx: int | None = None
    for line in content.splitlines():
        if line.strip().startswith("| Name |"):
            header_parts = [p.strip() for p in line.split("|") if p.strip()]
            desc_col_idx = next(
                (i for i, h in enumerate(header_parts) if h.lower() == "description"), None
            )
            in_inputs = True
            continue
        if in_inputs and line.startswith("| ---"):
            continue
        if in_inputs and line.startswith("|"):
            if desc_col_idx is None:
                continue
            parts = [p.strip() for p in line.split("|") if p.strip()]
            if len(parts) > desc_col_idx:
                description = parts[desc_col_idx]
                assert len(description) <= 80, (
                    f"{recipe_name}: description '{description[:40]}...' "
                    f"is {len(description)} chars (max 80)"
                )
        elif in_inputs and not line.startswith("|"):
            break


# ---------------------------------------------------------------------------
# T-SENT-1..4: build_recipe_graph sentinel awareness
# ---------------------------------------------------------------------------

_SENTINEL_ESCALATE_YAML = """
name: sentinel-test
description: Recipe with escalate as pure sentinel (not a step)
steps:
  start:
    tool: run_skill
    with:
      skill_command: /run-something
    on_success: done
    on_failure: done
  done:
    action: stop
    message: "Finished"
# Note: on_exhausted defaults to "escalate" but there is no "escalate" step.
# build_recipe_graph must NOT warn about this.
"""


class TestBuildRecipeGraphSentinels:
    """T-SENT-1..4: build_recipe_graph must not warn on terminal sentinel targets."""

    @pytest.fixture
    def sentinel_recipe(self, tmp_path):
        p = tmp_path / "sentinel-test.yaml"
        p.write_text(_SENTINEL_ESCALATE_YAML)
        from autoskillit.recipe.io import load_recipe  # noqa: PLC0415

        return load_recipe(p)

    def test_no_warning_for_default_escalate_sentinel(self, sentinel_recipe):
        """T-SENT-1: Default on_exhausted='escalate' sentinel emits zero warnings."""
        import structlog.testing  # noqa: PLC0415

        from autoskillit.recipe._analysis import build_recipe_graph  # noqa: PLC0415

        with structlog.testing.capture_logs() as cap_logs:
            build_recipe_graph(sentinel_recipe)
        warning_events = [entry for entry in cap_logs if entry.get("log_level") == "warning"]
        assert warning_events == [], f"Unexpected warnings: {warning_events}"

    def test_no_warning_for_explicit_done_sentinel(self, tmp_path):
        """T-SENT-2: Explicit on_exhausted='done' sentinel emits zero warnings."""
        import structlog.testing  # noqa: PLC0415

        from autoskillit.recipe._analysis import build_recipe_graph  # noqa: PLC0415
        from autoskillit.recipe.io import load_recipe  # noqa: PLC0415

        yaml_content = """
name: done-sentinel-test
description: Recipe with done as explicit exhausted target
steps:
  start:
    tool: run_skill
    with:
      skill_command: /run-something
    on_success: finish
    on_exhausted: done
  finish:
    action: stop
    message: "Done"
"""
        p = tmp_path / "done-sentinel.yaml"
        p.write_text(yaml_content)
        recipe = load_recipe(p)
        with structlog.testing.capture_logs() as cap_logs:
            build_recipe_graph(recipe)
        warning_events = [entry for entry in cap_logs if entry.get("log_level") == "warning"]
        assert warning_events == [], f"Unexpected warnings: {warning_events}"

    def test_still_warns_for_truly_unknown_target(self, tmp_path):
        """T-SENT-3: Genuinely unknown routing targets still emit warnings."""
        import structlog.testing  # noqa: PLC0415

        from autoskillit.recipe._analysis import build_recipe_graph  # noqa: PLC0415
        from autoskillit.recipe.io import load_recipe  # noqa: PLC0415

        yaml_content = """
name: unknown-target-test
description: Recipe with a genuinely unknown routing target
steps:
  start:
    tool: run_skill
    with:
      skill_command: /run-something
    on_success: nonexistent_step
  done:
    action: stop
    message: "Done"
"""
        p = tmp_path / "unknown-target.yaml"
        p.write_text(yaml_content)
        recipe = load_recipe(p)
        with structlog.testing.capture_logs() as cap_logs:
            build_recipe_graph(recipe)
        warning_events = [entry for entry in cap_logs if entry.get("log_level") == "warning"]
        assert any("nonexistent_step" in str(e) for e in warning_events), (
            "Expected warning for unknown non-sentinel target"
        )

    def test_build_recipe_graph_no_warning_for_action_step_exhausted(self, tmp_path):
        """T-SENT-4: Action steps (stop/confirm/route) do not warn on on_exhausted edges."""
        import structlog.testing  # noqa: PLC0415

        from autoskillit.recipe._analysis import build_recipe_graph  # noqa: PLC0415
        from autoskillit.recipe.io import load_recipe  # noqa: PLC0415

        yaml_content = """
name: action-step-test
description: Recipe where a stop step has default on_exhausted
steps:
  start:
    tool: run_skill
    with:
      skill_command: /run-something
    on_success: done
  done:
    action: stop
    message: "All done"
  # done.on_exhausted defaults to "escalate" — action step, should not warn
"""
        p = tmp_path / "action-step.yaml"
        p.write_text(yaml_content)
        recipe = load_recipe(p)
        with structlog.testing.capture_logs() as cap_logs:
            build_recipe_graph(recipe)
        warning_events = [entry for entry in cap_logs if entry.get("log_level") == "warning"]
        assert warning_events == [], (
            f"Unexpected warnings from action-step exhausted edge: {warning_events}"
        )


# ---------------------------------------------------------------------------
# T-SPEC-1, T-SPATIAL-1, T-VER-1 — Spec oracle, spatial constraints, version gate
# ---------------------------------------------------------------------------

_SPEC_FIXTURES = Path(__file__).parent / "fixtures"
_MAX_LINE_WIDTH = 120


def test_renderer_matches_visual_spec(tmp_path: Path) -> None:
    """T-SPEC-1: Renderer output must match the committed hand-crafted spec fixture.

    This is the correctness oracle. Fails until all renderer bugs are fixed.
    spec_diagram_expected.md is committed and NEVER auto-regenerated — it is the
    definition of 'correct'. Updating it requires deliberate authorship.
    """
    from autoskillit.recipe.diagrams import generate_recipe_diagram  # noqa: PLC0415

    spec_recipe = _SPEC_FIXTURES / "spec_diagram_recipe.yaml"
    spec_expected = _SPEC_FIXTURES / "spec_diagram_expected.md"

    out_dir = tmp_path / "diagrams"
    out_dir.mkdir()
    actual = generate_recipe_diagram(
        spec_recipe, recipes_dir=spec_recipe.parent, out_dir=out_dir
    ).render_markdown()
    expected = spec_expected.read_text()

    assert actual == expected, (
        "Renderer output does not match committed visual spec. "
        "If the renderer was intentionally changed, update spec_diagram_expected.md "
        "AND bump DIAGRAM_FORMAT_VERSION in diagrams.py."
    )


@pytest.mark.parametrize(
    "recipe_name",
    [
        "implementation",
        "implementation-groups",
        "bugfix-loop",
        "audit-and-fix",
        "remediation",
        "smoke-test",
        "pr-merge-pipeline",
        "batch-implementation",
        "dev-sprint",
    ],
)
def test_no_graph_line_exceeds_width_limit(recipe_name: str, tmp_path: Path) -> None:
    """T-SPATIAL-1: No graph-body line may exceed 120 chars for any bundled recipe.

    Checks only the flow graph body (before the terminal-step separator line)
    to catch 'unbounded horizontal chain' and 'unbounded side-leg indentation' bugs
    independently of the spec fixture, for all production recipes.
    """
    from autoskillit.core.paths import pkg_root  # noqa: PLC0415
    from autoskillit.recipe.diagrams import generate_recipe_diagram  # noqa: PLC0415

    recipes_dir = pkg_root() / "recipes"
    pipeline = recipes_dir / f"{recipe_name}.yaml"
    out_dir = tmp_path / "diagrams"
    out_dir.mkdir()
    diagram = generate_recipe_diagram(
        pipeline, recipes_dir=recipes_dir, out_dir=out_dir
    ).render_markdown()

    in_graph = False
    past_sep = False
    violations = []
    for lineno, line in enumerate(diagram.splitlines(), 1):
        if line.strip() == "### Graph":
            in_graph = True
            continue
        if in_graph and line.startswith("### "):
            break
        if in_graph and line.startswith(DIAGRAM_SECTION_SEPARATOR):
            past_sep = True
        if in_graph and not past_sep and len(line) > _MAX_LINE_WIDTH:
            violations.append((lineno, len(line), line[:60]))

    assert not violations, (
        f"{recipe_name}: {len(violations)} graph-body line(s) exceed {_MAX_LINE_WIDTH} chars.\n"
        + "\n".join(f"  line {ln}: {w} chars: {p!r}..." for ln, w, p in violations)
    )


def test_spec_fixture_version_matches_diagram_format_constant() -> None:
    """T-VER-1: spec_diagram_expected.md must embed the current DIAGRAM_FORMAT_VERSION.

    Enforcement gate: when rendering logic changes, T-SPEC-1 fails → developer
    updates the spec → T-VER-1 fails if DIAGRAM_FORMAT_VERSION wasn't also bumped.
    Both must be updated together. This gate makes version bumps mandatory.
    """
    import re  # noqa: PLC0415

    from autoskillit.recipe.diagrams import DIAGRAM_FORMAT_VERSION  # noqa: PLC0415

    spec_expected = _SPEC_FIXTURES / "spec_diagram_expected.md"
    content = spec_expected.read_text()
    m = re.search(r"<!-- autoskillit-diagram-format: (\S+) -->", content)
    assert m is not None, "spec_diagram_expected.md missing format version comment"
    assert m.group(1) == DIAGRAM_FORMAT_VERSION, (
        f"spec embeds {m.group(1)!r} but DIAGRAM_FORMAT_VERSION={DIAGRAM_FORMAT_VERSION!r}. "
        "Either bump DIAGRAM_FORMAT_VERSION or regenerate the spec fixture."
    )


# ---------------------------------------------------------------------------
# RecipeDiagram structured model tests
# ---------------------------------------------------------------------------


def test_recipe_diagram_from_recipe_populates_all_fields(
    tmp_path: Path, sample_recipe_yaml: Path
) -> None:
    """RecipeDiagram.from_recipe extracts all diagram components as structured data."""
    from autoskillit.recipe.io import load_recipe  # noqa: PLC0415

    recipe = load_recipe(sample_recipe_yaml)
    model = RecipeDiagram.from_recipe(recipe, sample_recipe_yaml)

    assert model.name == "my-recipe"
    assert model.description == "A test recipe for diagram generation"
    assert model.flow_summary == "step1 -> done"
    assert model.graph_text  # non-empty
    assert isinstance(model.input_rows, list)
    assert len(model.input_rows) >= 1
    assert model.input_rows[0].name == "task"
    assert model.input_rows[0].description == "What to do"
    assert model.recipe_hash.startswith("sha256:")
    assert model.format_version
    assert "Use AutoSkillit tools only" in model.kitchen_rules


def test_recipe_diagram_render_markdown_matches_current_format(
    tmp_path: Path, sample_recipe_yaml: Path
) -> None:
    """render_markdown() output is byte-identical to generate_recipe_diagram() file output."""
    recipes_dir = tmp_path / "recipes"
    model = generate_recipe_diagram(sample_recipe_yaml, recipes_dir)
    md_from_method = model.render_markdown()
    md_from_file = (recipes_dir / "diagrams" / "my-recipe.md").read_text()
    assert md_from_method == md_from_file


def test_recipe_diagram_render_terminal_contains_no_markdown(
    tmp_path: Path, sample_recipe_yaml: Path
) -> None:
    """render_terminal() output must not contain Markdown syntax."""
    model = build_recipe_diagram(
        __import__("autoskillit.recipe.io", fromlist=["load_recipe"]).load_recipe(
            sample_recipe_yaml
        ),
        sample_recipe_yaml,
    )
    output = model.render_terminal()
    assert "<!--" not in output
    assert "|---" not in output
    assert not any(line.lstrip().startswith("## ") for line in output.splitlines())
    assert "**Flow:**" not in output
    # Positive assertion: ingredient data IS present
    assert "task" in output
    assert "What to do" in output


def test_recipe_diagram_render_terminal_aligns_columns(
    tmp_path: Path, sample_recipe_yaml: Path
) -> None:
    """render_terminal() input rows are column-aligned with padding."""
    from autoskillit.recipe.io import load_recipe  # noqa: PLC0415

    model = build_recipe_diagram(load_recipe(sample_recipe_yaml), sample_recipe_yaml)
    output = model.render_terminal()
    # Header and separator lines present
    assert "NAME" in output
    assert "DESCRIPTION" in output
    assert "DEFAULT" in output
    # Data lines have consistent column positions (all start with 2 spaces)
    data_lines = [
        ln
        for ln in output.splitlines()
        if ln.startswith("  ") and "---" not in ln and "NAME" not in ln and ln.strip()
    ]
    assert len(data_lines) >= 1
