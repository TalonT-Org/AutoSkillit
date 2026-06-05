"""Contract tests: phoropter-priority-synthesis SKILL.md structural and content invariants."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from autoskillit.core.io import load_yaml

pytestmark = [pytest.mark.small]

SKILL_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "src"
    / "autoskillit"
    / "skills_extended"
    / "phoropter-priority-synthesis"
    / "SKILL.md"
)
CONTRACTS_YAML = (
    Path(__file__).resolve().parent.parent.parent
    / "src"
    / "autoskillit"
    / "recipe"
    / "skill_contracts.yaml"
)


def _text() -> str:
    assert SKILL_PATH.exists(), "phoropter-priority-synthesis/SKILL.md does not exist"
    return SKILL_PATH.read_text()


def _frontmatter() -> dict[str, object]:
    lines = _text().splitlines()
    assert lines, "SKILL.md is empty"
    assert lines[0].strip() == "---"
    end = next(
        (i for i, ln in enumerate(lines[1:], 1) if ln.strip() == "---"),
        None,
    )
    assert end is not None, "SKILL.md frontmatter missing closing ---"
    return load_yaml("\n".join(lines[1:end]))


def test_skill_md_exists() -> None:
    assert SKILL_PATH.exists()


def test_frontmatter_name() -> None:
    assert _frontmatter()["name"] == "phoropter-priority-synthesis"


def test_frontmatter_categories() -> None:
    assert _frontmatter()["categories"] == ["research", "vis-lens"]


def test_frontmatter_description() -> None:
    desc = str(_frontmatter().get("description", ""))
    assert desc.strip(), "description must not be empty"


def test_when_to_use_positioning() -> None:
    text = _text()
    assert "synthesize" in text.lower()
    assert "apply" in text.lower()
    assert "create_worktree" in text.lower()


def test_arguments_positional_args() -> None:
    text = _text()
    for arg in ("source_dir", "experiment_plan_path", "capture_dir"):
        assert arg in text, f"Arguments must document {arg}"


def test_arguments_hierarchy_flag() -> None:
    text = _text()
    assert "--hierarchy" in text
    assert "accessibility" in text
    assert "anti-pattern" in text
    assert "methodology-norms" in text
    assert "chart-select" in text


def test_never_parse_yaml_figure_spec() -> None:
    text = _text().lower()
    assert re.search(r"never[\s\S]{0,500}yaml:figure-spec", text), (
        "SKILL.md must prohibit yaml:figure-spec parsing in a NEVER context"
    )


def test_never_write_outside_temp() -> None:
    text = _text()
    assert "phoropter-priority-synthesis/" in text
    assert re.search(
        r"NEVER[\s\S]{0,500}outside.*phoropter-priority-synthesis",
        text,
    ), "Must prohibit writing outside phoropter-priority-synthesis/"


def test_never_omit_tokens() -> None:
    text = _text().lower()
    assert re.search(r"never[\s\S]{0,500}omit[\s\S]{0,200}token", text), (
        "Must prohibit omitting required path tokens"
    )


def test_always_emit_literal_plain_text() -> None:
    text = _text()
    assert "literal plain text" in text


def test_conflict_resolution_log_columns() -> None:
    text = _text()
    for col in ("Lens A", "Lens A Rec", "Lens B", "Lens B Rec", "Dimension", "Winner", "Reason"):
        assert col in text, f"Conflict Resolution Log must include column: {col}"


def test_workflow_five_steps() -> None:
    text = _text()
    assert "Step 0" in text
    assert "Step 1" in text
    assert "Step 2" in text
    assert "Step 3" in text
    assert "Step 4" in text


def test_three_output_tokens() -> None:
    text = _text()
    for token in ("synthesis_result_path", "report_path", "synthesis_trace_path"):
        assert re.search(rf"{token}\s*=", text), f"Must emit structured token: {token}"


def test_important_callout_present() -> None:
    text = _text()
    assert "IMPORTANT" in text
    assert "literal plain text" in text
    assert "no markdown formatting" in text


def test_contract_in_skill_contracts_yaml() -> None:
    data = load_yaml(CONTRACTS_YAML)
    entry = data["skills"].get("phoropter-priority-synthesis")
    assert entry is not None, "phoropter-priority-synthesis not found in skill_contracts.yaml"


def test_contract_inputs() -> None:
    data = load_yaml(CONTRACTS_YAML)
    entry = data["skills"]["phoropter-priority-synthesis"]
    input_names = {i["name"] for i in entry.get("inputs", [])}
    assert "source_dir" in input_names
    assert "experiment_plan_path" in input_names
    assert "capture_dir" in input_names
    assert "hierarchy" in input_names


def test_contract_outputs() -> None:
    data = load_yaml(CONTRACTS_YAML)
    entry = data["skills"]["phoropter-priority-synthesis"]
    output_names = {o["name"] for o in entry.get("outputs", [])}
    assert "synthesis_result_path" in output_names
    assert "report_path" in output_names
    assert "synthesis_trace_path" in output_names


def test_contract_three_output_patterns() -> None:
    data = load_yaml(CONTRACTS_YAML)
    entry = data["skills"]["phoropter-priority-synthesis"]
    patterns = entry.get("expected_output_patterns", [])
    assert len(patterns) == 3
    assert any("synthesis_result_path" in p for p in patterns)
    assert any("report_path" in p for p in patterns)
    assert any("synthesis_trace_path" in p for p in patterns)


def test_contract_write_behavior() -> None:
    data = load_yaml(CONTRACTS_YAML)
    entry = data["skills"]["phoropter-priority-synthesis"]
    assert entry.get("write_behavior") == "always"
