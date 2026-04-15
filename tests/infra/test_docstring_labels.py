"""Tests for correct docstring layer labels across the codebase."""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def test_session_py_docstring_says_l2():
    """session.py docstring must carry an L2 label."""
    src = (_REPO_ROOT / "src/autoskillit/execution/session.py").read_text()
    assert "L2" in src.split('"""')[1], "session.py docstring must say L2"


def test_headless_py_docstring_says_l1():
    """headless.py docstring must carry an L1 label."""
    src = (_REPO_ROOT / "src/autoskillit/execution/headless.py").read_text()
    assert "L1" in src.split('"""')[1], "headless.py docstring must say L1"


def test_smoke_utils_documents_limitation():
    """smoke_utils.py documents its file-path coupling limitation."""
    src = (_REPO_ROOT / "src/autoskillit/smoke_utils.py").read_text()
    assert "limitation" in src.lower() or "known" in src.lower(), (
        "smoke_utils.py must document its path-coupling limitation"
    )


def test_claude_md_no_stale_l3_headless_label():
    """CLAUDE.md must not contain stale 'L3 service' label for headless."""
    src = (_REPO_ROOT / "CLAUDE.md").read_text()
    assert "L3 service module" not in src, (
        "CLAUDE.md still contains stale 'L3 service module' label for headless"
    )
