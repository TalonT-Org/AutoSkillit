"""Pin the standalone collector's identities, tools and hook policy to the product."""

import json
from pathlib import Path
from typing import Any

import pytest

from autoskillit.core import DIRECT_PREFIX, PLUGIN_PREFIX, ClaudeFlags, pkg_root
from autoskillit.core.types import GATED_TOOLS, UNGATED_TOOLS
from autoskillit.execution.backends import claude_resolvable_agent_names
from tests._hook_protocol_oracle import claude_matcher_admits
from tests.infra._complexity_helpers import load_check_script

pytestmark = pytest.mark.medium

sessions = load_check_script(
    "_autoskillit_e2e_sessions_contract",
    Path(__file__).resolve().parents[2] / "scripts" / "e2e" / "e2e_sessions.py",
)


def test_server_identities_match_canonical_prefixes() -> None:
    assert sessions._AUTOSKILLIT_SERVERS == frozenset(
        tuple(prefix.split("__")[:-1]) for prefix in (DIRECT_PREFIX, PLUGIN_PREFIX)
    )


def test_tracked_tools_are_registered_product_tools() -> None:
    assert sessions._TRACKED_TOOLS
    assert {
        "claim_and_resolve_issue",
        "create_and_publish_branch",
        "run_skill",
        "complete_run_skill_result",
        "recover_run_skill_result",
    } <= sessions._TRACKED_TOOLS
    assert sessions._TRACKED_TOOLS <= GATED_TOOLS | UNGATED_TOOLS


def test_agent_hooks_admit_every_resolvable_agent_and_reject_an_unnamed_one() -> None:
    names = claude_resolvable_agent_names()
    assert names
    for event in sessions._AGENT_EVENTS:
        matcher = sessions.LIFECYCLE_HOOK_MATCHERS[event]
        assert matcher is not None
        assert not claude_matcher_admits(matcher, "")
        assert all(claude_matcher_admits(matcher, name) for name in names)


def test_session_hooks_stay_unfiltered() -> None:
    matchers = sessions.LIFECYCLE_HOOK_MATCHERS
    assert sessions._SESSION_EVENTS == frozenset({"SessionStart", "SessionEnd"})
    assert set(matchers) == sessions._LIFECYCLE_EVENTS
    assert all(matchers[event] is None for event in sessions._SESSION_EVENTS)


@pytest.mark.anyio
async def test_ownership_parser_accepts_actual_product_tool_envelopes(
    tool_ctx_kitchen_open: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from unittest.mock import AsyncMock

    import autoskillit.server as server
    import autoskillit.server._misc as server_misc
    from autoskillit.server.tools import tools_git, tools_issue_composite

    def parsed_response(response: Any) -> dict[str, Any] | None:
        wire_result = response.model_dump(mode="json", by_alias=True, exclude_none=True)
        return sessions._result_object(wire_result)

    async def resolve_remote(_work_dir: str) -> str:
        return "origin"

    async def create_branch(_base_name: str, _remote: str, _work_dir: str) -> dict[str, Any]:
        return {"branch_name": "impl-contract-42", "was_unique": True, "base_ref": "main"}

    class CloneManager:
        push_result: dict[str, Any] = {"success": True}

        def push_to_remote(self, *_args: object, **_kwargs: object) -> dict[str, Any]:
            return self.push_result

    clone_manager = CloneManager()
    monkeypatch.setattr(tool_ctx_kitchen_open, "clone_mgr", clone_manager)
    monkeypatch.setattr(server_misc, "resolve_remote_name", resolve_remote)
    monkeypatch.setattr(tools_git, "_resolve_and_create_branch", create_branch)

    branch_args: dict[str, Any] = {
        "issue_slug": "contract",
        "run_name": "impl",
        "issue_number": "42",
        "work_dir": str(tmp_path),
        "remote_url": "https://github.com/owner/repo.git",
    }
    branch_response = await server.mcp.call_tool(
        "create_and_publish_branch", branch_args, run_middleware=False
    )
    branch_result = parsed_response(branch_response)
    assert branch_result is not None
    branch_violations: list[str] = []
    assert (
        sessions._branch_target(
            "root-session",
            "root-session",
            "create_and_publish_branch",
            branch_args,
            branch_result,
            False,
            True,
            "42",
            "owner/repo",
            branch_violations,
        )
        == "impl-contract-42"
    )
    assert not branch_violations

    clone_manager.push_result = {
        "success": False,
        "stderr": "push failed",
        "error_type": "PushError",
    }
    failed_branch_response = await server.mcp.call_tool(
        "create_and_publish_branch", branch_args, run_middleware=False
    )
    failed_branch_result = parsed_response(failed_branch_response)
    assert failed_branch_result is not None
    branch_violations = []
    assert (
        sessions._branch_target(
            "root-session",
            "root-session",
            "create_and_publish_branch",
            branch_args,
            failed_branch_result,
            False,
            True,
            "42",
            "owner/repo",
            branch_violations,
        )
        is None
    )
    assert branch_violations == ["ownership:branch_result_failed"]

    issue_url = "https://github.com/owner/repo/issues/42"
    claim_label = tool_ctx_kitchen_open.config.github.in_progress_label
    github_client = AsyncMock()
    github_client.fetch_title = AsyncMock(
        return_value={"success": True, "number": 42, "title": "Contract", "slug": "contract"}
    )
    github_client.fetch_issue = AsyncMock(
        side_effect=[
            {"success": True, "state": "open", "labels": [], "body": ""},
            {
                "success": True,
                "state": "open",
                "labels": [{"name": claim_label}],
                "body": "",
            },
        ]
    )
    github_client.ensure_label = AsyncMock(return_value={"success": True})
    github_client.swap_labels = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(tool_ctx_kitchen_open, "github_client", github_client)
    monkeypatch.setattr(tools_issue_composite, "_get_campaign_state_paths", lambda _ctx: [])

    claim_args: dict[str, Any] = {
        "issue_url": issue_url,
        "label": claim_label,
        "allow_reentry": False,
    }
    claim_response = await server.mcp.call_tool(
        "claim_and_resolve_issue", claim_args, run_middleware=False
    )
    claim_result = parsed_response(claim_response)
    assert claim_result is not None
    claim_violations: list[str] = []
    assert sessions._claim_succeeded(
        [(claim_args, claim_result, False)], "42", claim_label, claim_violations
    )
    assert not claim_violations

    reentry_args: dict[str, Any] = {
        "issue_url": issue_url,
        "label": claim_label,
        "allow_reentry": True,
    }
    reentry_response = await server.mcp.call_tool(
        "claim_and_resolve_issue", reentry_args, run_middleware=False
    )
    reentry_result = parsed_response(reentry_response)
    assert reentry_result is not None
    assert reentry_result.get("reentry") is True
    claim_violations = []
    assert not sessions._claim_succeeded(
        [(reentry_args, reentry_result, False)], "42", claim_label, claim_violations
    )


def test_product_selects_no_main_agent() -> None:
    # Internal-agent callbacks are unnamed only while no main agent is set.
    assert {"--agent", "--settings"}.isdisjoint(flag.value for flag in ClaudeFlags)
    root = pkg_root()
    manifest = json.loads((root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert "agent" not in manifest.get("settings", {})
    plugin_settings = root / "settings.json"
    assert not plugin_settings.exists() or "agent" not in json.loads(
        plugin_settings.read_text(encoding="utf-8")
    )
