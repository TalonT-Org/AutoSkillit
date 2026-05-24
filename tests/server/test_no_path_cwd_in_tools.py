"""Regression guard: Path.cwd() must not appear in server tool handlers.

This test prevents reintroduction of Path.cwd() call sites.

The one allowed site (_reload_session_handler in tools_kitchen.py) is excluded
because it uses cwd to find the server's own log directory, not the project root.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]

# Files to check and their allowed Path.cwd() sites.
# None means: compute allowed lines dynamically by function name (see _get_allowlisted_lines).
TOOLS_FILES: dict[str, list[int] | None] = {
    "src/autoskillit/server/tools/tools_kitchen.py": None,  # allowed in _reload_session_handler
    "src/autoskillit/server/tools/tools_recipe.py": [],
    "src/autoskillit/server/tools/tools_issue_headless.py": [],
    "src/autoskillit/server/tools/tools_issue_labels.py": [],
}

LIFESPAN_FILES: dict[str, list[int] | None] = {
    "src/autoskillit/server/_lifespan.py": [],
}

ALL_FILES: dict[str, list[int] | None] = {**TOOLS_FILES, **LIFESPAN_FILES}


def _find_cwd_lines_in_function(source: str, func_name: str) -> list[int]:
    """Return line numbers where Path.cwd() appears within the named function."""
    lines = source.splitlines()
    func_start = None
    func_indent = 0
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith(f"def {func_name}") or stripped.startswith(
            f"async def {func_name}"
        ):
            func_start = i
            func_indent = len(line) - len(stripped)
            break
    if func_start is None:
        return []
    func_end = len(lines)
    for i in range(func_start + 1, len(lines)):
        line = lines[i]
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        stripped = line.lstrip()
        if indent <= func_indent and (
            stripped.startswith("def ")
            or stripped.startswith("async def ")
            or stripped.startswith("class ")
        ):
            func_end = i
            break
    return [
        i + 1
        for i, line in enumerate(lines[func_start:func_end], start=func_start)
        if "Path.cwd()" in line
    ]


def _get_allowlisted_lines(filepath: str, source: str) -> list[int]:
    """Get the allowlisted line numbers for a given file."""
    entry = ALL_FILES.get(filepath)
    if entry is None:
        return _find_cwd_lines_in_function(source, "_reload_session_handler")
    return entry


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

        try:
            source = filepath.read_text()
        except OSError:
            missing_files.append(rel_path)
            continue

        lines_with_cwd = [
            i for i, line in enumerate(source.splitlines(), start=1) if "Path.cwd()" in line
        ]
        allowlisted = _get_allowlisted_lines(rel_path, source)

        for lineno in lines_with_cwd:
            if lineno not in allowlisted:
                violations.append(f"{rel_path}:{lineno}: Path.cwd() found (not allowlisted)")

    assert not missing_files, (
        "Server tool files not found (update TOOLS_FILES if renamed):\n" + "\n".join(missing_files)
    )
    assert not violations, "Path.cwd() found in server tool handlers:\n" + "\n".join(violations)
