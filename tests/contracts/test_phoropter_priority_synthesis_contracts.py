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


def test_frontmatter_categories_include_vis_lens() -> None:
    assert "vis-lens" in _frontmatter()["categories"]


def test_frontmatter_description_mentions_synthesis_or_phoropter() -> None:
    desc = str(_frontmatter().get("description", "")).lower()
    assert "synthesis" in desc or "phoropter" in desc


def test_hierarchy_arg_documented() -> None:
    assert "--hierarchy" in _text()


def test_hierarchy_arg_is_named_arg() -> None:
    assert "--hierarchy=" in _text()


def test_hierarchy_is_comma_separated() -> None:
    text = _text()
    assert "comma" in text.lower() or "accessibility,anti-pattern" in text


def test_hierarchy_default_value_documented() -> None:
    assert "accessibility,anti-pattern,methodology-norms,chart-select" in _text()


def test_hierarchy_contains_all_four_tiers() -> None:
    text = _text()
    assert "accessibility" in text
    assert "anti-pattern" in text
    assert "methodology-norms" in text
    assert "chart-select" in text


def test_three_output_tokens_present() -> None:
    text = _text()
    assert "synthesis_result_path" in text
    assert "report_path" in text
    assert "synthesis_trace_path" in text


def test_synthesis_result_path_token_emitted() -> None:
    assert re.search(r"synthesis_result_path\s*=", _text())


def test_report_path_token_emitted() -> None:
    assert re.search(r"report_path\s*=", _text())


def test_synthesis_trace_path_token_emitted() -> None:
    assert re.search(r"synthesis_trace_path\s*=", _text())


def test_no_subagents_constraint() -> None:
    assert re.search(r"never[\s\S]{0,500}sub.?agent", _text().lower())


def test_output_paths_use_phoropter_priority_synthesis() -> None:
    assert "phoropter-priority-synthesis/" in _text()


def test_skill_contracts_yaml_registers_skill() -> None:
    data = load_yaml(CONTRACTS_YAML)
    entry = data["skills"].get("phoropter-priority-synthesis")
    assert entry is not None, "phoropter-priority-synthesis not found in skill_contracts.yaml"


def test_skill_contracts_yaml_write_behavior_always() -> None:
    data = load_yaml(CONTRACTS_YAML)
    entry = data["skills"]["phoropter-priority-synthesis"]
    assert entry.get("write_behavior") == "always"


def test_skill_contracts_yaml_declares_synthesis_result_path_output() -> None:
    data = load_yaml(CONTRACTS_YAML)
    entry = data["skills"]["phoropter-priority-synthesis"]
    assert any(o["name"] == "synthesis_result_path" for o in entry.get("outputs", []))


def test_skill_contracts_yaml_declares_report_path_output() -> None:
    data = load_yaml(CONTRACTS_YAML)
    entry = data["skills"]["phoropter-priority-synthesis"]
    assert any(o["name"] == "report_path" for o in entry.get("outputs", []))


def test_skill_contracts_yaml_declares_synthesis_trace_path_output() -> None:
    data = load_yaml(CONTRACTS_YAML)
    entry = data["skills"]["phoropter-priority-synthesis"]
    assert any(o["name"] == "synthesis_trace_path" for o in entry.get("outputs", []))
