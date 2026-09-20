"""Automation may read the human waiver ledger but may not write it."""

from __future__ import annotations

import ast

import pytest

from tests.arch._helpers import SRC_ROOT

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_LEDGER_NAME = "audit-findings.yaml"
_WRITE_CALLS = {
    "write_text",
    "write_bytes",
    "atomic_write",
    "write_canonical_versioned_json",
    "write_canonical_json",
}


def _writer_sites(source: str) -> list[int]:
    tree = ast.parse(source)
    ledger_names = {
        target.id
        for node in ast.walk(tree)
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        and node.value is not None
        and _LEDGER_NAME in ast.unparse(node.value)
        for target in (node.targets if isinstance(node, ast.Assign) else (node.target,))
        if isinstance(target, ast.Name)
    }
    sites: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        name = (
            function.attr
            if isinstance(function, ast.Attribute)
            else (function.id if isinstance(function, ast.Name) else "")
        )
        is_write = name in _WRITE_CALLS or (
            name in {"open", "fdopen"}
            and any(
                isinstance(arg, ast.Constant)
                and isinstance(arg.value, str)
                and any(mode in arg.value for mode in ("w", "a", "x", "+"))
                for arg in (
                    *node.args[1:],
                    *(kw.value for kw in node.keywords if kw.arg == "mode"),
                )
            )
        )
        if not is_write:
            continue
        call_text = ast.unparse(node)
        if _LEDGER_NAME in call_text or any(
            isinstance(child, ast.Name) and child.id in ledger_names for child in ast.walk(node)
        ):
            sites.append(node.lineno)
    return sites


def test_production_has_no_waiver_ledger_writer() -> None:
    violations = {
        path.relative_to(SRC_ROOT).as_posix(): sites
        for path in SRC_ROOT.rglob("*.py")
        if (sites := _writer_sites(path.read_text(encoding="utf-8")))
    }
    assert violations == {}


@pytest.mark.parametrize(
    "source",
    [
        'Path(".autoskillit/waivers/audit-findings.yaml").write_text("waived")',
        'ledger = Path("audit-findings.yaml")\natomic_write(ledger, "waived")',
    ],
)
def test_writer_guard_detects_direct_and_persistence_writes(source: str) -> None:
    assert _writer_sites(source)
