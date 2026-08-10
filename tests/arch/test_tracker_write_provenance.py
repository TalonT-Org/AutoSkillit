"""AST guard: hook scripts must never mutate pipeline tracker files.

Step completion is server-authoritative — only server/tools/ code may mutate
tracker state. Any hook-side write reintroduces the split-brain bug (#4293).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

HOOKS_DIR = Path(__file__).resolve().parents[2] / "src" / "autoskillit" / "hooks"
_PATH_MUTATION_METHODS = {"write_text", "write_bytes", "replace", "unlink", "rmdir"}
_QUALIFIED_MUTATION_CALLS = {
    ("os", "remove"),
    ("os", "replace"),
    ("os", "rmdir"),
    ("os", "write"),
    ("shutil", "rmtree"),
}


def _has_tracker_reference(tree: ast.Module) -> bool:
    """Check if the module references the pipeline_tracker path segment."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if "pipeline_tracker" in node.value:
                return True
    return False


def _looks_path_like(node: ast.expr) -> bool:
    """Recognize the path-valued receiver shapes used by hook scripts."""
    if isinstance(node, ast.Name):
        name = node.id.lower()
        return name in {"path", "file", "directory"} or name.endswith(
            ("_path", "_file", "_dir", "_directory")
        )
    if isinstance(node, ast.Attribute):
        return _looks_path_like(ast.Name(id=node.attr)) or _looks_path_like(node.value)
    if isinstance(node, ast.Subscript):
        if _looks_path_like(node.value):
            return True
        if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
            return _looks_path_like(ast.Name(id=node.slice.value))
        return False
    if isinstance(node, ast.Call):
        func = node.func
        return (isinstance(func, ast.Name) and func.id == "Path") or (
            isinstance(func, ast.Attribute)
            and func.attr in {"joinpath", "resolve", "with_name", "with_suffix"}
        )
    return False


def _has_file_mutation(tree: ast.Module) -> list[int]:
    """Find lines with file-write or destructive operations."""
    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                qualified_call = (
                    (
                        node.func.value.id,
                        node.func.attr,
                    )
                    if isinstance(node.func.value, ast.Name)
                    else None
                )
                if qualified_call in _QUALIFIED_MUTATION_CALLS or (
                    node.func.attr in _PATH_MUTATION_METHODS and _looks_path_like(node.func.value)
                ):
                    violations.append(node.lineno)
            # Check calls to open() with write mode
            if isinstance(node.func, ast.Name) and node.func.id == "open":
                for arg in node.args[1:]:
                    if (
                        isinstance(arg, ast.Constant)
                        and isinstance(arg.value, str)
                        and "w" in arg.value
                    ):
                        violations.append(node.lineno)
                        break
    return violations


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ('value.replace("old", "new")', []),
        ('state.value.replace("old", "new")', []),
        ("state.output_path.replace(target)", [1]),
        ('artifacts["tracker_path"].unlink()', [1]),
        ('items.remove("value")', []),
        ("tracker_path.unlink()", [1]),
        ("os.replace(source, destination)", [1]),
        ('Path("tracker.json").write_text("{}")', [1]),
    ],
    ids=[
        "string-replace",
        "nested-string-replace",
        "attribute-path-replace",
        "subscript-path-unlink",
        "list-remove",
        "path-unlink",
        "os-replace",
        "path-write",
    ],
)
def test_file_mutation_detection_qualifies_receivers(source: str, expected: list[int]) -> None:
    assert _has_file_mutation(ast.parse(source)) == expected


def test_hooks_never_write_pipeline_tracker_files():
    """No hook script may both reference pipeline_tracker and perform file writes."""
    violating_files = []
    for py_file in sorted(HOOKS_DIR.rglob("*.py")):
        if py_file.name.startswith("_") and py_file.name != "_dispatch.py":
            continue
        try:
            tree = ast.parse(py_file.read_text())
        except SyntaxError:
            continue
        if _has_tracker_reference(tree):
            mutation_lines = _has_file_mutation(tree)
            if mutation_lines:
                violating_files.append((py_file.relative_to(HOOKS_DIR), mutation_lines))

    assert violating_files == [], (
        f"Hook scripts must not mutate pipeline_tracker files (server-authoritative). "
        f"Violations: {violating_files}"
    )
