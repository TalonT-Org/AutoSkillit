"""Check the standalone ownership parser against live MCP tool response schemas."""

from pathlib import Path
from typing import Any

import pytest

from tests._script_loader import load_check_script

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium]

sessions = load_check_script(
    "_autoskillit_e2e_sessions_mcp_contract",
    Path(__file__).resolve().parents[2] / "scripts" / "e2e" / "e2e_sessions.py",
)


@pytest.mark.anyio
async def test_ownership_parser_accepts_actual_product_tool_envelopes(
    tool_ctx_kitchen_open: Any,
    kitchen_enabled: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from unittest.mock import AsyncMock

    from mcp.types import CallToolResult

    import autoskillit.server as server
    import autoskillit.server._misc as server_misc
    from autoskillit.server.tools import tools_git, tools_issue_composite

    def parsed_response(response: Any) -> dict[str, Any] | None:
        wire_result = CallToolResult(
            content=response.content, structuredContent=response.structured_content
        ).model_dump(mode="json", by_alias=True, exclude_none=True)
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
