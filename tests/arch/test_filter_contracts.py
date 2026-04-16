"""AST-based contract test enforcing signature compatibility between the two apply_manifest
implementations: tests/_test_filter.py and src/autoskillit/_test_filter.py.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import autoskillit._test_filter as src_filter
import tests._test_filter as conftest_filter


def _get_return_annotation(module: object, func_name: str) -> str:
    """Return the string representation of func_name's return annotation in module."""
    source = Path(inspect.getfile(module)).read_text()  # type: ignore[arg-type]
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            if node.returns is not None:
                return ast.unparse(node.returns)
    return ""


class TestApplyManifestSignatureContract:
    """Enforces structural compatibility between the two apply_manifest implementations.

    When the production module's apply_manifest signature changes, this test fails
    immediately, forcing the conftest-side implementation to be updated in sync.
    """

    def test_both_return_optional_set(self) -> None:
        """Both apply_manifest implementations must return set[str] | None."""
        src_ann = _get_return_annotation(src_filter, "apply_manifest")
        conftest_ann = _get_return_annotation(conftest_filter, "apply_manifest")
        assert "None" in src_ann, f"src apply_manifest missing None return: {src_ann!r}"
        assert "None" in conftest_ann, (
            f"conftest apply_manifest missing None return: {conftest_ann!r}\n"
            "The conftest module must return None (not empty set) to signal fail-open."
        )

    def test_both_accept_manifest_parameter(self) -> None:
        """Both apply_manifest implementations must accept a manifest parameter."""
        src_sig = inspect.signature(src_filter.apply_manifest)
        conftest_sig = inspect.signature(conftest_filter.apply_manifest)
        assert "manifest" in src_sig.parameters
        assert "manifest" in conftest_sig.parameters
