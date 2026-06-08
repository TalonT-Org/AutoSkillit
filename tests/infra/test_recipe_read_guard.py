"""Tests for recipe_read_guard — blocks unauthorized recipe/skill/agent file reads."""

from __future__ import annotations

import io
import json
import os
from contextlib import redirect_stdout
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.layer("infra"), pytest.mark.small]


def _run_guard(tool_input, *, headless=True, raw_stdin=None) -> str:
    from autoskillit.hooks.guards.recipe_read_guard import main

    stdin_content = raw_stdin if raw_stdin is not None else json.dumps(tool_input)
    env = {"AUTOSKILLIT_HEADLESS": "1"} if headless else {}
    with (
        patch.dict(os.environ, env, clear=True),
        patch("sys.stdin", io.StringIO(stdin_content)),
    ):
        buf = io.StringIO()
        with redirect_stdout(buf):
            try:
                main()
            except SystemExit:
                pass
        return buf.getvalue()


def _is_denied(output: str) -> bool:
    if not output.strip():
        return False
    data = json.loads(output)
    return data.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


class TestRunCmdBlocking:
    """Verify run_cmd calls to recipe/skill/agent paths are blocked."""

    def test_denies_rg_recipe_yaml_path(self):
        out = _run_guard(
            {
                "tool_name": "mcp__mcp-autoskillit__run_cmd",
                "tool_input": {"cmd": "rg -n 'pattern' src/autoskillit/recipes/impl.yaml"},
            }
        )
        assert _is_denied(out)

    def test_denies_cat_recipe_yaml(self):
        out = _run_guard(
            {
                "tool_name": "mcp__mcp-autoskillit__run_cmd",
                "tool_input": {"cmd": "cat .autoskillit/recipes/foo.yml"},
            }
        )
        assert _is_denied(out)

    def test_denies_sed_skill_md(self):
        out = _run_guard(
            {
                "tool_name": "mcp__mcp-autoskillit__run_cmd",
                "tool_input": {
                    "cmd": "sed -n '1,10p' src/autoskillit/skills_extended/my_skill/SKILL.md"
                },
            }
        )
        assert _is_denied(out)

    def test_denies_cat_agent_md(self):
        out = _run_guard(
            {
                "tool_name": "mcp__mcp-autoskillit__run_cmd",
                "tool_input": {"cmd": "cat src/autoskillit/agents/some_agent.md"},
            }
        )
        assert _is_denied(out)

    def test_allows_unrelated_command(self):
        out = _run_guard(
            {
                "tool_name": "mcp__mcp-autoskillit__run_cmd",
                "tool_input": {"cmd": "git status"},
            }
        )
        assert not out.strip()

    def test_allows_non_recipe_yaml(self):
        out = _run_guard(
            {
                "tool_name": "mcp__mcp-autoskillit__run_cmd",
                "tool_input": {"cmd": "cat config.yaml"},
            }
        )
        assert not out.strip()


class TestRunPythonBlocking:
    """Verify run_python calls to recipe module are blocked."""

    def test_denies_load_recipe_callable(self):
        out = _run_guard(
            {
                "tool_name": "mcp__mcp-autoskillit__run_python",
                "tool_input": {"callable": "autoskillit.recipe.load_recipe"},
            }
        )
        assert _is_denied(out)

    def test_denies_recipe_module_callable(self):
        out = _run_guard(
            {
                "tool_name": "mcp__mcp-autoskillit__run_python",
                "tool_input": {"callable": "autoskillit.recipe.schema.validate"},
            }
        )
        assert _is_denied(out)

    def test_allows_unrelated_callable(self):
        out = _run_guard(
            {
                "tool_name": "mcp__mcp-autoskillit__run_python",
                "tool_input": {"callable": "autoskillit.smoke_utils.check_version"},
            }
        )
        assert not out.strip()


class TestSessionScope:
    """Verify guard respects session scope boundaries."""

    def test_allows_interactive_session(self):
        out = _run_guard(
            {
                "tool_name": "mcp__mcp-autoskillit__run_cmd",
                "tool_input": {"cmd": "cat src/autoskillit/recipes/impl.yaml"},
            },
            headless=False,
        )
        assert not out.strip()

    def test_malformed_input_fails_open(self):
        out = _run_guard({}, raw_stdin="not json at all {{{")
        assert not out.strip()


class TestBashToolCoverage:
    """Verify Bash tool (non-MCP) is also covered."""

    def test_denies_bash_recipe_read(self):
        out = _run_guard(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "cat src/autoskillit/recipes/foo.yaml"},
            }
        )
        assert _is_denied(out)

    def test_allows_bash_unrelated(self):
        out = _run_guard(
            {
                "tool_name": "Bash",
                "tool_input": {"command": "ls -la"},
            }
        )
        assert not out.strip()
