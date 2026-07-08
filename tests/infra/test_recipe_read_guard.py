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

    @pytest.mark.parametrize(
        "cmd",
        [
            "git add -- src/autoskillit/recipes/remediation.yaml",
            "git diff --stat -- src/autoskillit/recipes/remediation.yaml",
            "git diff --name-only -- src/autoskillit/recipes/remediation.yaml",
            "git status -- src/autoskillit/recipes/remediation.yaml",
            "wc -l src/autoskillit/recipes/remediation.yaml",
        ],
    )
    def test_allows_recipe_path_vcs_and_metadata_commands(self, cmd):
        out = _run_guard(
            {
                "tool_name": "mcp__mcp-autoskillit__run_cmd",
                "tool_input": {"cmd": cmd},
            }
        )
        assert not out.strip()

    def test_allows_chain_of_safe_recipe_metadata_commands(self):
        out = _run_guard(
            {
                "tool_name": "mcp__mcp-autoskillit__run_cmd",
                "tool_input": {
                    "cmd": (
                        "git add -- src/autoskillit/recipes/remediation.yaml "
                        "&& git status --short -- src/autoskillit/recipes/remediation.yaml"
                    )
                },
            }
        )
        assert not out.strip()

    def test_denies_chained_recipe_read_after_allowed_git_add(self):
        out = _run_guard(
            {
                "tool_name": "mcp__mcp-autoskillit__run_cmd",
                "tool_input": {
                    "cmd": (
                        "git add -- src/autoskillit/recipes/remediation.yaml "
                        "&& cat src/autoskillit/recipes/remediation.yaml"
                    )
                },
            }
        )
        assert _is_denied(out)

    @pytest.mark.parametrize(
        "cmd",
        [
            "git diff -- src/autoskillit/recipes/remediation.yaml",
            "git status -v -- src/autoskillit/recipes/remediation.yaml",
            "git add -p -- src/autoskillit/recipes/remediation.yaml",
            "git add --pathspec-from-file=src/autoskillit/recipes/remediation.yaml",
            "git add --pathspec-from-file src/autoskillit/recipes/remediation.yaml",
            "git diff --stat --patch-with-stat -- src/autoskillit/recipes/remediation.yaml",
            "git diff --stat --patch-with-raw -- src/autoskillit/recipes/remediation.yaml",
            "git diff --stat --binary -- src/autoskillit/recipes/remediation.yaml",
            "git diff --check -- src/autoskillit/recipes/remediation.yaml",
            ("python3 <<'PY'\nprint(open('src/autoskillit/recipes/remediation.yaml').read())\nPY"),
            (
                "git add -- src/autoskillit/recipes/remediation.yaml\n"
                "cat src/autoskillit/recipes/remediation.yaml"
            ),
            (
                "git add -- src/autoskillit/recipes/remediation.yaml "
                "& cat src/autoskillit/recipes/remediation.yaml"
            ),
            'git add -- "$(cat src/autoskillit/recipes/remediation.yaml)"',
            "git add -- src/autoskillit/recipes/remediation.yaml && cat $_",
            (
                "git add -- src/autoskillit/recipes/remediation.yaml"
                "&&cat src/autoskillit/recipes/remediation.yaml"
            ),
            # Input redirection: `<` is not in punctuation_chars; path is in
            # the segment text, pattern matches, verb `cat` is not git/wc.
            "cat < src/autoskillit/recipes/remediation.yaml",
            # Content-reading git subcommands not in the metadata allowlist.
            "git show HEAD:src/autoskillit/recipes/remediation.yaml",
            "git log -p -- src/autoskillit/recipes/remediation.yaml",
            "git blame src/autoskillit/recipes/remediation.yaml",
            "git grep pattern -- src/autoskillit/recipes/remediation.yaml",
            # -A/--all/--force stage content even when restricted to a path.
            "git add -A -- src/autoskillit/recipes/remediation.yaml",
            "git add --all -- src/autoskillit/recipes/remediation.yaml",
        ],
    )
    def test_denies_protected_path_content_bypasses(self, cmd):
        out = _run_guard(
            {
                "tool_name": "mcp__mcp-autoskillit__run_cmd",
                "tool_input": {"cmd": cmd},
            }
        )
        assert _is_denied(out)


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

    def test_cmd_rpc_callables_not_blocked(self):
        """autoskillit.recipe._cmd_rpc* module callables must pass through (not denied)."""
        cmd_rpc_callables = [
            "autoskillit.recipe._cmd_rpc_guards.check_dropped_ci_loop",
            "autoskillit.recipe._cmd_rpc_guards.commit_guard",
            "autoskillit.recipe._cmd_rpc_guards.main_repo_guard",
            "autoskillit.recipe._cmd_rpc_guards.check_eject_limit",
            "autoskillit.recipe._cmd_rpc_guards.compute_branch",
            "autoskillit.recipe._cmd_rpc_guards.check_dropped_healthy_loop",
            "autoskillit.recipe._cmd_rpc_merge.wait_for_direct_merge",
            "autoskillit.recipe._cmd_rpc_merge.wait_for_immediate_merge",
            "autoskillit.recipe._cmd_rpc_merge.direct_merge_conflict_fix",
            "autoskillit.recipe._cmd_rpc_merge.immediate_merge_conflict_fix",
            "autoskillit.recipe._cmd_rpc_merge.queue_ejected_fix",
            "autoskillit.recipe._cmd_rpc_merge.attempt_cheap_rebase",
            "autoskillit.recipe._cmd_rpc_merge.wait_for_review_pr_mergeability",
            "autoskillit.recipe._cmd_rpc_merge.create_persistent_integration",
            "autoskillit.recipe._cmd_rpc_merge.force_push_and_wait_mergeability",
            "autoskillit.recipe._cmd_rpc_merge.advance_queue_pr",
            "autoskillit.recipe._cmd_rpc_merge.review_path_rebase",
            "autoskillit.recipe._cmd_rpc_merge.proactive_rebase_next_pr",
            "autoskillit.recipe._cmd_rpc_issues.refetch_issues",
            "autoskillit.recipe._cmd_rpc_issues.emit_fallback_map",
            "autoskillit.recipe._cmd_rpc_issues.ensure_results",
            "autoskillit.recipe._cmd_rpc_issues.export_local_bundle",
            "autoskillit.recipe._cmd_rpc_issues.create_audit_run_dir",
            "autoskillit.recipe._cmd_rpc_issues.batch_create_issues",
        ]
        for callable_name in cmd_rpc_callables:
            out = _run_guard(
                {
                    "tool_name": "mcp__mcp-autoskillit__run_python",
                    "tool_input": {"callable": callable_name},
                }
            )
            assert not out.strip(), f"Expected {callable_name!r} to be allowed, got: {out!r}"

    def test_recipe_file_readers_still_blocked(self):
        """Non-_cmd_rpc recipe module callables must remain blocked."""
        blocked_callables = [
            "autoskillit.recipe.io.load_recipe",
            "autoskillit.recipe.schema.validate",
            "autoskillit.recipe.loader.parse_recipe_metadata",
            "autoskillit.recipe.load_recipe",
            "autoskillit.recipe.list_recipes",
            "autoskillit.recipe.parse_recipe_metadata",
        ]
        for callable_name in blocked_callables:
            out = _run_guard(
                {
                    "tool_name": "mcp__mcp-autoskillit__run_python",
                    "tool_input": {"callable": callable_name},
                }
            )
            assert _is_denied(out), f"Expected {callable_name!r} to be denied, got: {out!r}"


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

    def test_allows_bash_git_diff_recipe_path(self):
        out = _run_guard(
            {
                "tool_name": "Bash",
                "tool_input": {
                    "command": "git diff --stat -- .autoskillit/recipes/remediation.yaml"
                },
            }
        )
        assert not out.strip()
