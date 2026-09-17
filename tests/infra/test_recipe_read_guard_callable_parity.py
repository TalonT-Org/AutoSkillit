"""Parity coverage for recipe-read callable admission at both boundaries."""

from __future__ import annotations

import io
import json
import os
from contextlib import redirect_stdout
from unittest.mock import patch

import pytest

from autoskillit.server.lifecycle._guards import _check_recipe_read_prohibition

pytestmark = [pytest.mark.layer("infra"), pytest.mark.small]


_CMD_RPC_CALLABLES = (
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
)

_RECIPE_READER_CALLABLES = (
    "autoskillit.recipe.io.load_recipe",
    "autoskillit.recipe.schema.validate",
    "autoskillit.recipe.loader.parse_recipe_metadata",
    "autoskillit.recipe.load_recipe",
    "autoskillit.recipe.list_recipes",
    "autoskillit.recipe.parse_recipe_metadata",
)


def _hook_denies_callable(callable_name: str) -> bool:
    from autoskillit.hooks.guards.recipe_read_guard import main

    event = {
        "tool_name": "mcp__mcp-autoskillit__run_python",
        "tool_input": {"callable": callable_name},
    }
    with (
        patch.dict(os.environ, {"AUTOSKILLIT_HEADLESS": "1"}, clear=True),
        patch("sys.stdin", io.StringIO(json.dumps(event))),
    ):
        output = io.StringIO()
        with redirect_stdout(output):
            try:
                main()
            except SystemExit:
                pass
    if not output.getvalue().strip():
        return False
    payload = json.loads(output.getvalue())
    return payload["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize(
    ("callable_name", "expected_denial"),
    [
        *((callable_name, False) for callable_name in _CMD_RPC_CALLABLES),
        *((callable_name, True) for callable_name in _RECIPE_READER_CALLABLES),
    ],
)
def test_recipe_read_callable_decision_matches_hook_and_server_lifecycle_guard(
    monkeypatch: pytest.MonkeyPatch,
    callable_name: str,
    expected_denial: bool,
) -> None:
    """Both headless boundaries must classify each dotted callable identically."""
    hook_denied = _hook_denies_callable(callable_name)

    monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
    server_denied = _check_recipe_read_prohibition(callable_name=callable_name) is not None

    assert hook_denied == expected_denial
    assert server_denied == expected_denial
    assert hook_denied == server_denied
