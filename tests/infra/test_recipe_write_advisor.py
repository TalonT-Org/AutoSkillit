"""Infra tests: recipe_write_advisor.py session-scope enforcement.

Verifies the headless=True (suppressed) and headless=False (emitted) paths,
satisfying the test_scoped_guard_has_both_session_type_test_cases contract.
"""

from __future__ import annotations

import io
import json
import os
from contextlib import redirect_stdout
from unittest.mock import patch


def _run_advisor(
    tool_name: str,
    file_path: str,
    *,
    headless: bool = False,
) -> str:
    from autoskillit.hooks.recipe_write_advisor import main

    payload = json.dumps({"tool_name": tool_name, "tool_input": {"file_path": file_path}})
    env_overrides: dict[str, str | None] = {"AUTOSKILLIT_HEADLESS": "1" if headless else None}
    env_clean = {k: v for k, v in {**os.environ, **{k: v for k, v in env_overrides.items() if v is not None}}.items()}
    if not headless:
        env_clean.pop("AUTOSKILLIT_HEADLESS", None)
    with (
        patch.dict(os.environ, env_clean, clear=True),
        patch("sys.stdin", io.StringIO(payload)),
    ):
        buf = io.StringIO()
        with redirect_stdout(buf):
            try:
                main()
            except SystemExit:
                pass
        return buf.getvalue()


def test_recipe_advisor_emits_advisory_when_headless_false() -> None:
    """Non-headless session: advisory message is emitted for recipe YAML writes."""
    out = _run_advisor("Write", ".autoskillit/recipes/foo.yaml", headless=False)
    assert out.strip(), "Expected advisory output in interactive session"
    data = json.loads(out.strip())
    assert "write-recipe" in data["hookSpecificOutput"]["message"]


def test_recipe_advisor_suppressed_when_headless_true() -> None:
    """Headless session: advisory is suppressed (AUTOSKILLIT_HEADLESS=1)."""
    out = _run_advisor("Write", ".autoskillit/recipes/foo.yaml", headless=True)
    assert not out.strip(), "Advisory must be suppressed in headless sessions"
