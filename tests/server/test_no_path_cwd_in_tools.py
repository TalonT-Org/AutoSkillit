"""Regression guard: Path.cwd() must not appear in server tool handlers.

This test prevents reintroduction of Path.cwd() call sites that were migrated
to use tool_ctx.project_dir in the fleet project_dir propagation fix (Part B).

The one allowed site (_reload_session_handler in tools_kitchen.py) is excluded
because it uses cwd to find the server's own log directory, not the project root.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]

# Files to check and their allowed Path.cwd() sites
TOOLS_FILES = {
    "src/autoskillit/server/tools/tools_kitchen.py": [
        # Line 597 in _reload_session_handler — correct: server's own cwd for log discovery
    ],
    "src/autoskillit/server/tools/tools_recipe.py": [],
    "src/autoskillit/server/tools/tools_issue_lifecycle.py": [],
}

LIFESPAN_FILES = {
    "src/autoskillit/server/_lifespan.py": [],
}

ALL_FILES = {**TOOLS_FILES, **LIFESPAN_FILES}

# _factory.py contains Path.cwd() as the last-resort fallback in _resolve_project_dir.
# This is the resolver itself and is correct — not a tool handler.
ALLOWED_FILE_PATTERNS = ["_factory.py"]


class PathCwdVisitor(ast.NodeVisitor):
    """Find all Path.cwd() calls in a file."""

    def __init__(self, filename: str):
        self.filename = filename
        self.calls: list[tuple[int, str]] = []  # (lineno, line text)

    def visit_Call(self, node: ast.Call) -> None:
        # Check for Path.cwd()
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "cwd"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "Path"
        ):
            self.calls.append((node.lineno, ""))
        self.generic_visit(node)


def _find_path_cwd_lines(filepath: Path) -> list[int]:
    """Return line numbers where Path.cwd() appears in the file."""
    try:
        source = filepath.read_text()
    except OSError:
        return []

    # Quick string check first
    lines_with_cwd = []
    for i, line in enumerate(source.splitlines(), start=1):
        if "Path.cwd()" in line:
            lines_with_cwd.append(i)

    return lines_with_cwd


def _get_allowlisted_lines(filepath: str) -> list[int]:
    """Get the allowlisted line numbers for a given file."""
    filename = Path(filepath).name
    return ALL_FILES.get(filepath, ALL_FILES.get(filename, []))


def test_no_path_cwd_in_server_tools():
    """Assert Path.cwd() does not appear in server tool handlers.

    This is a latch test: once the migration removes all Path.cwd() sites,
    this test prevents them from being reintroduced.
    """
    import autoskillit

    pkg_root = Path(autoskillit.__file__).parent.parent
    violations = []

    for rel_path in ALL_FILES:
        filepath = pkg_root / rel_path
        if not filepath.exists():
            violations.append(f"{rel_path}: FILE NOT FOUND")
            continue

        lines_with_cwd = _find_path_cwd_lines(filepath)
        allowlisted = _get_allowlisted_lines(rel_path)

        for lineno in lines_with_cwd:
            if lineno not in allowlisted:
                violations.append(f"{rel_path}:{lineno}: Path.cwd() found (not allowlisted)")

    assert not violations, "Path.cwd() found in server tool handlers:\n" + "\n".join(violations)
