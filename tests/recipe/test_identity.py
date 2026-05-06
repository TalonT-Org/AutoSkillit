"""Tests for recipe identity hashing — composite hash computation, query, and re-run detection."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.medium]


def _write_recipe(tmp_path: Path, name: str = "test", *, skill_name: str = "") -> Path:
    """Write a minimal valid recipe YAML and return its path."""
    msg = f"/autoskillit:{skill_name}" if skill_name else "hi"
    content = (
        f"name: {name}\ndescription: d\nkitchen_rules:\n  - rule\n"
        f"steps:\n  s1:\n    tool: run_skill\n    message: '{msg}'\n"
        f"    on_success: done\n  done:\n    action: stop\n    message: Done\n"
    )
    p = tmp_path / f"{name}.yaml"
    p.write_text(content)
    return p


def _make_skill(skills_dir: Path, name: str, content: str = "# skill") -> None:
    """Create a skill directory with SKILL.md."""
    d = skills_dir / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(content)


def test_composite_hash_changes_with_recipe_content(tmp_path):
    from autoskillit.recipe.identity import compute_composite_hash
    from autoskillit.recipe.io import load_recipe

    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()

    p1 = _write_recipe(tmp_path, "test")
    r1 = load_recipe(p1)
    h1 = compute_composite_hash(p1, r1, skills_dir=skills_dir, project_dir=tmp_path)

    # Overwrite the same file with different step content (same name)
    p1.write_text(
        "name: test\ndescription: d\nkitchen_rules:\n  - rule\n"
        "steps:\n  s1:\n    tool: run_skill\n    message: 'different'\n"
        "    on_success: done\n  done:\n    action: stop\n    message: Done\n"
    )
    r2 = load_recipe(p1)
    h2 = compute_composite_hash(p1, r2, skills_dir=skills_dir, project_dir=tmp_path)

    assert h1 != h2


def test_composite_hash_changes_with_skill_content(tmp_path):
    from autoskillit.recipe.identity import compute_composite_hash
    from autoskillit.recipe.io import load_recipe

    skills_dir = tmp_path / "skills"
    _make_skill(skills_dir, "investigate", "# version 1")

    p = _write_recipe(tmp_path, skill_name="investigate")
    recipe = load_recipe(p)
    h1 = compute_composite_hash(p, recipe, skills_dir=skills_dir, project_dir=tmp_path)

    _make_skill(skills_dir, "investigate", "# version 2")
    h2 = compute_composite_hash(p, recipe, skills_dir=skills_dir, project_dir=tmp_path)

    assert h1 != h2


def test_composite_hash_changes_with_sub_recipe(tmp_path):
    from autoskillit.recipe.identity import compute_composite_hash
    from autoskillit.recipe.io import load_recipe

    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()

    sub_dir = tmp_path / ".autoskillit" / "recipes" / "sub-recipes"
    sub_dir.mkdir(parents=True)
    (sub_dir / "child.yaml").write_text(
        "name: child\ndescription: d\nsteps:\n"
        "  s1:\n    tool: run_cmd\n    message: echo hi\n    on_success: done\n"
        "  done:\n    action: stop\n    message: Done\n"
    )

    recipe_content = (
        "name: parent\ndescription: d\nkitchen_rules:\n  - rule\n"
        "steps:\n  s1:\n    sub_recipe: child\n    on_success: done\n"
        "  done:\n    action: stop\n    message: Done\n"
    )
    p = tmp_path / "parent.yaml"
    p.write_text(recipe_content)
    recipe = load_recipe(p)
    h1 = compute_composite_hash(p, recipe, skills_dir=skills_dir, project_dir=tmp_path)

    (sub_dir / "child.yaml").write_text(
        "name: child\ndescription: d modified\nsteps:\n"
        "  s1:\n    tool: run_cmd\n    message: echo bye\n    on_success: done\n"
        "  done:\n    action: stop\n    message: Done\n"
    )
    h2 = compute_composite_hash(p, recipe, skills_dir=skills_dir, project_dir=tmp_path)

    assert h1 != h2


def test_composite_hash_is_deterministic(tmp_path):
    from autoskillit.recipe.identity import compute_composite_hash
    from autoskillit.recipe.io import load_recipe

    skills_dir = tmp_path / "skills"
    _make_skill(skills_dir, "investigate")

    p = _write_recipe(tmp_path, skill_name="investigate")
    recipe = load_recipe(p)
    h1 = compute_composite_hash(p, recipe, skills_dir=skills_dir, project_dir=tmp_path)
    h2 = compute_composite_hash(p, recipe, skills_dir=skills_dir, project_dir=tmp_path)

    assert h1 == h2


def test_composite_hash_no_dependencies_differs_from_content_hash(tmp_path):
    from autoskillit.recipe.identity import compute_composite_hash
    from autoskillit.recipe.io import load_recipe

    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()

    p = _write_recipe(tmp_path)
    recipe = load_recipe(p)
    composite = compute_composite_hash(p, recipe, skills_dir=skills_dir, project_dir=tmp_path)
    content = "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()

    assert composite != content


# ---------------------------------------------------------------------------
# Part B: find_prior_runs tests
# ---------------------------------------------------------------------------


def test_find_prior_runs_empty(tmp_path):
    from autoskillit.recipe.identity import find_prior_runs

    result = find_prior_runs(tmp_path / "sessions.jsonl", composite_hash="sha256:abc")
    assert result == []


def test_find_prior_runs_by_composite_hash(tmp_path):
    from autoskillit.recipe.identity import find_prior_runs

    jsonl = tmp_path / "sessions.jsonl"
    entry = {
        "recipe_name": "impl",
        "recipe_composite_hash": "sha256:abc",
        "timestamp": "2026-04-01T00:00:00",
        "session_id": "s1",
        "success": True,
    }
    jsonl.write_text(json.dumps(entry) + "\n")
    result = find_prior_runs(jsonl, composite_hash="sha256:abc")
    assert len(result) == 1
    assert result[0]["session_id"] == "s1"


def test_find_prior_runs_filters_mismatch(tmp_path):
    from autoskillit.recipe.identity import find_prior_runs

    jsonl = tmp_path / "sessions.jsonl"
    e1 = {
        "recipe_name": "impl",
        "recipe_composite_hash": "sha256:abc",
        "timestamp": "T1",
        "session_id": "s1",
    }
    e2 = {
        "recipe_name": "impl",
        "recipe_composite_hash": "sha256:xyz",
        "timestamp": "T2",
        "session_id": "s2",
    }
    jsonl.write_text(json.dumps(e1) + "\n" + json.dumps(e2) + "\n")
    result = find_prior_runs(jsonl, composite_hash="sha256:abc")
    assert len(result) == 1


def test_find_prior_runs_by_name(tmp_path):
    from autoskillit.recipe.identity import find_prior_runs

    jsonl = tmp_path / "sessions.jsonl"
    e1 = {
        "recipe_name": "impl",
        "recipe_composite_hash": "sha256:abc",
        "timestamp": "T1",
    }
    e2 = {
        "recipe_name": "research",
        "recipe_composite_hash": "sha256:def",
        "timestamp": "T2",
    }
    jsonl.write_text(json.dumps(e1) + "\n" + json.dumps(e2) + "\n")
    result = find_prior_runs(jsonl, recipe_name="impl")
    assert len(result) == 1


def test_find_prior_runs_sorted_descending(tmp_path):
    from autoskillit.recipe.identity import find_prior_runs

    jsonl = tmp_path / "sessions.jsonl"
    e1 = {
        "recipe_name": "impl",
        "recipe_composite_hash": "sha256:abc",
        "timestamp": "2026-04-01",
    }
    e2 = {
        "recipe_name": "impl",
        "recipe_composite_hash": "sha256:abc",
        "timestamp": "2026-04-10",
    }
    jsonl.write_text(json.dumps(e1) + "\n" + json.dumps(e2) + "\n")
    result = find_prior_runs(jsonl, composite_hash="sha256:abc")
    assert result[0]["timestamp"] == "2026-04-10"


def test_find_prior_runs_skips_malformed(tmp_path):
    from autoskillit.recipe.identity import find_prior_runs

    jsonl = tmp_path / "sessions.jsonl"
    jsonl.write_text(
        "not json\n"
        + json.dumps(
            {
                "recipe_name": "impl",
                "recipe_composite_hash": "sha256:abc",
                "timestamp": "T1",
            }
        )
        + "\n"
    )
    result = find_prior_runs(jsonl, composite_hash="sha256:abc")
    assert len(result) == 1


# ---------------------------------------------------------------------------
# Part B: Re-run detection tests
# ---------------------------------------------------------------------------


def test_check_rerun_no_prior(tmp_path):
    from autoskillit.recipe.identity import check_rerun_detection

    result = check_rerun_detection(tmp_path / "sessions.jsonl", composite_hash="sha256:new")
    assert result is None


def test_check_rerun_found(tmp_path):
    from autoskillit.recipe.identity import check_rerun_detection

    jsonl = tmp_path / "sessions.jsonl"
    entry = {
        "recipe_composite_hash": "sha256:abc",
        "timestamp": "2026-04-10",
        "session_id": "s1",
    }
    jsonl.write_text(json.dumps(entry) + "\n")
    result = check_rerun_detection(jsonl, composite_hash="sha256:abc")
    assert result is not None
    assert result["rule"] == "duplicate-run-detected"
    assert "2026-04-10" in result["message"]
