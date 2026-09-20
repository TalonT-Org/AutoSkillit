"""Callable contracts declare their public success and error result keys."""

from __future__ import annotations

import ast
import importlib
import inspect
import textwrap
from pathlib import Path

import pytest

from autoskillit.core.io import load_yaml

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

_CONTRACTS = Path(__file__).resolve().parents[2] / "src/autoskillit/recipe/skill_contracts.yaml"


def _literal_return_keys(function: object) -> set[str]:
    tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
    root = next(
        node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    )
    keys: set[str] = set()

    class Returns(ast.NodeVisitor):
        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            if node is root:
                self.generic_visit(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            if node is root:
                self.generic_visit(node)

        def visit_Return(self, node: ast.Return) -> None:
            if isinstance(node.value, ast.Dict):
                keys.update(
                    key.value
                    for key in node.value.keys
                    if isinstance(key, ast.Constant) and isinstance(key.value, str)
                )

    Returns().visit(root)
    return keys


def test_callable_contracts_cover_literal_result_keys() -> None:
    contracts = load_yaml(_CONTRACTS.read_text(encoding="utf-8"))["callable_contracts"]
    errors: list[str] = []
    for path, contract in contracts.items():
        declared = {item["name"] for item in contract.get("outputs", [])}
        if not declared:
            errors.append(f"{path}: no outputs declared")
            continue
        module_name, function_name = path.rsplit(".", 1)
        function = getattr(importlib.import_module(module_name), function_name)
        missing = _literal_return_keys(function) - declared
        if missing:
            errors.append(f"{path}: undeclared result keys {sorted(missing)}")
    assert errors == []
