"""Contract tests: synthesize-vis-plan SKILL.md structural and content invariants."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

_SKILL_MD = (
    Path(__file__).parents[2] / "src/autoskillit/skills_extended/synthesize-vis-plan/SKILL.md"
)


def _text() -> str:
    assert _SKILL_MD.exists(), "synthesize-vis-plan/SKILL.md does not exist"
    return _SKILL_MD.read_text()


def _frontmatter() -> dict:
    lines = _text().splitlines()
    assert lines[0].strip() == "---"
    end = next(i for i, ln in enumerate(lines[1:], 1) if ln.strip() == "---")
    return yaml.safe_load("\n".join(lines[1:end]))


def test_skill_md_exists() -> None:
    assert _SKILL_MD.exists()


def test_frontmatter_name() -> None:
    assert _frontmatter()["name"] == "synthesize-vis-plan"


def test_frontmatter_categories() -> None:
    assert _frontmatter()["categories"] == ["research", "vis-lens"]


def test_frontmatter_description_mentions_synthesize() -> None:
    desc = _frontmatter().get("description", "")
    assert "synthesize" in desc.lower() or "vis-lens phoropter" in desc.lower()


def test_arguments_positional_args() -> None:
    text = _text()
    for arg in ("source_dir", "experiment_plan_path", "capture_dir"):
        assert arg in text, f"Arguments must document {arg}"


def test_arguments_tier_c_routing_fields() -> None:
    text = _text()
    for field in (
        "tier_c_lens",
        "methodology_tradition",
        "disambiguation_rule_applied",
        "applied_union_rules",
        "precedence_trace",
    ):
        assert field in text, f"Arguments must document Tier-C field {field}"


def test_tier_c_lens_exact_token_name() -> None:
    """Must use tier_c_lens, not primary_lens or other variants."""
    assert "tier_c_lens" in _text()


def test_methodology_tradition_exact_token_name() -> None:
    """Arguments must use methodology_tradition, not primary_tradition as the arg name."""
    text = _text()
    assert "methodology_tradition" in text


def test_conflict_resolution_hierarchy() -> None:
    text = _text()
    assert "accessibility" in text
    assert "anti-pattern" in text or "anti_pattern" in text
    assert "methodology-norms" in text or "methodology_norms" in text
    assert "chart-select" in text or "chart_select" in text


def test_conflict_resolution_log_table_columns() -> None:
    text = _text()
    for col in (
        "Fig ID",
        "Dimension",
        "Lens A",
        "Lens A Rec",
        "Lens B",
        "Lens B Rec",
        "Winner",
        "Reason",
    ):
        assert col in text, f"Conflict Resolution Log must include column: {col}"


def test_output_paths_use_synthesize_vis_plan() -> None:
    """All output file paths must reference synthesize-vis-plan/, not plan-visualization/."""
    text = _text()
    assert "synthesize-vis-plan/" in text
    _OUTPUT_PATH_RE = re.compile(
        r"\{\{AUTOSKILLIT_TEMP\}\}/plan-visualization/",
    )
    matches = _OUTPUT_PATH_RE.findall(text)
    assert not matches, (
        f"Found {len(matches)} output path(s) referencing plan-visualization/ "
        f"instead of synthesize-vis-plan/"
    )


def test_three_output_tokens() -> None:
    text = _text()
    for token in (
        "visualization_plan_path",
        "report_plan_path",
        "visualization_plan_trace_path",
    ):
        assert token in text, f"Must emit structured token: {token}"


def test_yaml_figure_spec_parsing() -> None:
    text = _text()
    assert "yaml:figure-spec" in text
    assert "capture_dir" in text


def test_visualization_plan_content_structure() -> None:
    text = _text()
    for section in (
        "Figure Inventory",
        "Figure Specifications",
        "Code Allocation Hints",
        "Conflict Resolution Log",
    ):
        assert section in text, f"visualization-plan.md must contain: {section}"


def test_report_plan_content_structure() -> None:
    assert "Section Outline" in _text()


def test_trace_file_populates_from_arguments() -> None:
    text = _text()
    assert "visualization-plan-trace.md" in text
    assert "primary_tradition" in text


def test_on_success_routes_to_create_worktree() -> None:
    assert "create_worktree" in _text()


def test_on_failure_routes_to_escalate_stop() -> None:
    assert "escalate_stop" in _text()


def test_no_subagents_constraint() -> None:
    text = _text().lower()
    assert "subagent" in text or "sub-agent" in text


def test_applied_union_rules_from_arguments_not_recipe() -> None:
    """applied_union_rules must be sourced from methodology-norms args, not recipe context."""
    text = _text()
    assert "applied_union_rules" in text
    assert "select-vis-lenses" in text
