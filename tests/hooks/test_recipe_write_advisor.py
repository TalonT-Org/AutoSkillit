"""Tests for autoskillit.hooks.guards.recipe_write_advisor."""

from __future__ import annotations

import io
import json
import os
from contextlib import redirect_stdout
from unittest.mock import patch

import pytest

from autoskillit.core.paths import pkg_root
from tests._hook_protocol_oracle import (
    STATUS_COMPLETED,
    claude_verdict,
    codex_verdict,
    run_hook,
)

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.medium]

HOOK_PATH = pkg_root() / "hooks" / "guards" / "recipe_write_advisor.py"


def _run_advisor(payload: dict, extra_env: dict[str, str] | None = None):
    env = extra_env or {}
    unset = () if "AUTOSKILLIT_HEADLESS" in env else ("AUTOSKILLIT_HEADLESS",)
    event = {"hook_event_name": "PreToolUse", **payload}
    return run_hook(HOOK_PATH, event, env=env, unset=unset)


def _assert_completed_context(payload: dict, emission) -> str:
    output = json.loads(emission.stdout)
    specific = output["hookSpecificOutput"]
    assert set(output) == {"hookSpecificOutput"}
    assert "additionalContext" in specific
    assert "message" not in specific
    context = specific["additionalContext"]
    event = {"hook_event_name": "PreToolUse", **payload}
    for verdict_fn in (codex_verdict, claude_verdict):
        verdict = verdict_fn(
            event,
            exit_code=emission.exit_code,
            stdout=emission.stdout,
            stderr=emission.stderr,
        )
        assert verdict.status == STATUS_COMPLETED
        assert len(verdict.contexts) == 1
        assert verdict.contexts[0] == context
    return context


class TestRecipeWriteAdvisor:
    def test_recipe_yaml_write_triggers_advisory(self) -> None:
        payload = {
            "tool_name": "Write",
            "tool_input": {"file_path": ".autoskillit/recipes/foo.yaml"},
        }
        emission = _run_advisor(payload)
        assert emission.exit_code == 0
        assert "write-recipe" in _assert_completed_context(payload, emission)

    def test_non_recipe_yaml_write_is_silent(self) -> None:
        payload = {
            "tool_name": "Write",
            "tool_input": {"file_path": "/home/user/project/config.py"},
        }
        emission = _run_advisor(payload)
        assert emission.exit_code == 0
        assert emission.stdout == ""

    def test_campaign_yaml_suggests_make_campaign(self) -> None:
        payload = {
            "tool_name": "Write",
            "tool_input": {"file_path": ".autoskillit/recipes/campaigns/my_campaign.yaml"},
        }
        emission = _run_advisor(payload)
        assert emission.exit_code == 0
        context = _assert_completed_context(payload, emission)
        assert "make-campaign" in context
        assert "write-recipe" not in context

    def test_headless_session_skips_advisory(self) -> None:
        payload = {
            "tool_name": "Write",
            "tool_input": {"file_path": ".autoskillit/recipes/foo.yaml"},
        }
        emission = _run_advisor(payload, extra_env={"AUTOSKILLIT_HEADLESS": "1"})
        assert emission.exit_code == 0
        assert emission.stdout == ""

    def test_edit_tool_also_triggers_advisory(self) -> None:
        payload = {
            "tool_name": "Edit",
            "tool_input": {"file_path": "src/autoskillit/recipes/my_recipe.yaml"},
        }
        emission = _run_advisor(payload)
        assert emission.exit_code == 0
        assert "write-recipe" in _assert_completed_context(payload, emission)

    def test_non_write_edit_tool_is_silent(self) -> None:
        payload = {
            "tool_name": "Read",
            "tool_input": {"file_path": ".autoskillit/recipes/foo.yaml"},
        }
        emission = _run_advisor(payload)
        assert emission.exit_code == 0
        assert emission.stdout == ""


def _run_advisor_inprocess(
    tool_name: str,
    file_path: str,
    *,
    headless: bool = False,
) -> str:
    from autoskillit.hooks.guards.recipe_write_advisor import main

    payload = json.dumps(
        {
            "hook_event_name": "PreToolUse",
            "tool_name": tool_name,
            "tool_input": {"file_path": file_path},
        }
    )
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
    out = _run_advisor_inprocess("Write", ".autoskillit/recipes/foo.yaml", headless=False)
    assert out.strip(), "Expected advisory output in interactive session"
    specific = json.loads(out)["hookSpecificOutput"]
    assert "write-recipe" in specific["additionalContext"]
    assert "message" not in specific


def test_recipe_advisor_suppressed_when_headless_true() -> None:
    out = _run_advisor_inprocess("Write", ".autoskillit/recipes/foo.yaml", headless=True)
    assert not out.strip(), "Advisory must be suppressed in headless sessions"
