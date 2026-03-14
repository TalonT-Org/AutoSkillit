"""Tests for recipe/diagrams.py — load, staleness, and suggestions."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.recipe.diagrams import (
    DIAGRAM_FORMAT_VERSION,
    check_diagram_staleness,
    diagram_stale_to_suggestions,
    load_recipe_diagram,
)
from autoskillit.recipe.io import load_recipe


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


@pytest.fixture
def sample_recipe_yaml(tmp_path: Path) -> Path:
    path = tmp_path / "my-recipe.yaml"
    path.write_text(_SAMPLE_RECIPE_YAML)
    return path


# ---------------------------------------------------------------------------
# DG-6: load_recipe_diagram
# ---------------------------------------------------------------------------


def test_load_returns_none_when_missing(tmp_path: Path) -> None:
    """DG-6: load_recipe_diagram returns None when diagram missing."""
    assert load_recipe_diagram("no-such-recipe", tmp_path / "recipes") is None


# ---------------------------------------------------------------------------
# DG-10: check_diagram_staleness
# ---------------------------------------------------------------------------


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
# T8, T9: _extract_routing_edges from _analysis.py
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# T10: bundled implementation diagram matches spec structure
# ---------------------------------------------------------------------------


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

    # Optional step bracket notation
    assert "(optional)" in graph_section, "Optional steps must use '(optional)' annotation."

    # Inputs section (not Ingredients)
    assert "### Inputs" in content, "Section header must be '### Inputs'."

    # Boolean defaults rendered as off/on
    inputs_start = content.index("### Inputs")
    inputs_section = content[inputs_start:]
    assert "off" in inputs_section, (
        "Boolean-default ingredients must render as 'off' in Inputs table."
    )


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
# T-VER-1: spec fixture version matches DIAGRAM_FORMAT_VERSION constant
# ---------------------------------------------------------------------------

_SPEC_FIXTURES = Path(__file__).parent / "fixtures"


def test_spec_fixture_version_matches_diagram_format_constant() -> None:
    """T-VER-1: spec_diagram_expected.md must embed the current DIAGRAM_FORMAT_VERSION.

    Enforcement gate: when rendering logic changes, T-SPEC-1 fails → developer
    updates the spec → T-VER-1 fails if DIAGRAM_FORMAT_VERSION wasn't also bumped.
    Both must be updated together. This gate makes version bumps mandatory.
    """
    import re  # noqa: PLC0415

    spec_expected = _SPEC_FIXTURES / "spec_diagram_expected.md"
    content = spec_expected.read_text()
    m = re.search(r"<!-- autoskillit-diagram-format: (\S+) -->", content)
    assert m is not None, "spec_diagram_expected.md missing format version comment"
    assert m.group(1) == DIAGRAM_FORMAT_VERSION, (
        f"spec embeds {m.group(1)!r} but DIAGRAM_FORMAT_VERSION={DIAGRAM_FORMAT_VERSION!r}. "
        "Either bump DIAGRAM_FORMAT_VERSION or regenerate the spec fixture."
    )
