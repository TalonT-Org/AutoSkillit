"""Tests for the set_commit_status MCP tool handler."""

from __future__ import annotations

import json

import pytest

from autoskillit.pipeline.gate import GATED_TOOLS, DefaultGateState
from autoskillit.server.tools_ci import set_commit_status
from tests.conftest import _make_result

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


async def _async_return(value: object) -> object:
    """Minimal async helper returning a fixed value — for monkeypatching async callables."""
    return value


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
    raw = await set_commit_status(
        sha="abc123",
        state="pending",
        context="autoskillit/ai-review",
    )
    result = json.loads(raw)
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
async def test_set_commit_status_posts_pending(tool_ctx, monkeypatch):
    """Tool posts gh api with state=pending to the correct endpoint."""
    monkeypatch.setattr(
        "autoskillit.server.tools_ci.infer_repo_from_remote",
        lambda cwd, hint=None: _async_return("owner/repo"),
    )
    tool_ctx.runner.push(_make_result(0, "", ""))

    raw = await set_commit_status(
        sha="deadbeef",
        state="pending",
        context="autoskillit/ai-review",
        description="AI review in progress",
        cwd="/some/repo",
    )
    result = json.loads(raw)

    assert result["success"] is True
    assert result["sha"] == "deadbeef"
    assert result["state"] == "pending"
    assert result["context"] == "autoskillit/ai-review"

    # Verify the POST call went to the right endpoint
    cmd, *_ = tool_ctx.runner.call_args_list[0]
    cmd_str = " ".join(cmd)
    assert "/repos/owner/repo/statuses/deadbeef" in cmd_str
    assert "pending" in cmd_str


# ---------------------------------------------------------------------------
# Posts success status
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_set_commit_status_posts_success(tool_ctx, monkeypatch):
    """Tool posts gh api with state=success and context preserved."""
    monkeypatch.setattr(
        "autoskillit.server.tools_ci.infer_repo_from_remote",
        lambda cwd, hint=None: _async_return("myorg/myrepo"),
    )
    tool_ctx.runner.push(_make_result(0, "", ""))

    raw = await set_commit_status(
        sha="cafebabe",
        state="success",
        context="autoskillit/ai-review",
        cwd="/some/repo",
    )
    result = json.loads(raw)

    assert result["success"] is True
    assert result["state"] == "success"
    assert result["context"] == "autoskillit/ai-review"

    post_cmd = " ".join(tool_ctx.runner.call_args_list[0][0])
    assert "success" in post_cmd


# ---------------------------------------------------------------------------
# Posts failure status
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_set_commit_status_posts_failure(tool_ctx, monkeypatch):
    """Tool posts gh api with state=failure."""
    monkeypatch.setattr(
        "autoskillit.server.tools_ci.infer_repo_from_remote",
        lambda cwd, hint=None: _async_return("owner/repo"),
    )
    tool_ctx.runner.push(_make_result(0, "", ""))

    raw = await set_commit_status(
        sha="feedface",
        state="failure",
        context="autoskillit/ai-review",
        cwd="/some/repo",
    )
    result = json.loads(raw)

    assert result["success"] is True
    assert result["state"] == "failure"

    post_cmd = " ".join(tool_ctx.runner.call_args_list[0][0])
    assert "failure" in post_cmd


# ---------------------------------------------------------------------------
# Infers repo from cwd when repo param absent
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_set_commit_status_infers_repo_from_cwd(tool_ctx, monkeypatch):
    """Tool infers owner/repo via infer_repo_from_remote when repo param is absent."""
    infer_calls: list[str] = []

    async def fake_infer(cwd: str, hint: object = None) -> str:
        infer_calls.append(cwd)
        return "inferred/repo"

    monkeypatch.setattr("autoskillit.server.tools_ci.infer_repo_from_remote", fake_infer)
    tool_ctx.runner.push(_make_result(0, "", ""))

    raw = await set_commit_status(
        sha="abc001",
        state="success",
        context="autoskillit/ai-review",
        # repo not provided — must infer from cwd
        cwd="/some/repo",
    )
    result = json.loads(raw)

    assert result["success"] is True
    assert infer_calls, "infer_repo_from_remote was not called"

    # POST must use the inferred repo
    post_cmd = tool_ctx.runner.call_args_list[0][0]
    assert "inferred/repo" in " ".join(post_cmd)


@pytest.mark.anyio
async def test_set_commit_status_falls_back_to_plugin_dir_when_no_cwd(tool_ctx, monkeypatch):
    """When neither repo nor cwd is provided, tool falls back to plugin_source for inference."""
    infer_calls: list[str] = []

    async def fake_infer(cwd: str, hint: object = None) -> str:
        infer_calls.append(cwd)
        return "fallback/repo"

    monkeypatch.setattr("autoskillit.server.tools_ci.infer_repo_from_remote", fake_infer)
    tool_ctx.runner.push(_make_result(0, "", ""))

    raw = await set_commit_status(
        sha="abc003",
        state="pending",
        context="autoskillit/ai-review",
        # neither repo nor cwd — falls back to tool_ctx.plugin_source
    )
    result = json.loads(raw)

    assert result["success"] is True
    assert infer_calls, "infer_repo_from_remote was not called for fallback"
    # POST must reference the fallback repo
    post_cmd, *_ = tool_ctx.runner.call_args_list[0]
    assert "fallback/repo" in " ".join(post_cmd)


