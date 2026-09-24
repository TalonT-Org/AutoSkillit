"""Arch test: the write-target scan must not bypass shared path resolution."""

import ast

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


def _has_path_startswith_slash(func_node: ast.FunctionDef) -> bool:
    """Detect inline ``<expr>.startswith("/")`` inside a function body."""
    for node in ast.walk(func_node):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "startswith"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "/"
        ):
            return True
    return False


def test_scan_write_targets_uses_resolve_write_target():
    import autoskillit.hooks._runtime._command_classification as mod

    with open(mod.__file__) as f:
        source = ast.parse(f.read())
    for node in ast.walk(source):
        if isinstance(node, ast.FunctionDef) and node.name == "scan_write_targets":
            assert not _has_path_startswith_slash(node), (
                "scan_write_targets must use resolve_write_target() "
                'instead of inline path.startswith("/") checks'
            )
            break
    else:
        pytest.fail("scan_write_targets function not found")
