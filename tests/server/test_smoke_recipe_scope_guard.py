"""Structural guard: smoke_recipe fixture must not use scope='module'."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def test_smoke_recipe_fixture_is_not_module_scoped():
    """smoke_recipe must not use scope='module'; Recipe is a mutable dataclass."""
    src = (Path(__file__).parent / "test_smoke_pipeline.py").read_text()
    tree = ast.parse(src)
    found_smoke_recipe = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name != "smoke_recipe":
            continue
        found_smoke_recipe = True
        for decorator in node.decorator_list:
            if not (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr == "fixture"
            ):
                continue
            for kw in decorator.keywords:
                if kw.arg == "scope":
                    assert not (
                        isinstance(kw.value, ast.Constant) and kw.value.value == "module"
                    ), "smoke_recipe must not use scope='module' — Recipe is mutable"
    assert found_smoke_recipe, "smoke_recipe fixture not found in test_smoke_pipeline.py"
