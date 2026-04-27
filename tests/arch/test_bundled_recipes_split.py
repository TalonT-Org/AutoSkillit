"""Enforcement: test_bundled_recipes.py split structure guard."""

from __future__ import annotations

import ast
import importlib
import inspect
from pathlib import Path

import pytest

_TRIPLICATED_TESTS = [
    "test_ci_step_structure",
    "test_re_push_has_explicit_remote_url",
    "test_compose_pr_routes_to_extract_pr_number",
    "test_detect_ci_conflict_exists",
    "test_detect_ci_conflict_uses_merge_base",
    "test_detect_ci_conflict_routing",
    "test_ci_conflict_fix_exists",
    "test_ci_conflict_fix_routing",
    "test_detect_ci_conflict_skip_when_false",
    "test_ci_conflict_fix_skip_when_false",
    "test_review_step_has_skip_when_false",
    "test_review_step_has_retries",
    "test_review_step_has_on_context_limit",
    "test_audit_impl_has_on_context_limit",
    "test_compose_pr_has_on_context_limit",
    "test_ci_conflict_fix_has_on_context_limit",
]


@pytest.mark.parametrize(
    "module",
    [
        "tests.recipe.test_bundled_recipes_general",
        "tests.recipe.test_bundled_recipes_pipeline_structure",
        "tests.recipe.test_bundled_recipes_review_pr",
        "tests.recipe.test_bundled_recipes_research",
    ],
)
def test_split_module_exists(module):
    importlib.import_module(module)


def test_original_file_deleted():
    assert not (
        Path(__file__).resolve().parent.parent / "recipe" / "test_bundled_recipes.py"
    ).exists(), "test_bundled_recipes.py must be deleted after split"


def test_pipeline_variant_invariants_parametrized():
    from tests.recipe import test_bundled_recipes_pipeline_structure as m

    src = inspect.getsource(m.TestPipelineVariantInvariants)
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "recipe":
            for deco in node.decorator_list:
                call = deco if isinstance(deco, ast.Call) else None
                if call:
                    for kw in call.keywords:
                        if kw.arg == "params":
                            names = [elt.s for elt in kw.value.elts]
                            assert set(names) == {
                                "implementation",
                                "implementation-groups",
                                "remediation",
                            }
                            return
    pytest.fail("TestPipelineVariantInvariants.recipe fixture not found or missing params")


def test_triplicated_tests_in_invariants_class():
    from tests.recipe import test_bundled_recipes_pipeline_structure as m

    invariant_methods = {
        n
        for n, _ in inspect.getmembers(
            m.TestPipelineVariantInvariants, predicate=inspect.isfunction
        )
    }
    for name in _TRIPLICATED_TESTS:
        assert name in invariant_methods, f"{name} must be in TestPipelineVariantInvariants"


def test_triplicated_tests_removed_from_variant_classes():
    from tests.recipe import test_bundled_recipes_pipeline_structure as m

    for cls in (
        m.TestImplementationPipelineStructure,
        m.TestImplementationGroupsStructure,
        m.TestInvestigateFirstStructure,
    ):
        methods = {n for n, _ in inspect.getmembers(cls, predicate=inspect.isfunction)}
        for name in _TRIPLICATED_TESTS:
            assert name not in methods, f"{name} must not remain in {cls.__name__}"
