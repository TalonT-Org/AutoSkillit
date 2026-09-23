"""AST guard: diagnostic doctor remains read-only (REQ-DOCTOR-READONLY).

``run_doctor`` performs no durable mutation. Its one permitted write surface is
disposable managed-Codex probe scratch beneath the configured project temp root.
Safe repair is a distinct, opt-in entry point selected only by the CLI's
``repair=True`` branch.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "autoskillit"

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

FORBIDDEN_WRITE_CALLS = frozenset(
    {
        "shutil.rmtree",
        "os.remove",
        "os.unlink",
        "Path.unlink",
        "Path.rmdir",
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
    """REQ-DOCTOR-READONLY: run_doctor() must not directly mutate the filesystem."""
    source = (SRC / "cli" / "doctor" / "__init__.py").read_text()
    tree = ast.parse(source)

    func = _find_function(tree, "run_doctor")
    assert func is not None, "run_doctor() not found in cli/doctor/__init__.py"

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


def test_managed_preparation_probe_scratch_is_project_temp_scoped() -> None:
    """The diagnostic write exception is limited to disposable project-temp scratch."""
    source = (SRC / "cli" / "doctor" / "_doctor_config.py").read_text()
    tree = ast.parse(source)
    func = _find_function(tree, "_check_codex_managed_preparation")
    assert func is not None

    scratch_assignments = [
        node
        for node in ast.walk(func)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "scratch_root" for target in node.targets
        )
    ]
    assert len(scratch_assignments) == 1
    assert ast.unparse(scratch_assignments[0].value) == (
        "resolve_temp_dir(project_dir, workspace_temp_dir) / 'doctor-managed-codex-preparation'"
    )
    preparation_calls = [
        node
        for node in ast.walk(func)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "prepare_managed_codex_catalog"
    ]
    assert len(preparation_calls) == 1
    scratch_keywords = [
        keyword.value for keyword in preparation_calls[0].keywords if keyword.arg == "scratch_root"
    ]
    assert len(scratch_keywords) == 1
    assert isinstance(scratch_keywords[0], ast.Name)
    assert scratch_keywords[0].id == "scratch_root"


def test_repair_entry_point_is_only_reachable_with_the_flag() -> None:
    source = (SRC / "cli" / "app.py").read_text()
    tree = ast.parse(source)
    doctor = _find_function(tree, "doctor")
    assert doctor is not None

    repair_imports = [
        node
        for node in ast.walk(doctor)
        if isinstance(node, ast.ImportFrom)
        and any(alias.name == "run_doctor_repairs" for alias in node.names)
    ]
    assert len(repair_imports) == 1
    assert any(
        isinstance(parent, ast.If)
        and isinstance(parent.test, ast.Name)
        and parent.test.id == "repair"
        and repair_imports[0] in ast.walk(parent)
        for parent in ast.walk(doctor)
    )
