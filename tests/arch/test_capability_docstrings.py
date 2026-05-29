"""Architectural invariant: BackendCapabilities must have class and field documentation."""

import ast

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


def test_backend_capabilities_has_class_docstring():
    """BackendCapabilities must have a class-level docstring."""
    from autoskillit.core import BackendCapabilities

    assert BackendCapabilities.__doc__ is not None, "BackendCapabilities lacks a class docstring"


def test_backend_capabilities_fields_documented():
    """Every BackendCapabilities field must have inline or preceding-line documentation."""
    from autoskillit.core import paths

    src_path = paths.pkg_root() / "core" / "types" / "_type_backend.py"
    lines = src_path.read_text().splitlines()
    tree = ast.parse(src_path.read_text())

    for cls_node in ast.walk(tree):
        if isinstance(cls_node, ast.ClassDef) and cls_node.name == "BackendCapabilities":
            for node in cls_node.body:
                if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                    field_line = lines[node.lineno - 1]
                    prev_line = lines[node.lineno - 2] if node.lineno >= 2 else ""
                    has_inline = "#" in field_line
                    has_preceding = prev_line.strip().startswith("#")
                    assert has_inline or has_preceding, (
                        f"Field {node.target.id!r} (line {node.lineno})"
                        " has no documentation comment"
                    )
