"""Tests for recipe._cmd_rpc.verify_plan_artifacts — deterministic salvage callable
for context-limit-stumbled plan-producing steps (issue #4305)."""

from __future__ import annotations

import pytest

from autoskillit.recipe._cmd_rpc import verify_plan_artifacts

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def _write(tmp_path, name, content="plan content"):
    path = tmp_path / name
    path.write_text(content)
    return str(path)


def test_single_absolute_path_salvaged(tmp_path):
    p = _write(tmp_path, "plan.md")
    result = verify_plan_artifacts(plan_parts=p)
    assert result == {"verdict": "salvaged", "plan_parts": p, "plan_path": p}


def test_newline_joined_two_paths_salvaged(tmp_path):
    a = _write(tmp_path, "plan_part_a.md")
    b = _write(tmp_path, "plan_part_b.md")
    result = verify_plan_artifacts(plan_parts=f"{a}\n{b}")
    assert result == {"verdict": "salvaged", "plan_parts": f"{a}\n{b}", "plan_path": a}


def test_comma_separated_two_paths_salvaged(tmp_path):
    a = _write(tmp_path, "plan_part_a.md")
    b = _write(tmp_path, "plan_part_b.md")
    result = verify_plan_artifacts(plan_parts=f"{a},{b}")
    assert result == {"verdict": "salvaged", "plan_parts": f"{a}\n{b}", "plan_path": a}


def test_json_list_repr_two_paths_salvaged(tmp_path):
    a = _write(tmp_path, "plan_part_a.md")
    b = _write(tmp_path, "plan_part_b.md")
    result = verify_plan_artifacts(plan_parts=f'["{a}", "{b}"]')
    assert result == {"verdict": "salvaged", "plan_parts": f"{a}\n{b}", "plan_path": a}


def test_python_list_repr_single_path_salvaged(tmp_path):
    a = _write(tmp_path, "plan.md")
    result = verify_plan_artifacts(plan_parts=f"['{a}']")
    assert result == {"verdict": "salvaged", "plan_parts": a, "plan_path": a}


def test_newline_joined_missing_file_unsalvageable(tmp_path):
    a = _write(tmp_path, "plan_part_a.md")
    missing = str(tmp_path / "plan_part_b.md")
    result = verify_plan_artifacts(plan_parts=f"{a}\n{missing}")
    assert result == {"verdict": "unsalvageable"}


def test_empty_input_unsalvageable():
    assert verify_plan_artifacts(plan_parts="") == {"verdict": "unsalvageable"}


def test_whitespace_input_unsalvageable():
    assert verify_plan_artifacts(plan_parts="   \n  ") == {"verdict": "unsalvageable"}


def test_relative_path_unsalvageable(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "plan.md").write_text("content")
    result = verify_plan_artifacts(plan_parts="plan.md")
    assert result == {"verdict": "unsalvageable"}


def test_existing_empty_file_unsalvageable(tmp_path):
    empty = tmp_path / "plan.md"
    empty.write_text("")
    result = verify_plan_artifacts(plan_parts=str(empty))
    assert result == {"verdict": "unsalvageable"}
