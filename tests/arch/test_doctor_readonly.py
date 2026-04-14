"""AST guard: run_doctor() must not perform filesystem mutations (REQ-DOCTOR-READONLY).

Doctor is a diagnostic command — it reads and reports but must never modify
the filesystem. Any destructive call (shutil.rmtree, os.remove, _clear_plugin_cache, …)
in run_doctor() or its direct callees within _doctor.py is a structural violation.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "autoskillit"

FORBIDDEN_WRITE_CALLS = frozenset(
    {
        "shutil.rmtree",
        "os.remove",
        "os.unlink",
        "Path.unlink",
        "Path.rmdir",
        "_clear_plugin_cache",
    }
)


def _get_call_name(node: ast.Call) -> str:
    """Extract dotted call name from an ast.Call node."""
    if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
        return f"{node.func.value.id}.{node.func.attr}"
    if isinstance(node.func, ast.Name):
        return node.func.id
    return ""


def _find_function(tree: ast.Module, name: str) -> ast.FunctionDef | None:
    """Find a top-level FunctionDef by name."""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def test_doctor_performs_no_writes() -> None:
    """REQ-DOCTOR-READONLY: run_doctor() must not perform filesystem mutations."""
    source = (SRC / "cli" / "_doctor.py").read_text()
    tree = ast.parse(source)

    func = _find_function(tree, "run_doctor")
    assert func is not None, "run_doctor() not found in _doctor.py"

    violations: list[str] = []
    for node in ast.walk(func):
        if isinstance(node, ast.Call):
            call_name = _get_call_name(node)
            if call_name in FORBIDDEN_WRITE_CALLS:
                violations.append(f"{call_name} at line {node.lineno}")

    assert not violations, (
        "run_doctor() must be read-only — found forbidden write call(s):\n"
        + "\n".join(f"  {v}" for v in violations)
    )
