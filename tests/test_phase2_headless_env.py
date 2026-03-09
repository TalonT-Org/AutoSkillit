"""Phase 2 tests: AUTOSKILLIT_HEADLESS=1 env var injection in headless.py."""

from __future__ import annotations

import inspect


def test_headless_command_includes_headless_env_var() -> None:
    """run_headless_core must inject AUTOSKILLIT_HEADLESS=1 into the subprocess command."""
    from autoskillit.execution import headless as headless_mod

    src = inspect.getsource(headless_mod.run_headless_core)
    assert "AUTOSKILLIT_HEADLESS=1" in src, (
        "run_headless_core must inject AUTOSKILLIT_HEADLESS=1 into the subprocess command "
        "so PreToolUse hooks can identify headless sessions."
    )
