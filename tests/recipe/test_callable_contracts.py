from __future__ import annotations

import importlib
import inspect

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
