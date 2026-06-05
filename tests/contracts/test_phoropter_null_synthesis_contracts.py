"""Contract tests: phoropter-null-synthesis SKILL.md structural and content invariants."""

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
    / "phoropter-null-synthesis"
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
    assert SKILL_PATH.exists(), "phoropter-null-synthesis/SKILL.md does not exist"
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
    assert _frontmatter()["name"] == "phoropter-null-synthesis"


def test_frontmatter_categories() -> None:
    assert _frontmatter()["categories"] == ["research", "arch-lens", "exp-lens"]


def test_frontmatter_write_paths() -> None:
    assert _frontmatter()["write_paths"] == ["{{AUTOSKILLIT_TEMP}}/phoropter-null-synthesis/"]


def test_frontmatter_description_is_directive() -> None:
    desc = str(_frontmatter().get("description", "")).strip()
    assert desc, "description must not be empty"
    first_word = desc.split()[0].lower()
    assert not first_word.endswith("s") or first_word in ("pass",), (
        f"Description should begin with a verb (directive language), got: {first_word!r}"
    )


def test_arguments_positional_args() -> None:
    text = _text()
    for arg in ("source_dir", "capture_dir"):
        assert arg in text, f"Arguments must document {arg}"


def test_no_yaml_figure_spec() -> None:
    assert "yaml:figure-spec" not in _text(), (
        "phoropter-null-synthesis must NOT reference yaml:figure-spec"
    )


def test_no_priority_ordering() -> None:
    text = _text().lower()
    assert re.search(r"never[\s\S]{0,500}priority.order", text), (
        "SKILL.md must prohibit priority ordering in a NEVER context"
    )


def test_no_reordering_or_filtering() -> None:
    text = _text().lower()
    assert re.search(r"never[\s\S]{0,500}reorder", text), (
        "SKILL.md must prohibit reordering in a NEVER context"
    )


def test_no_subagents_constraint() -> None:
    text = _text().lower()
    assert re.search(r"never[\s\S]{0,500}sub.?agent", text), (
        "SKILL.md must prohibit sub-agents in a NEVER context"
    )


def test_anti_fabrication_constraint() -> None:
    text = _text().lower()
    assert re.search(r"never[\s\S]{0,500}fabricat", text), (
        "SKILL.md must prohibit fabrication in a NEVER context"
    )


def test_lexicographic_order() -> None:
    text = _text().lower()
    assert "lexicographic" in text or "alphabetical" in text, (
        "Workflow must specify lexicographic/alphabetical file read order"
    )


def test_synthesis_result_token() -> None:
    assert re.search(r"synthesis_result_path\s*=", _text()), (
        "SKILL.md must emit synthesis_result_path token"
    )


def test_output_path_uses_phoropter_null_synthesis() -> None:
    text = _text()
    assert "phoropter-null-synthesis/" in text
    assert not re.search(r"\{\{AUTOSKILLIT_TEMP\}\}/synthesize-vis-plan/", text), (
        "Output paths must not reference synthesize-vis-plan/"
    )


def test_workflow_four_steps() -> None:
    text = _text()
    assert "Step 0" in text
    assert "Step 1" in text
    assert "Step 2" in text
    assert "Step 3" in text
    assert "Step 4" not in text, "Workflow must have exactly 4 steps (0-3)"


def test_important_callout_present() -> None:
    text = _text()
    assert "IMPORTANT" in text
    assert "literal plain text" in text
    assert "no markdown formatting" in text


def test_write_unconditionally() -> None:
    text = _text().lower()
    assert "unconditionally" in text or "even" in text, (
        "Must write synthesis-result.md unconditionally even for empty capture_dir"
    )


def test_contract_in_skill_contracts_yaml() -> None:
    data = load_yaml(CONTRACTS_YAML)
    entry = data["skills"].get("phoropter-null-synthesis")
    assert entry is not None, "phoropter-null-synthesis not found in skill_contracts.yaml"


def test_contract_inputs() -> None:
    data = load_yaml(CONTRACTS_YAML)
    entry = data["skills"]["phoropter-null-synthesis"]
    input_names = {i["name"] for i in entry.get("inputs", [])}
    assert "source_dir" in input_names
    assert "capture_dir" in input_names


def test_contract_outputs() -> None:
    data = load_yaml(CONTRACTS_YAML)
    entry = data["skills"]["phoropter-null-synthesis"]
    output_names = {o["name"] for o in entry.get("outputs", [])}
    assert "synthesis_result_path" in output_names


def test_contract_output_pattern() -> None:
    data = load_yaml(CONTRACTS_YAML)
    entry = data["skills"]["phoropter-null-synthesis"]
    patterns = entry.get("expected_output_patterns", [])
    assert any("synthesis_result_path" in p for p in patterns)


def test_contract_write_behavior() -> None:
    data = load_yaml(CONTRACTS_YAML)
    entry = data["skills"]["phoropter-null-synthesis"]
    assert entry.get("write_behavior") == "always"
