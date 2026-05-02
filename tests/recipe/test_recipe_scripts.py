"""Tests for scripts/recipe/ — externalized shell scripts."""

from __future__ import annotations

import os
import subprocess

import pytest

from autoskillit.core.paths import pkg_root
from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.rules_inline_script import _INLINE_SCRIPT_ALLOWLIST
from autoskillit.recipe.validator import run_semantic_rules

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

SCRIPTS_DIR = pkg_root().parent.parent / "scripts" / "recipe"


def test_recipe_scripts_are_executable():
    for sh in SCRIPTS_DIR.glob("*.sh"):
        assert os.access(sh, os.X_OK), f"{sh} is not executable"


def test_recipe_scripts_pass_syntax_check():
    for sh in SCRIPTS_DIR.glob("*.sh"):
        result = subprocess.run(["bash", "-n", str(sh)], capture_output=True)
        assert result.returncode == 0, f"{sh} has syntax errors: {result.stderr.decode()}"


def test_allowlist_is_empty():
    assert len(_INLINE_SCRIPT_ALLOWLIST) == 0, (
        f"Allowlist still has {len(_INLINE_SCRIPT_ALLOWLIST)} entries: {_INLINE_SCRIPT_ALLOWLIST}"
    )


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
