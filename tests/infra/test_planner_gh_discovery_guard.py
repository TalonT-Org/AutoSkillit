"""Tests for the planner_gh_discovery_guard PreToolUse hook.

Guards against GitHub discovery commands (gh issue list, gh pr list, gh search,
gh api listing endpoints) in planner skill sessions while allowing targeted
reads (gh issue view <N>, gh api .../issues/<N>).
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import unittest.mock

import pytest

pytestmark = [pytest.mark.layer("infra"), pytest.mark.small]

_BASH_TOOL = "Bash"
_RUN_CMD_TOOL = "mcp__autoskillit__local__autoskillit__run_cmd"


def _run_guard(
    cmd: str,
    *,
    tool_type: str = "bash",
    skill_name: str = "planner-generate-phases",
    headless: bool = True,
    raw_stdin: str | None = None,
) -> str:
    """Invoke planner_gh_discovery_guard.main() and return captured stdout."""
    from autoskillit.hooks.guards.planner_gh_discovery_guard import main  # noqa: PLC0415

    if raw_stdin is not None:
        stdin_content = raw_stdin
    else:
        if tool_type == "bash":
            tool_name = _BASH_TOOL
            tool_input = {"command": cmd}
        else:
            tool_name = _RUN_CMD_TOOL
            tool_input = {"cmd": cmd}
        stdin_content = json.dumps({"tool_name": tool_name, "tool_input": tool_input})

    env_patch: dict[str, str] = {}
    env_remove: list[str] = []
    if headless:
        env_patch["AUTOSKILLIT_HEADLESS"] = "1"
    else:
        env_remove.append("AUTOSKILLIT_HEADLESS")
    if skill_name:
        env_patch["AUTOSKILLIT_SKILL_NAME"] = skill_name
    else:
        env_remove.append("AUTOSKILLIT_SKILL_NAME")

    clean_env = {k: v for k, v in os.environ.items() if k not in env_remove}
    clean_env.update(env_patch)

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        with unittest.mock.patch("sys.stdin", io.StringIO(stdin_content)):
            with unittest.mock.patch.dict(os.environ, clean_env, clear=True):
                try:
                    main()
                except SystemExit as exc:
                    assert exc.code == 0, f"Guard exited non-zero: {exc.code!r}"

    return buf.getvalue()


def _is_denied(output: str) -> bool:
    if not output:
        return False
    data = json.loads(output)
    return data.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


# ---------------------------------------------------------------------------
# Group 1: Planner session — discovery commands DENIED
# ---------------------------------------------------------------------------


class TestPlannerDiscoveryDenied:
    def test_denies_gh_issue_list(self):
        out = _run_guard("gh issue list")
        assert _is_denied(out)

    def test_denies_gh_issue_list_with_flags(self):
        out = _run_guard("gh issue list --state open --label bug")
        assert _is_denied(out)

    def test_denies_gh_pr_list(self):
        out = _run_guard("gh pr list")
        assert _is_denied(out)

    def test_denies_gh_search_issues(self):
        out = _run_guard('gh search issues "query"')
        assert _is_denied(out)

    def test_denies_gh_search_prs(self):
        out = _run_guard('gh search prs "query"')
        assert _is_denied(out)

    def test_denies_gh_api_issues_listing(self):
        out = _run_guard("gh api /repos/owner/repo/issues")
        assert _is_denied(out)

    def test_denies_gh_api_pulls_listing(self):
        out = _run_guard("gh api /repos/owner/repo/pulls")
        assert _is_denied(out)

    def test_denies_via_run_cmd(self):
        out = _run_guard("gh issue list", tool_type="run_cmd")
        assert _is_denied(out)

    def test_denies_gh_in_pipeline(self):
        out = _run_guard("echo foo && gh issue list")
        assert _is_denied(out)

    @pytest.mark.parametrize(
        "skill",
        ["planner-analyze", "planner-elaborate-phase", "planner-refine"],
    )
    def test_denies_all_planner_prefixes(self, skill: str):
        out = _run_guard("gh issue list", skill_name=skill)
        assert _is_denied(out)


# ---------------------------------------------------------------------------
# Group 2: Planner session — targeted reads ALLOWED
# ---------------------------------------------------------------------------


class TestPlannerTargetedAllowed:
    def test_allows_gh_issue_view(self):
        out = _run_guard("gh issue view 1625")
        assert out.strip() == ""

    def test_allows_gh_pr_view(self):
        out = _run_guard("gh pr view 42")
        assert out.strip() == ""

    def test_allows_gh_api_specific_issue(self):
        out = _run_guard("gh api /repos/owner/repo/issues/123")
        assert out.strip() == ""

    def test_allows_gh_api_specific_pull(self):
        out = _run_guard("gh api /repos/owner/repo/pulls/456")
        assert out.strip() == ""

    def test_allows_gh_issue_view_with_json(self):
        out = _run_guard("gh issue view 42 --json title,body")
        assert out.strip() == ""


# ---------------------------------------------------------------------------
# Group 3: Non-planner session — everything ALLOWED
# ---------------------------------------------------------------------------


class TestNonPlannerAllowed:
    def test_allows_when_skill_name_unset(self):
        out = _run_guard("gh issue list", skill_name="", headless=True)
        assert out.strip() == ""

    def test_allows_when_skill_is_investigate(self):
        out = _run_guard("gh issue list", skill_name="investigate", headless=True)
        assert out.strip() == ""

    def test_allows_when_skill_is_triage_issues(self):
        out = _run_guard("gh issue list", skill_name="triage-issues", headless=True)
        assert out.strip() == ""


# ---------------------------------------------------------------------------
# Group 4: Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_allows_non_gh_command(self):
        out = _run_guard("ls -la")
        assert out.strip() == ""

    def test_allows_malformed_stdin(self):
        out = _run_guard("", raw_stdin="not-json{{{")
        assert out.strip() == ""

    def test_allows_empty_command(self):
        out = _run_guard("")
        assert out.strip() == ""

    def test_denies_gh_api_issues_with_query_params(self):
        out = _run_guard("gh api /repos/o/r/issues?state=open")
        assert _is_denied(out)

    def test_allows_gh_api_non_issues_endpoint(self):
        out = _run_guard("gh api /repos/o/r/commits")
        assert out.strip() == ""


# ---------------------------------------------------------------------------
# Group 5: Headless gate
# ---------------------------------------------------------------------------


class TestHeadlessGate:
    def test_allows_when_not_headless(self):
        out = _run_guard(
            "gh issue list",
            skill_name="planner-generate-phases",
            headless=False,
        )
        assert out.strip() == ""

    def test_denies_when_headless_and_planner(self):
        out = _run_guard(
            "gh issue list",
            skill_name="planner-generate-phases",
            headless=True,
        )
        assert _is_denied(out)


# ---------------------------------------------------------------------------
# Nested-shell structured-tokenization regressions
# ---------------------------------------------------------------------------


class TestNestedShellAllow:
    """Nested-shell fallback must not falsely deny prose / grep / view / api-specific."""

    def test_allows_bash_c_prose(self):
        out = _run_guard('bash -c "echo listing issue tracker items"')
        assert out.strip() == "", "Words inside bash -c prose must not be denied"

    def test_allows_bash_c_grep_pattern(self):
        out = _run_guard("sh -c \"grep 'gh issue list' docs/\"")
        assert out.strip() == "", "Grep pattern inside sh -c must not be denied"

    def test_allows_bash_c_targeted_view(self):
        out = _run_guard('bash -c "gh issue view 1"')
        assert out.strip() == "", "Targeted view inside bash -c must not be denied"

    def test_allows_bash_c_api_specific(self):
        out = _run_guard('bash -c "gh api /repos/o/r/issues/1"')
        assert out.strip() == "", "Specific API inside bash -c must not be denied"

    def test_allows_malformed_inner_payload(self):
        """Malformed inner shell text must remain fail-open (no deny)."""
        out = _run_guard("bash -c \"echo 'unclosed")
        assert out.strip() == "", "Malformed inner payload must fail open"


class TestNestedShellDeny:
    """Real nested-shell discovery commands must still be denied."""

    def test_denies_bash_c_gh_issue_list(self):
        out = _run_guard('bash -c "gh issue list --state open"')
        assert _is_denied(out)

    def test_denies_absolute_bash_with_sudo(self):
        out = _run_guard('/bin/bash -c "sudo -u root gh pr list"')
        assert _is_denied(out)

    def test_denies_nested_bash_c(self):
        out = _run_guard("""bash -c 'bash -c "gh search issues bug"'""")
        assert _is_denied(out)

    def test_denies_targeted_then_discovery(self):
        """A targeted read in one segment must not suppress a later discovery segment."""
        out = _run_guard('bash -c "gh issue view 1 && gh issue list"')
        assert _is_denied(out)

    def test_denies_bash_c_api_listing(self):
        out = _run_guard('bash -c "gh api /repos/o/r/issues"')
        assert _is_denied(out)