@pytest.mark.anyio
async def test_set_commit_status_uses_explicit_repo_without_inference(tool_ctx, monkeypatch):
    """When repo is provided explicitly, infer_repo_from_remote is not called."""
    infer_calls: list[str] = []

    async def fake_infer(cwd: str, hint: object = None) -> str:
        infer_calls.append(cwd)
        return "should-not-be-used/repo"

    monkeypatch.setattr("autoskillit.server.tools_ci.infer_repo_from_remote", fake_infer)
    tool_ctx.runner.push(_make_result(0, "", ""))

    raw = await set_commit_status(
        sha="abc002",
        state="success",
        context="autoskillit/ai-review",
        repo="explicit/repo",
        cwd="/some/repo",
    )
    result = json.loads(raw)

    assert result["success"] is True
    assert not infer_calls, "infer_repo_from_remote should not be called when repo is explicit"
    post_cmd = tool_ctx.runner.call_args_list[0][0]
    assert "explicit/repo" in " ".join(post_cmd)


# ---------------------------------------------------------------------------
# Error handling — gh api returns non-zero
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_set_commit_status_on_gh_failure_returns_error_dict(tool_ctx, monkeypatch):
    """When gh api returns non-zero, tool returns success=false, never raises."""
    monkeypatch.setattr(
        "autoskillit.server.tools_ci.infer_repo_from_remote",
        lambda cwd, hint=None: _async_return("owner/repo"),
    )
    tool_ctx.runner.push(_make_result(1, "", "API rate limit exceeded"))

    raw = await set_commit_status(
        sha="baadf00d",
        state="success",
        context="autoskillit/ai-review",
        cwd="/some/repo",
    )
    result = json.loads(raw)

    assert result["success"] is False
    assert isinstance(result["error"], str) and result["error"]


@pytest.mark.anyio
async def test_set_commit_status_repo_inference_failure_returns_error(tool_ctx, monkeypatch):
    """When repo inference returns empty string, tool returns success=false."""
    monkeypatch.setattr(
        "autoskillit.server.tools_ci.infer_repo_from_remote",
        lambda cwd, hint=None: _async_return(""),
    )

    raw = await set_commit_status(
        sha="baadf00d",
        state="pending",
        context="autoskillit/ai-review",
        cwd="/some/repo",
    )
    result = json.loads(raw)

    assert result["success"] is False
    assert "error" in result


# ---------------------------------------------------------------------------
# T1 — Return type is str throughout
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_set_commit_status_gate_returns_str(tool_ctx):
    """Gate-closed path must return str, not dict."""
    import json

    tool_ctx.gate = DefaultGateState(enabled=False)
    result = await set_commit_status(sha="abc", state="pending", context="ctx")
    assert isinstance(result, str)
    parsed = json.loads(result)
    assert parsed["success"] is False


@pytest.mark.anyio
async def test_set_commit_status_validation_errors_return_str(tool_ctx):
    """Validation error paths (empty sha) return str."""
    import json

    result = await set_commit_status(sha="", state="pending", context="ctx")
    assert isinstance(result, str)
    parsed = json.loads(result)
    assert parsed["success"] is False


@pytest.mark.anyio
async def test_set_commit_status_success_returns_str(tool_ctx, monkeypatch):
    """Success path returns str."""
    import json

    from tests.conftest import _make_result

    async def fake_infer(cwd: str, hint: object = None) -> str:
        return "owner/repo"

    monkeypatch.setattr("autoskillit.server.tools_ci.infer_repo_from_remote", fake_infer)
    tool_ctx.runner.push(_make_result(0, "", ""))
    result = await set_commit_status(sha="abc123", state="success", context="ci", cwd="/tmp")
    assert isinstance(result, str)
    parsed = json.loads(result)
    assert parsed["success"] is True


# ---------------------------------------------------------------------------
# T2 — Uses infer_repo_from_remote, not gh repo view subprocess
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_set_commit_status_uses_infer_repo_not_gh_subprocess(tool_ctx, monkeypatch):
    """Must call infer_repo_from_remote, not launch gh repo view subprocess."""
    from tests.conftest import _make_result

    calls: list[str] = []

    async def fake_infer(cwd: str, hint: object = None) -> str:
        calls.append(cwd)
        return "owner/repo"

    monkeypatch.setattr("autoskillit.server.tools_ci.infer_repo_from_remote", fake_infer)
    # Only one runner push needed (the POST call, repo already resolved)
    tool_ctx.runner.push(_make_result(0, "", ""))
    await set_commit_status(sha="abc", state="success", context="ci", cwd="/tmp")
    assert calls, "infer_repo_from_remote was never called"
    # gh repo view must NOT have been invoked as a subprocess
    for cmd_args, *_ in tool_ctx.runner.call_args_list:
        assert not ("repo" in cmd_args and "view" in cmd_args), (
            "gh repo view subprocess was invoked; infer_repo_from_remote should be used instead"
        )
