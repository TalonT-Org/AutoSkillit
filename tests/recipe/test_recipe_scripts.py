"""Tests for scripts/recipe/ — externalized shell scripts."""

from __future__ import annotations

import os
import subprocess

import pytest

from autoskillit.core.paths import pkg_root
from autoskillit.recipe.io import builtin_recipes_dir, builtin_scripts_dir, load_recipe
from autoskillit.recipe.validator import run_semantic_rules

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.medium]

SCRIPTS_DIR = builtin_scripts_dir()


def test_bundled_scripts_exist_within_package_boundary():
    """All scripts referenced in bundled recipe cmd fields must live inside pkg_root()."""
    scripts_dir = builtin_scripts_dir()
    assert scripts_dir.exists(), f"builtin_scripts_dir does not exist: {scripts_dir}"
    scripts = list(scripts_dir.glob("*.sh"))
    assert scripts, f"No .sh scripts found in {scripts_dir}"
    for sh in scripts:
        assert str(sh).startswith(str(pkg_root())), (
            f"Script {sh} is outside package boundary {pkg_root()}"
        )


def test_scripts_dir_does_not_traverse_above_pkg_root():
    """builtin_scripts_dir() must resolve within pkg_root(), not traverse above it."""
    scripts_dir = builtin_scripts_dir()
    pkg = pkg_root()
    assert scripts_dir.is_relative_to(pkg), (
        f"builtin_scripts_dir() ({scripts_dir}) is not under pkg_root() ({pkg})"
    )


def test_autoskillit_scripts_placeholder_substituted_at_load_time():
    """Loading a recipe must resolve {{AUTOSKILLIT_SCRIPTS}} to an absolute path."""
    recipe = load_recipe(builtin_recipes_dir() / "research.yaml")
    for name, step in recipe.steps.items():
        if step.tool != "run_cmd":
            continue
        cmd = (step.with_args or {}).get("cmd", "")
        assert "{{AUTOSKILLIT_SCRIPTS}}" not in cmd, (
            f"Step '{name}' has unresolved {{{{AUTOSKILLIT_SCRIPTS}}}} in cmd"
        )
        if "scripts/" in cmd and ".sh" in cmd:
            assert cmd.strip().startswith("bash /"), (
                f"Step '{name}' script path not resolved to absolute: {cmd[:80]}"
            )


@pytest.mark.parametrize(
    "recipe_name",
    ["research", "research-design", "research-implement", "research-review", "research-archive"],
)
def test_no_recipe_uses_dev_tree_script_paths(recipe_name):
    """Loaded bundled recipes must not contain relative scripts/recipe/ paths."""
    recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
    for name, step in recipe.steps.items():
        if step.tool != "run_cmd":
            continue
        cmd = (step.with_args or {}).get("cmd", "")
        assert "scripts/recipe/" not in cmd, (
            f"Step '{name}' in {recipe_name} still uses dev-tree path: {cmd[:80]}"
        )


def test_recipe_scripts_are_executable():
    scripts = list(SCRIPTS_DIR.glob("*.sh"))
    assert scripts, f"No .sh scripts found in {SCRIPTS_DIR}"
    for sh in scripts:
        assert os.access(sh, os.X_OK), f"{sh} is not executable"


def test_recipe_scripts_pass_syntax_check():
    scripts = list(SCRIPTS_DIR.glob("*.sh"))
    assert scripts, f"No .sh scripts found in {SCRIPTS_DIR}"
    for sh in scripts:
        result = subprocess.run(["bash", "-n", str(sh)], capture_output=True)
        assert result.returncode == 0, f"{sh} has syntax errors: {result.stderr.decode()}"


@pytest.mark.parametrize(
    "recipe_name",
    [
        "implementation",
        "remediation",
        "implementation-groups",
        "merge-prs",
        "research",
        "bem-wrapper",
    ],
)
def test_recipes_pass_inline_script_rule(recipe_name):
    recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
    findings = run_semantic_rules(recipe)
    inline_findings = [
        f for f in findings if f.rule in ("inline-script-in-cmd", "inline-python-in-cmd")
    ]
    assert inline_findings == [], (
        f"Recipe {recipe_name} has inline script findings: {inline_findings}"
    )
