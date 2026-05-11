"""Regression guard: Path.cwd() must not appear in server tool handlers.

This test prevents reintroduction of Path.cwd() call sites that were migrated
to use tool_ctx.project_dir in the fleet project_dir propagation fix (Part B).

The one allowed site (_reload_session_handler in tools_kitchen.py) is excluded
because it uses cwd to find the server's own log directory, not the project root.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]

# Files to check and their allowed Path.cwd() sites
TOOLS_FILES = {
    "src/autoskillit/server/tools/tools_kitchen.py": [
        600,  # _reload_session_handler — correct: server's own cwd for log discovery
    ],
    "src/autoskillit/server/tools/tools_recipe.py": [],
    "src/autoskillit/server/tools/tools_issue_lifecycle.py": [],
}

LIFESPAN_FILES = {
    "src/autoskillit/server/_lifespan.py": [],
}

ALL_FILES = {**TOOLS_FILES, **LIFESPAN_FILES}


def _find_path_cwd_lines(filepath: Path) -> list[int]:
    """Return line numbers where Path.cwd() appears in the file."""
    try:
        source = filepath.read_text()
    except OSError:
        return []

    lines_with_cwd = []
    for i, line in enumerate(source.splitlines(), start=1):
        if "Path.cwd()" in line:
            lines_with_cwd.append(i)

    return lines_with_cwd


def _get_allowlisted_lines(filepath: str) -> list[int]:
    """Get the allowlisted line numbers for a given file."""
    return ALL_FILES.get(filepath, [])


def test_no_path_cwd_in_server_tools():
    """Assert Path.cwd() does not appear in server tool handlers.

    This is a latch test: once the migration removes all Path.cwd() sites,
    this test prevents them from being reintroduced.
    """
    import autoskillit

    pkg_root = Path(autoskillit.__file__).parent.parent.parent
    missing_files = []
    violations = []

    for rel_path in ALL_FILES:
        filepath = pkg_root / rel_path
        if not filepath.exists():
            missing_files.append(rel_path)
            continue

        lines_with_cwd = _find_path_cwd_lines(filepath)
        allowlisted = _get_allowlisted_lines(rel_path)

        for lineno in lines_with_cwd:
            if lineno not in allowlisted:
                violations.append(f"{rel_path}:{lineno}: Path.cwd() found (not allowlisted)")

    assert not missing_files, "Server tool files not found:\n" + "\n".join(missing_files)
    assert not violations, "Path.cwd() found in server tool handlers:\n" + "\n".join(violations)
