"""Contract tests for the two apply_manifest implementations: tests/_test_filter.py and
src/autoskillit/_test_filter.py.

The shared contract is checked per module, not by comparing the two signatures: each must
return ``set[str] | None`` and accept a ``manifest`` parameter. The tests-side implementation
additionally exposes the keyword-only ``compiled_matchers`` reuse argument that
``build_test_scope`` uses to compile each manifest pattern once per invocation.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

import autoskillit._test_filter as src_filter
import tests._test_filter as conftest_filter

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


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
    """Checks each apply_manifest implementation against the shared contract.

    Each module is inspected independently: both must return ``set[str] | None`` and accept
    a ``manifest`` parameter. The tests-side extension for matcher reuse is checked separately;
    the two signatures are not required to be identical.
    """

    def test_both_return_optional_set(self) -> None:
        """Both apply_manifest implementations must return set[str] | None."""
        src_ann = _get_return_annotation(src_filter, "apply_manifest")
        conftest_ann = _get_return_annotation(conftest_filter, "apply_manifest")
        assert src_ann in ("set[str] | None", "Optional[set[str]]"), (
            f"src apply_manifest unexpected return annotation: {src_ann!r}"
        )
        assert conftest_ann in ("set[str] | None", "Optional[set[str]]"), (
            f"conftest apply_manifest unexpected return annotation: {conftest_ann!r}\n"
            "The conftest module must return None (not empty set) to signal fail-open."
        )

    def test_both_accept_manifest_parameter(self) -> None:
        """Both apply_manifest implementations must accept a manifest parameter."""
        src_sig = inspect.signature(src_filter.apply_manifest)
        conftest_sig = inspect.signature(conftest_filter.apply_manifest)
        assert "manifest" in src_sig.parameters
        assert "manifest" in conftest_sig.parameters

    def test_conftest_exposes_keyword_only_compiled_matchers(self) -> None:
        """The tests-side apply_manifest must expose the matcher-reuse argument."""
        conftest_sig = inspect.signature(conftest_filter.apply_manifest)
        param = conftest_sig.parameters.get("compiled_matchers")
        assert param is not None, (
            "tests/_test_filter.py apply_manifest must accept compiled_matchers so "
            "build_test_scope can compile each manifest pattern once per invocation"
        )
        assert param.kind is inspect.Parameter.KEYWORD_ONLY, (
            f"compiled_matchers must be keyword-only, got {param.kind}"
        )
        assert param.default is None, (
            f"compiled_matchers must default to None, got {param.default!r}"
        )
