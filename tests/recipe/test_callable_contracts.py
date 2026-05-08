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
