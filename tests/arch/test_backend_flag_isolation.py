"""Structural guard: ClaudeFlags must not appear in _session_launch.py.

If this guard fires, a backend-specific flag has leaked into the CLI layer.
Backend flag translation belongs in each backend's build_interactive_cmd().
"""

from __future__ import annotations

import ast

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


def _session_launch_source() -> str:
    from autoskillit.core import paths

    return (paths.pkg_root() / "cli" / "session" / "_session_launch.py").read_text()


def test_claude_flags_not_referenced_in_session_launch() -> None:
    """ClaudeFlags must not appear anywhere in _session_launch.py.

    Backend-specific flag logic belongs inside each backend's
    build_interactive_cmd(), not in the CLI dispatch layer.
    """
    source = _session_launch_source()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "ClaudeFlags":
            pytest.fail(
                "ClaudeFlags referenced in _session_launch.py — "
                "backend-specific flags must live inside the backend's "
                "build_interactive_cmd(), not in the CLI layer."
            )
        if isinstance(node, ast.Attribute) and node.attr == "ClaudeFlags":
            pytest.fail(
                "ClaudeFlags referenced in _session_launch.py — "
                "backend-specific flags must live inside the backend's "
                "build_interactive_cmd(), not in the CLI layer."
            )
