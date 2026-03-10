"""Tests for the set_commit_status MCP tool handler."""

from __future__ import annotations

import pytest

from autoskillit.pipeline.gate import GATED_TOOLS, DefaultGateState
from autoskillit.server.tools_ci import set_commit_status
from tests.conftest import _make_result

# ---------------------------------------------------------------------------
# Gate membership
# ---------------------------------------------------------------------------


def test_set_commit_status_is_kitchen_gated():
    """Tool is tagged kitchen and present in GATED_TOOLS."""
    assert "set_commit_status" in GATED_TOOLS


# ---------------------------------------------------------------------------
# Gate-closed behavioural check
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_set_commit_status_gate_check(tool_ctx):
    """Gate-closed returns gate_error, never calls subprocess."""
    tool_ctx.gate = DefaultGateState(enabled=False)
    result = await set_commit_status(
        sha="abc123",
        state="pending",
        context="autoskillit/ai-review",
    )
    assert result["success"] is False
    assert result.get("subtype") == "gate_error"
    # set_commit_status calls _run_subprocess → tool_ctx.runner (confirmed wired by
    # test_set_commit_status_posts_pending). If the gate error path bypassed the gate
    # and proceeded to shell dispatch, runner.call_args_list would be non-empty.
    assert tool_ctx.runner.call_args_list == []


# ---------------------------------------------------------------------------
# Posts pending status
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_set_commit_status_posts_pending(tool_ctx):
    """Tool posts gh api with state=pending to the correct endpoint."""
    # First call: gh repo view (repo inference)
    tool_ctx.runner.push(_make_result(0, "owner/repo\n", ""))
    # Second call: gh api POST
    tool_ctx.runner.push(_make_result(0, "", ""))

    result = await set_commit_status(
        sha="deadbeef",
        state="pending",
        context="autoskillit/ai-review",
        description="AI review in progress",
        cwd="/some/repo",
    )

    assert result["success"] is True
    assert result["sha"] == "deadbeef"
    assert result["state"] == "pending"
    assert result["context"] == "autoskillit/ai-review"

    # Verify the POST call went to the right endpoint
    # MockSubprocessRunner stores (cmd, cwd, timeout, kwargs) tuples
    cmd, *_ = tool_ctx.runner.call_args_list[1]
    cmd_str = " ".join(cmd)
    assert "/repos/owner/repo/statuses/deadbeef" in cmd_str
    assert "pending" in cmd_str


# ---------------------------------------------------------------------------
# Posts success status
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_set_commit_status_posts_success(tool_ctx):
    """Tool posts gh api with state=success and context preserved."""
    tool_ctx.runner.push(_make_result(0, "myorg/myrepo\n", ""))
    tool_ctx.runner.push(_make_result(0, "", ""))

    result = await set_commit_status(
        sha="cafebabe",
        state="success",
        context="autoskillit/ai-review",
        cwd="/some/repo",
    )

    assert result["success"] is True
    assert result["state"] == "success"
    assert result["context"] == "autoskillit/ai-review"

    post_cmd = " ".join(tool_ctx.runner.call_args_list[1][0])
    assert "success" in post_cmd


# ---------------------------------------------------------------------------
# Posts failure status
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_set_commit_status_posts_failure(tool_ctx):
    """Tool posts gh api with state=failure."""
    tool_ctx.runner.push(_make_result(0, "owner/repo\n", ""))
    tool_ctx.runner.push(_make_result(0, "", ""))

    result = await set_commit_status(
        sha="feedface",
        state="failure",
        context="autoskillit/ai-review",
        cwd="/some/repo",
    )

    assert result["success"] is True
    assert result["state"] == "failure"

    post_cmd = " ".join(tool_ctx.runner.call_args_list[1][0])
    assert "failure" in post_cmd


# ---------------------------------------------------------------------------
# Infers repo from cwd when repo param absent
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_set_commit_status_infers_repo_from_cwd(tool_ctx):
    """Tool infers owner/repo from cwd git remote when repo param is absent."""
    tool_ctx.runner.push(_make_result(0, "inferred/repo\n", ""))
    tool_ctx.runner.push(_make_result(0, "", ""))

    result = await set_commit_status(
        sha="abc001",
        state="success",
        context="autoskillit/ai-review",
        # repo not provided — must infer from cwd
        cwd="/some/repo",
    )

    assert result["success"] is True
    # First subprocess call must be gh repo view
    first_cmd = tool_ctx.runner.call_args_list[0][0]
    assert "gh" in first_cmd
    assert "repo" in first_cmd
    assert "view" in first_cmd

    # POST must use the inferred repo
    post_cmd = tool_ctx.runner.call_args_list[1][0]
    assert "inferred/repo" in " ".join(post_cmd)


@pytest.mark.anyio
async def test_set_commit_status_falls_back_to_plugin_dir_when_no_cwd(tool_ctx):
    """When neither repo nor cwd is provided, tool falls back to plugin_dir for inference."""
    from tests.conftest import _make_result

    tool_ctx.runner.push(_make_result(0, "fallback/repo\n", ""))
    tool_ctx.runner.push(_make_result(0, "", ""))

    result = await set_commit_status(
        sha="abc003",
        state="pending",
        context="autoskillit/ai-review",
        # neither repo nor cwd — falls back to tool_ctx.plugin_dir
    )

    assert result["success"] is True
    # Inference call must have happened (gh repo view)
    first_cmd, *_ = tool_ctx.runner.call_args_list[0]
    assert "gh" in first_cmd and "repo" in first_cmd and "view" in first_cmd
    # POST must reference the fallback repo
    post_cmd, *_ = tool_ctx.runner.call_args_list[1]
    assert "fallback/repo" in " ".join(post_cmd)


@pytest.mark.anyio
async def test_set_commit_status_uses_explicit_repo_without_inference(tool_ctx):
    """When repo is provided explicitly, gh repo view is not called."""
    tool_ctx.runner.push(_make_result(0, "", ""))

    result = await set_commit_status(
        sha="abc002",
        state="success",
        context="autoskillit/ai-review",
        repo="explicit/repo",
        cwd="/some/repo",
    )

    assert result["success"] is True
    # Only one subprocess call (the POST, not gh repo view)
    assert len(tool_ctx.runner.call_args_list) == 1
    post_cmd = tool_ctx.runner.call_args_list[0][0]
    assert "explicit/repo" in " ".join(post_cmd)


# ---------------------------------------------------------------------------
# Error handling — gh api returns non-zero
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_set_commit_status_on_gh_failure_returns_error_dict(tool_ctx):
    """When gh api returns non-zero, tool returns success=false, never raises."""
    tool_ctx.runner.push(_make_result(0, "owner/repo\n", ""))
    tool_ctx.runner.push(_make_result(1, "", "API rate limit exceeded"))

    result = await set_commit_status(
        sha="baadf00d",
        state="success",
        context="autoskillit/ai-review",
        cwd="/some/repo",
    )

    assert result["success"] is False
    assert isinstance(result["error"], str) and result["error"]


@pytest.mark.anyio
async def test_set_commit_status_repo_inference_failure_returns_error(tool_ctx):
    """When repo inference fails, tool returns success=false."""
    tool_ctx.runner.push(_make_result(1, "", "not a git repository"))

    result = await set_commit_status(
        sha="baadf00d",
        state="pending",
        context="autoskillit/ai-review",
        cwd="/some/repo",
    )

    assert result["success"] is False
    assert "error" in result
