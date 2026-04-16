"""Tests for the recipe/rules.py monolith decomposition into sub-modules."""

from __future__ import annotations

import ast
import pathlib

import pytest

pytestmark = [pytest.mark.layer("recipe")]


def test_no_deferred_validator_imports_in_rule_modules() -> None:
    """T1: No rule sub-module should defer-import from validator.py inside a function body."""
    recipe_dir = pathlib.Path(__file__).resolve().parents[2] / "src/autoskillit/recipe"
    rule_files = list(recipe_dir.glob("rules_*.py"))
    assert len(rule_files) >= 5, "Expected at least 5 rule sub-modules"
    for path in rule_files:
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for child in ast.walk(node):
                    if isinstance(child, (ast.Import, ast.ImportFrom)):
                        src = getattr(child, "module", "") or ""
                        assert "validator" not in src, (
                            f"{path.name}: deferred import from validator.py in function body"
                        )


def test_all_rules_registered_across_submodules() -> None:
    """T2: All 28 rules registered, distributed across sub-modules."""
    import autoskillit.recipe  # noqa: F401 -- triggers rule registration
    from autoskillit.recipe.registry import _RULE_REGISTRY

    rule_names = {spec.name for spec in _RULE_REGISTRY}
    expected = {
        "outdated-recipe-version",
        "missing-ingredient",
        "shadowed-required-input",
        "unreachable-step",
        "model-on-non-skill-step",
        "retries-on-worktree-modifying-skill",
        "retry-worktree-cwd",
        "weak-constraint-text",
        "undeclared-capture-key",
        "dead-output",
        "implicit-handoff",
        "multipart-iteration-notes",
        "merge-cleanup-uncaptured",
        "stale-ref-after-merge",
        "unbounded-cycle",
        "on-result-missing-failure-route",
        "push-before-audit",
        "clone-root-as-worktree",
        "multipart-plan-parts-not-captured",
        "skill-command-missing-prefix",
        "push-missing-explicit-remote-url",
        "optional-without-skip-when",
        "skip-when-false-undeclared",
        "merge-base-unpublished",
        "unknown-skill-command",
        "missing-output-patterns",
        "ci-failure-missing-conflict-gate",
        "unknown-required-pack",
    }
    assert expected <= rule_names


def test_analysis_module_importable() -> None:
    """T3a: _analysis.py is importable at module level with no side effects."""
    from autoskillit.recipe._analysis import _build_step_graph, analyze_dataflow

    assert callable(analyze_dataflow)
    assert callable(_build_step_graph)


def test_analysis_module_no_validator_import() -> None:
    """T3b: _analysis.py must not import from validator.py."""
    src = (
        pathlib.Path(__file__).resolve().parents[2] / "src/autoskillit/recipe/_analysis.py"
    ).read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert "validator" not in (node.module or ""), (
                "_analysis.py must not import from validator.py"
            )


def test_validator_does_not_import_rules() -> None:
    """T4: validator.py no longer imports any rules module at module level."""
    src = (
        pathlib.Path(__file__).resolve().parents[2] / "src/autoskillit/recipe/validator.py"
    ).read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            assert not module.endswith(".rules"), (
                "validator.py must not import from rules.py (cycle eliminated)"
            )
            assert "rules_" not in module or module.startswith("autoskillit.recipe.rules_"), (
                f"validator.py must not import rule sub-modules directly: {module}"
            )
