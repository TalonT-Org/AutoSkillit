"""AST guard: hook scripts must never write pipeline tracker files.

Step completion is server-authoritative — only server/tools/ code may mutate
tracker state. Any hook-side write reintroduces the split-brain bug (#4293).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

HOOKS_DIR = Path(__file__).resolve().parents[2] / "src" / "autoskillit" / "hooks"

# Patterns indicating a file write operation
_WRITE_FUNC_NAMES = frozenset(
    {"write_text", "open", "atomic_write", "_atomic_write", "os.replace"}
)
_TRACKER_PATH_SEGMENTS = frozenset({"pipeline_tracker"})


def _has_tracker_reference(tree: ast.Module) -> bool:
    """Check if the module references the pipeline_tracker path segment."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if "pipeline_tracker" in node.value:
                return True
    return False


def _has_file_write(tree: ast.Module) -> list[int]:
    """Find lines with file-write operations."""
    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            # Check method calls like path.write_text(), open(..., "w"), os.replace()
            if isinstance(node.func, ast.Attribute):
                if node.func.attr in ("write_text", "replace"):
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
            # Check os.write(fd, ...) — the retired hook's raw fd-write pattern.
            # Deliberately narrower than "any .write attribute call" to avoid
            # false positives on sys.stdout.write()/logger writes/etc.
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "write"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "os"
            ):
                violations.append(node.lineno)
    return violations


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
            write_lines = _has_file_write(tree)
            if write_lines:
                violating_files.append((py_file.relative_to(HOOKS_DIR), write_lines))

    assert violating_files == [], (
        f"Hook scripts must not write pipeline_tracker files (server-authoritative). "
        f"Violations: {violating_files}"
    )
