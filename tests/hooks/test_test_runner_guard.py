"""Tests for test_runner_guard PreToolUse hook.

Validates that direct pytest invocations are intercepted and denied in headless
skill sessions, while read-only commands and `task test-*` invocations pass through.
"""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout

import pytest

from autoskillit.hook_registry import HOOK_REGISTRY, NEW_SUBDIR_BASENAMES

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.small]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_event(command: str, tool_name: str = "Bash") -> dict:
    """Build the stdin JSON dict. Uses 'command' key for Bash, 'cmd' for run_cmd."""
    if tool_name.startswith("mcp__") and tool_name.endswith("__run_cmd"):
        return {"tool_name": tool_name, "tool_input": {"cmd": command}}
    return {"tool_name": tool_name, "tool_input": {"command": command}}


def _run_hook(
    event: dict | None,
    monkeypatch,
    *,
    headless: bool = True,
    raw_stdin: str | None = None,
) -> str:
    """Import main(), patch sys.stdin, capture stdout. Returns stdout string."""
    from autoskillit.hooks.guards.test_runner_guard import main  # noqa: PLC0415

    if headless:
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
    else:
        monkeypatch.delenv("AUTOSKILLIT_HEADLESS", raising=False)

    stdin_text = raw_stdin if raw_stdin is not None else json.dumps(event)
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin_text))
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            main()
    except SystemExit:
        pass
    return buf.getvalue()


def _is_denied(output: str) -> bool:
    if not output:
        return False
    data = json.loads(output)
    return data.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


# ---------------------------------------------------------------------------
# T1: TestSessionScope
# ---------------------------------------------------------------------------


class TestSessionScope:
    def test_allows_when_not_headless(self, monkeypatch):
        event = _build_event("pytest tests/")
        output = _run_hook(event, monkeypatch, headless=False)
        assert output == ""

    def test_denies_when_headless(self, monkeypatch):
        event = _build_event("pytest tests/")
        output = _run_hook(event, monkeypatch, headless=True)
        assert _is_denied(output)


# ---------------------------------------------------------------------------
# T2: TestExemptSkills
# ---------------------------------------------------------------------------


class TestExemptSkills:
    def test_allows_implement_experiment(self, monkeypatch):
        monkeypatch.setenv("AUTOSKILLIT_SKILL_NAME", "implement-experiment")
        event = _build_event("pytest --collect-only tests/")
        output = _run_hook(event, monkeypatch, headless=True)
        assert output == ""

    def test_denies_non_exempt_skill(self, monkeypatch):
        monkeypatch.setenv("AUTOSKILLIT_SKILL_NAME", "implement-worktree")
        event = _build_event("pytest tests/")
        output = _run_hook(event, monkeypatch, headless=True)
        assert _is_denied(output)


# ---------------------------------------------------------------------------
# T3: TestDenyPatterns
# ---------------------------------------------------------------------------


class TestDenyPatterns:
    def test_blocks_bare_pytest(self, monkeypatch):
        event = _build_event("pytest")
        output = _run_hook(event, monkeypatch, headless=True)
        assert _is_denied(output)

    def test_blocks_pytest_with_path(self, monkeypatch):
        event = _build_event("pytest tests/hooks/")
        output = _run_hook(event, monkeypatch, headless=True)
        assert _is_denied(output)

    def test_blocks_python_m_pytest(self, monkeypatch):
        event = _build_event("python -m pytest")
        output = _run_hook(event, monkeypatch, headless=True)
        assert _is_denied(output)

    def test_blocks_python3_m_pytest(self, monkeypatch):
        event = _build_event("python3 -m pytest")
        output = _run_hook(event, monkeypatch, headless=True)
        assert _is_denied(output)

    def test_blocks_venv_pytest(self, monkeypatch):
        event = _build_event(".venv/bin/pytest tests/")
        output = _run_hook(event, monkeypatch, headless=True)
        assert _is_denied(output)

    def test_blocks_uv_run_pytest(self, monkeypatch):
        event = _build_event("uv run pytest tests/")
        output = _run_hook(event, monkeypatch, headless=True)
        assert _is_denied(output)

    def test_blocks_pytest_after_cd(self, monkeypatch):
        event = _build_event("cd /some/path && pytest")
        output = _run_hook(event, monkeypatch, headless=True)
        assert _is_denied(output)

    def test_blocks_pytest_after_semicolon(self, monkeypatch):
        event = _build_event("echo foo; pytest tests/")
        output = _run_hook(event, monkeypatch, headless=True)
        assert _is_denied(output)

    def test_blocks_python_m_py_test(self, monkeypatch):
        event = _build_event("python -m py.test")
        output = _run_hook(event, monkeypatch, headless=True)
        assert _is_denied(output)


# ---------------------------------------------------------------------------
# T4: TestAllowPatterns
# ---------------------------------------------------------------------------


