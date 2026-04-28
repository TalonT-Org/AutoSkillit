"""Tests for autoskillit.hooks.recipe_write_advisor."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from contextlib import redirect_stdout
from unittest.mock import patch

from autoskillit.core.paths import pkg_root


def _run_advisor(payload: dict, extra_env: dict[str, str] | None = None) -> tuple[int, str]:
    hook_path = pkg_root() / "hooks" / "recipe_write_advisor.py"
    env = {k: v for k, v in os.environ.items() if k != "AUTOSKILLIT_HEADLESS"}
    env.update(extra_env or {})
    result = subprocess.run(
        [sys.executable, str(hook_path)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
    )
    return result.returncode, result.stdout


class TestRecipeWriteAdvisor:
    def test_recipe_yaml_write_triggers_advisory(self) -> None:
        """Write to .autoskillit/recipes/foo.yaml triggers write-recipe advisory."""
        payload = {
            "tool_name": "Write",
            "tool_input": {"file_path": ".autoskillit/recipes/foo.yaml"},
        }
        rc, stdout = _run_advisor(payload)
        assert rc == 0
        assert stdout.strip(), "Expected advisory output for recipe YAML"
        data = json.loads(stdout.strip())
        assert "write-recipe" in data["hookSpecificOutput"]["message"]

    def test_non_recipe_yaml_write_is_silent(self) -> None:
        """Write to a non-recipe path produces no output (exit 0)."""
        payload = {
            "tool_name": "Write",
            "tool_input": {"file_path": "/home/user/project/config.py"},
        }
        rc, stdout = _run_advisor(payload)
        assert rc == 0
        assert not stdout.strip()

    def test_campaign_yaml_suggests_make_campaign(self) -> None:
        """Write to campaigns/ subdir suggests make-campaign instead of write-recipe."""
        payload = {
            "tool_name": "Write",
            "tool_input": {"file_path": ".autoskillit/recipes/campaigns/my_campaign.yaml"},
        }
        rc, stdout = _run_advisor(payload)
        assert rc == 0
        assert stdout.strip(), "Expected advisory output for campaign YAML"
        data = json.loads(stdout.strip())
        msg = data["hookSpecificOutput"]["message"]
        assert "make-campaign" in msg
        assert "write-recipe" not in msg

    def test_headless_session_skips_advisory(self) -> None:
        """When AUTOSKILLIT_HEADLESS=1, no advisory is emitted."""
        payload = {
            "tool_name": "Write",
            "tool_input": {"file_path": ".autoskillit/recipes/foo.yaml"},
        }
        rc, stdout = _run_advisor(payload, extra_env={"AUTOSKILLIT_HEADLESS": "1"})
        assert rc == 0
        assert not stdout.strip(), "Advisory must be suppressed in headless sessions"

    def test_edit_tool_also_triggers_advisory(self) -> None:
        """Edit (not just Write) to a recipe YAML also emits the advisory."""
        payload = {
            "tool_name": "Edit",
            "tool_input": {"file_path": "src/autoskillit/recipes/my_recipe.yaml"},
        }
        rc, stdout = _run_advisor(payload)
        assert rc == 0
        assert stdout.strip()
        data = json.loads(stdout.strip())
        assert "write-recipe" in data["hookSpecificOutput"]["message"]

    def test_non_write_edit_tool_is_silent(self) -> None:
        """Tools other than Write/Edit produce no output even for recipe paths."""
        payload = {
            "tool_name": "Read",
            "tool_input": {"file_path": ".autoskillit/recipes/foo.yaml"},
        }
        rc, stdout = _run_advisor(payload)
        assert rc == 0
        assert not stdout.strip()


# ---------------------------------------------------------------------------
# In-process session-scope enforcement tests (satisfies
# test_scoped_guard_has_both_session_type_test_cases contract)
# ---------------------------------------------------------------------------


def _run_advisor_inprocess(
    tool_name: str,
    file_path: str,
    *,
    headless: bool = False,
) -> str:
    from autoskillit.hooks.recipe_write_advisor import main

    payload = json.dumps({"tool_name": tool_name, "tool_input": {"file_path": file_path}})
    env_clean = {"AUTOSKILLIT_HEADLESS": "1"} if headless else {}
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
    out = _run_advisor_inprocess("Write", ".autoskillit/recipes/foo.yaml", headless=False)
    assert out.strip(), "Expected advisory output in interactive session"
    data = json.loads(out.strip())
    assert "write-recipe" in data["hookSpecificOutput"]["message"]


def test_recipe_advisor_suppressed_when_headless_true() -> None:
    """Headless session: advisory is suppressed (AUTOSKILLIT_HEADLESS=1)."""
    out = _run_advisor_inprocess("Write", ".autoskillit/recipes/foo.yaml", headless=True)
    assert not out.strip(), "Advisory must be suppressed in headless sessions"
