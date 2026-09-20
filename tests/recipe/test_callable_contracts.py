from __future__ import annotations

import ast
import importlib
import inspect
import textwrap

import pytest

from autoskillit.recipe.contracts import load_bundled_manifest

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def test_all_callable_contracts_declare_inputs():
    """Every callable_contract entry must declare inputs matching the function signature."""
    manifest = load_bundled_manifest()
    callables = manifest.get("callable_contracts", {})
    for dotted_path, entry in callables.items():
        if "." not in dotted_path:
            pytest.fail(f"{dotted_path}: not a dotted module path")
        module_path, attr_name = dotted_path.rsplit(".", 1)
        mod = importlib.import_module(module_path)
        func = getattr(mod, attr_name)
        sig = inspect.signature(func)
        required_params = [
            name
            for name, p in sig.parameters.items()
            if p.default is inspect.Parameter.empty
            and p.kind not in (inspect.Parameter.VAR_KEYWORD, inspect.Parameter.VAR_POSITIONAL)
        ]
        declared_inputs = [inp["name"] for inp in entry.get("inputs", [])]
        for param in required_params:
            assert param in declared_inputs, (
                f"{dotted_path}: required parameter '{param}' not declared in "
                f"callable_contracts inputs"
            )


def test_review_path_rebase_contract_inputs_and_outputs():
    """review_path_rebase callable contract must declare correct inputs and outputs."""
    from autoskillit.recipe.contracts import get_callable_contract

    contract = get_callable_contract("autoskillit.recipe._cmd_rpc.review_path_rebase")
    assert contract is not None, "review_path_rebase must be declared in callable_contracts"
    input_names = {inp.name for inp in contract.inputs}
    assert "work_dir" in input_names
    assert "base_branch" in input_names
    for inp in contract.inputs:
        if inp.name in ("work_dir", "base_branch"):
            assert inp.required is True, f"{inp.name} must be required"
    output_names = {out.name for out in contract.outputs}
    assert "status" in output_names


def test_extract_investigation_callable_contract():
    """extract_investigation must be declared in callable_contracts."""
    from autoskillit.recipe.contracts import get_callable_contract

    contract = get_callable_contract("autoskillit.smoke_utils.extract_investigation")
    assert contract is not None, "extract_investigation must be declared in callable_contracts"
    input_names = {inp.name for inp in contract.inputs}
    assert "investigation_path" in input_names
    assert "issue_number" in input_names
    assert "output_dir" in input_names
    output_names = {out.name for out in contract.outputs}
    assert "investigation_report" in output_names


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
    contracts = load_bundled_manifest()["callable_contracts"]
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
