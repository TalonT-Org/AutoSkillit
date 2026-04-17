"""Tests for recipe identity hashing — composite hash computation."""

from __future__ import annotations

import hashlib
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

    p1 = _write_recipe(tmp_path, "recipe1")
    r1 = load_recipe(p1)
    h1 = compute_composite_hash(p1, r1, skills_dir=skills_dir, project_dir=tmp_path)

    p2 = _write_recipe(tmp_path, "recipe2")
    r2 = load_recipe(p2)
    h2 = compute_composite_hash(p2, r2, skills_dir=skills_dir, project_dir=tmp_path)

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


def test_composite_hash_includes_domain_separator(tmp_path):
    from autoskillit.recipe.identity import compute_composite_hash
    from autoskillit.recipe.io import load_recipe

    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()

    p = _write_recipe(tmp_path)
    recipe = load_recipe(p)
    composite = compute_composite_hash(p, recipe, skills_dir=skills_dir, project_dir=tmp_path)
    naive = "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()

    assert composite != naive