class TestAllowPatterns:
    def test_allows_task_test_check(self, monkeypatch):
        event = _build_event("task test-check")
        output = _run_hook(event, monkeypatch, headless=True)
        assert output == ""

    def test_allows_task_test_all(self, monkeypatch):
        event = _build_event("task test-all")
        output = _run_hook(event, monkeypatch, headless=True)
        assert output == ""

    def test_allows_task_test_filtered(self, monkeypatch):
        event = _build_event("task test-filtered")
        output = _run_hook(event, monkeypatch, headless=True)
        assert output == ""

    def test_allows_pre_commit(self, monkeypatch):
        event = _build_event("pre-commit run --all-files")
        output = _run_hook(event, monkeypatch, headless=True)
        assert output == ""

    def test_allows_grep_pytest(self, monkeypatch):
        event = _build_event("grep -r pytest tests/")
        output = _run_hook(event, monkeypatch, headless=True)
        assert output == ""

    def test_allows_rg_pytest(self, monkeypatch):
        event = _build_event("rg pytest tests/")
        output = _run_hook(event, monkeypatch, headless=True)
        assert output == ""

    def test_allows_cat_conftest(self, monkeypatch):
        event = _build_event("cat tests/conftest.py")
        output = _run_hook(event, monkeypatch, headless=True)
        assert output == ""

    def test_allows_pip_show_pytest(self, monkeypatch):
        event = _build_event("pip show pytest")
        output = _run_hook(event, monkeypatch, headless=True)
        assert output == ""

    def test_allows_git_log_grep_pytest(self, monkeypatch):
        event = _build_event("git log --grep=pytest")
        output = _run_hook(event, monkeypatch, headless=True)
        assert output == ""

    def test_allows_find_pytest_files(self, monkeypatch):
        event = _build_event("find . -name 'test_*.py'")
        output = _run_hook(event, monkeypatch, headless=True)
        assert output == ""

    def test_allows_echo_pytest(self, monkeypatch):
        event = _build_event('echo "use pytest to run tests"')
        output = _run_hook(event, monkeypatch, headless=True)
        assert output == ""

    def test_allows_pytest_substring_in_path(self, monkeypatch):
        event = _build_event("cat pytestmark_example.py")
        output = _run_hook(event, monkeypatch, headless=True)
        assert output == ""


# ---------------------------------------------------------------------------
# T5: TestFailOpen
# ---------------------------------------------------------------------------


class TestFailOpen:
    def test_malformed_json(self, monkeypatch):
        output = _run_hook(None, monkeypatch, headless=True, raw_stdin="not valid json {{{")
        assert output == ""

    def test_missing_tool_input(self, monkeypatch):
        event: dict = {"tool_name": "Bash"}
        output = _run_hook(event, monkeypatch, headless=True)
        assert output == ""

    def test_missing_command_key(self, monkeypatch):
        event: dict = {"tool_name": "Bash", "tool_input": {"unrelated": "x"}}
        output = _run_hook(event, monkeypatch, headless=True)
        assert output == ""


# ---------------------------------------------------------------------------
# T6: TestDenyMessage
# ---------------------------------------------------------------------------


class TestDenyMessage:
    def test_deny_message_contains_corrective_guidance(self, monkeypatch):
        from autoskillit.hooks.guards.test_runner_guard import (  # noqa: PLC0415
            TEST_RUNNER_DENY_TRIGGER,
        )

        event = _build_event("pytest tests/")
        output = _run_hook(event, monkeypatch, headless=True)
        data = json.loads(output)
        reason = data["hookSpecificOutput"]["permissionDecisionReason"]
        assert "task test-check" in reason
        assert "AUTOSKILLIT_TEST_FILTER" in reason
        assert TEST_RUNNER_DENY_TRIGGER in reason


# ---------------------------------------------------------------------------
# T7: TestRegistration
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_registered_in_hook_registry(self):
        all_scripts = {s for h in HOOK_REGISTRY for s in h.scripts}
        assert "guards/test_runner_guard.py" in all_scripts
        matching = [h for h in HOOK_REGISTRY if "guards/test_runner_guard.py" in h.scripts]
        assert matching, "No HookDef found for test_runner_guard.py"
        hookdef = matching[0]
        assert hookdef.event_type == "PreToolUse"
        assert hookdef.matcher == r"Bash|mcp__.*autoskillit.*__run_cmd"
        assert hookdef.session_scope == "headless_only"

    def test_in_new_subdir_basenames(self):
        assert "test_runner_guard.py" in NEW_SUBDIR_BASENAMES

    def test_exempt_skills_in_sync(self):
        from autoskillit.hooks.guards.test_runner_guard import _EXEMPT_SKILLS  # noqa: PLC0415

        matching = [h for h in HOOK_REGISTRY if "guards/test_runner_guard.py" in h.scripts]
        assert matching, "No HookDef found for test_runner_guard.py"
        assert matching[0].exempt_skills == _EXEMPT_SKILLS


# ---------------------------------------------------------------------------
# T8: TestRunCmdVariant
# ---------------------------------------------------------------------------


class TestRunCmdVariant:
    def test_run_cmd_tool_name_detected(self, monkeypatch):
        event = _build_event(
            "pytest tests/", tool_name="mcp__autoskillit__local__autoskillit__run_cmd"
        )
        output = _run_hook(event, monkeypatch, headless=True)
        assert _is_denied(output)
