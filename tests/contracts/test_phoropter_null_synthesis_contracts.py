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
    cats = _frontmatter()["categories"]
    assert isinstance(cats, list) and len(cats) > 0


def test_frontmatter_description_mentions_synthesis() -> None:
    assert "synthesis" in str(_frontmatter().get("description", "")).lower()


def test_arguments_positional_source_dir() -> None:
    assert "source_dir" in _text()


def test_arguments_positional_capture_dir() -> None:
    assert "capture_dir" in _text()


def test_output_token_synthesis_result_path() -> None:
    assert "synthesis_result_path" in _text()


def test_output_token_emitted_as_assignment() -> None:
    assert re.search(r"synthesis_result_path\s*=", _text()), (
        "phoropter-null-synthesis SKILL.md must emit synthesis_result_path token as an assignment"
    )


def test_never_no_priority_ordering() -> None:
    assert "priority" in _text().lower()


def test_never_no_yaml_figure_spec_parsing() -> None:
    assert "yaml:figure-spec" not in _text(), (
        "phoropter-null-synthesis must not reference yaml:figure-spec "
        "(null synthesis does not parse figure specs)"
    )


def test_skill_contracts_yaml_write_behavior_always() -> None:
    data = load_yaml(CONTRACTS_YAML)
    entry = data["skills"].get("phoropter-null-synthesis")
    assert entry is not None
    assert entry.get("write_behavior") == "always"


def test_skill_contracts_yaml_output_synthesis_result_path() -> None:
    data = load_yaml(CONTRACTS_YAML)
    entry = data["skills"].get("phoropter-null-synthesis")
    output_names = {o.get("name") for o in entry.get("outputs", []) if isinstance(o, dict)}
    assert "synthesis_result_path" in output_names
