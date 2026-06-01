"""Arch test: extract_redirect_targets must use resolve_write_target, not inline startswith."""

import ast

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


def test_extract_redirect_targets_uses_resolve_write_target():
    import autoskillit.hooks._command_classification as mod

    source = ast.parse(open(mod.__file__).read())
    for node in ast.walk(source):
        if isinstance(node, ast.FunctionDef) and node.name == "extract_redirect_targets":
            body_src = ast.dump(node)
            assert "startswith" not in body_src, (
                "extract_redirect_targets must use resolve_write_target() "
                "instead of inline path.startswith checks"
            )
            break
    else:
        pytest.fail("extract_redirect_targets function not found")
