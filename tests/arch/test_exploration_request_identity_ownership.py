from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_ROOT = Path(__file__).resolve().parents[2]


def test_lifespan_boot_never_reads_request_identity_from_tool_context() -> None:
    _LIFESPAN_PKG = _ROOT / "src" / "autoskillit" / "server" / "_lifespan"
    source = ""
    for py in sorted(_LIFESPAN_PKG.rglob("*.py")):
        source += py.read_text()
    module = ast.parse(source)

    direct_reads = [
        node
        for node in ast.walk(module)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "ctx"
        and node.attr == "session_id"
    ]
    getattr_reads = [
        node
        for node in ast.walk(module)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "getattr"
        and len(node.args) >= 2
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id == "ctx"
        and isinstance(node.args[1], ast.Constant)
        and node.args[1].value == "session_id"
    ]

    assert not direct_reads
    assert not getattr_reads
