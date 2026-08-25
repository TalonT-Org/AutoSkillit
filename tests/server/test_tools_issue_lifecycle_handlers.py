"""Tests for tools_issue_headless.prepare_issue and tools_issue_labels handlers."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from autoskillit.core import (
    RetryReason,
    SkillExecutionRole,
    SkillResult,
    SkillSource,
)
from autoskillit.pipeline.gate import DefaultGateState
from autoskillit.server.tools.tools_issue_headless import prepare_issue
from autoskillit.server.tools.tools_issue_labels import claim_issue, release_issue
from tests.server._issue_lifecycle_test_helpers import _make_skill_result

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


@pytest.fixture
def tool_ctx_kitchen_open(tool_ctx):
    """Open the gate while retaining production backend compatibility metadata."""
    tool_ctx.gate = DefaultGateState(enabled=True)
    return tool_ctx


# ---------------------------------------------------------------------------
# MCP tool handlers
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_prepare_issue_gate_closed(tool_ctx) -> None:
    """Gate disabled → gate error JSON."""
    tool_ctx.gate = DefaultGateState(enabled=False)
    result = json.loads(await prepare_issue("Title", "Body"))
    assert result["success"] is False
    assert result["subtype"] == "gate_error"


@pytest.mark.anyio
async def test_prepare_issue_no_executor(tool_ctx_kitchen_open) -> None:
    """executor=None → {"success": False, "error": "Executor not configured"}."""
    tool_ctx_kitchen_open.executor = None
    result = json.loads(await prepare_issue("Title", "Body"))
    assert result["success"] is False
    assert "Executor not configured" in result["error"]


@pytest.mark.anyio
async def test_prepare_issue_session_failure(tool_ctx) -> None:
    """executor.run → success=False → error response with diagnostic fields."""
    skill_result = _make_skill_result(
        success=False, subtype="timeout", exit_code=1, stderr="process killed"
    )
    tool_ctx.executor = AsyncMock()
    tool_ctx.executor.run = AsyncMock(return_value=skill_result)

    result = json.loads(await prepare_issue("Title", "Body"))
    assert result["success"] is False
    assert "session_id" in result
    assert "stderr" in result


@pytest.mark.anyio
async def test_prepare_issue_empty_output(tool_ctx_kitchen_open) -> None:
    """success=True but result="" → drain-race error."""
    skill_result = _make_skill_result(success=True, result="")
    tool_ctx_kitchen_open.executor = AsyncMock()
    tool_ctx_kitchen_open.executor.run = AsyncMock(return_value=skill_result)

    result = json.loads(await prepare_issue("Title", "Body"))
    assert result["success"] is False
    assert "drain race" in result["error"]


@pytest.mark.anyio
async def test_prepare_issue_block_parse_error(tool_ctx_kitchen_open) -> None:
    """success=True with no delimiters → degraded-success warning."""
    skill_result = _make_skill_result(success=True, result="some output without delimiters")
    tool_ctx_kitchen_open.executor = AsyncMock()
    tool_ctx_kitchen_open.executor.run = AsyncMock(return_value=skill_result)

    result = json.loads(await prepare_issue("Title", "Body"))
    assert result["success"] is True
    assert result["status"] == "degraded"
    assert result["warning"] == "no result block found"


@pytest.mark.anyio
async def test_prepare_issue_contract_recovery_includes_partial_issue_url(
    tool_ctx_kitchen_open,
) -> None:
    """CONTRACT_RECOVERY with URL in result.result → partial_issue_url propagated to caller."""
    skill_result = _make_skill_result(
        success=False,
        retry_reason=RetryReason.CONTRACT_RECOVERY,
        result=(
            "I created the issue.\n\n"
            "issue_url = https://github.com/owner/repo/issues/42\n\n"
            "Now applying labels..."
        ),
    )
    tool_ctx_kitchen_open.executor = AsyncMock()
    tool_ctx_kitchen_open.executor.run = AsyncMock(return_value=skill_result)

    result = json.loads(await prepare_issue("Title", "Body"))
    assert result["success"] is False
    assert result["partial_issue_url"] == "https://github.com/owner/repo/issues/42"
    assert result["partial_issue_number"] == 42


@pytest.mark.anyio
async def test_prepare_issue_failure_without_issue_url_has_no_partial_fields(
    tool_ctx_kitchen_open,
) -> None:
    """Generic failure (no URL anywhere in result.result) → no partial_issue_* fields."""
    skill_result = _make_skill_result(
        success=False, result="Session timed out before any side effect"
    )
    tool_ctx_kitchen_open.executor = AsyncMock()
    tool_ctx_kitchen_open.executor.run = AsyncMock(return_value=skill_result)

    result = json.loads(await prepare_issue("Title", "Body"))
    assert result["success"] is False
    assert "partial_issue_url" not in result
    assert "partial_issue_number" not in result


@pytest.mark.anyio
async def test_prepare_issue_success(tool_ctx_kitchen_open) -> None:
    """Complete success path → success=True, block fields merged without 'success' key conflict."""
    block_data = {"issue_url": "https://github.com/o/r/issues/1", "issue_number": 1}
    payload = json.dumps(block_data)
    output = f"---prepare-issue-result---\n{payload}\n---/prepare-issue-result---\n"
    skill_result = _make_skill_result(success=True, result=output)
    tool_ctx_kitchen_open.executor = AsyncMock()
    tool_ctx_kitchen_open.executor.run = AsyncMock(return_value=skill_result)

    result = json.loads(await prepare_issue("Title", "Body"))
    assert result["success"] is True
    assert result["status"] == "complete"
    assert result["issue_url"] == "https://github.com/o/r/issues/1"


@pytest.mark.anyio
async def test_prepare_issue_requires_client_before_creating_issue_for_additional_labels(
    tool_ctx_kitchen_open,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_executor = AsyncMock()
    monkeypatch.setattr(tool_ctx_kitchen_open, "executor", mock_executor)
    monkeypatch.setattr(tool_ctx_kitchen_open, "github_client", None)

    result = json.loads(await prepare_issue("Title", "Body", labels=["bug"]))

    assert result["success"] is False
    assert "GitHub client not configured" in result["error"]
    mock_executor.run.assert_not_called()


@pytest.mark.anyio
async def test_prepare_issue_applies_deduplicated_additional_labels_once(
    tool_ctx_kitchen_open,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    block_data = {
        "issue_url": "https://github.com/o/r/issues/7",
        "issue_number": 7,
        "labels_applied": ["recipe:implementation", "bug"],
    }
    output = f"---prepare-issue-result---\n{json.dumps(block_data)}\n---/prepare-issue-result---\n"
    mock_executor = AsyncMock()
    mock_executor.run.return_value = _make_skill_result(success=True, result=output)
    mock_client = AsyncMock()
    mock_client.add_labels.return_value = {
        "success": True,
        "labels": ["urgent", "recipe:implementation", "bug"],
    }
    mock_sleep = AsyncMock()
    monkeypatch.setattr(tool_ctx_kitchen_open, "executor", mock_executor)
    monkeypatch.setattr(tool_ctx_kitchen_open, "github_client", mock_client)
    monkeypatch.setattr(
        "autoskillit.server.tools.tools_issue_headless.asyncio.sleep",
        mock_sleep,
    )

    result = json.loads(await prepare_issue("Title", "Body", labels=["bug", "urgent", "bug"]))

    assert result["success"] is True
    assert result["labels_applied"] == ["recipe:implementation", "bug", "urgent"]
    mock_sleep.assert_awaited_once_with(1)
    mock_client.add_labels.assert_awaited_once_with("o", "r", 7, ["bug", "urgent"])


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("issue_url", "issue_number"),
    [
        ("not-an-issue-url", 7),
        ("https://github.com/o/r/issues/7/extra", 7),
        ("https://github.com/o/r/issues/7", 8),
    ],
    ids=["malformed", "trailing-path", "number-mismatch"],
)
async def test_prepare_issue_rejects_invalid_created_issue_identity_before_labeling(
    issue_url: str,
    issue_number: int,
    tool_ctx_kitchen_open,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    block_data = {"issue_url": issue_url, "issue_number": issue_number}
    output = f"---prepare-issue-result---\n{json.dumps(block_data)}\n---/prepare-issue-result---\n"
    mock_executor = AsyncMock()
    mock_executor.run.return_value = _make_skill_result(success=True, result=output)
    mock_client = AsyncMock()
    monkeypatch.setattr(tool_ctx_kitchen_open, "executor", mock_executor)
    monkeypatch.setattr(tool_ctx_kitchen_open, "github_client", mock_client)

    result = json.loads(await prepare_issue("Title", "Body", labels=["bug"]))

    assert result["success"] is False
    assert result["issue_url"] == issue_url
    assert result["issue_number"] == issue_number
    mock_client.add_labels.assert_not_called()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "label_result",
    [
        {"success": False, "error": "denied"},
        {"success": True},
        {"success": True, "labels": ["other"]},
    ],
    ids=["failure", "missing-labels", "requested-label-missing"],
)
async def test_prepare_issue_reports_post_creation_label_failure(
    label_result: dict[str, object],
    tool_ctx_kitchen_open,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    block_data = {
        "issue_url": "https://github.com/o/r/issues/7",
        "issue_number": 7,
        "labels_applied": ["recipe:implementation"],
    }
    output = f"---prepare-issue-result---\n{json.dumps(block_data)}\n---/prepare-issue-result---\n"
    mock_executor = AsyncMock()
    mock_executor.run.return_value = _make_skill_result(success=True, result=output)
    mock_client = AsyncMock()
    mock_client.add_labels.return_value = label_result
    monkeypatch.setattr(tool_ctx_kitchen_open, "executor", mock_executor)
    monkeypatch.setattr(tool_ctx_kitchen_open, "github_client", mock_client)
    monkeypatch.setattr(
        "autoskillit.server.tools.tools_issue_headless.asyncio.sleep",
        AsyncMock(),
    )

    result = json.loads(await prepare_issue("Title", "Body", labels=["bug"]))

    assert result["success"] is False
    assert result["status"] == "failed"
    assert result["error"].startswith("Additional labels were not applied:")
    assert result["issue_url"] == "https://github.com/o/r/issues/7"
    assert result["issue_number"] == 7


@pytest.mark.anyio
async def test_prepare_issue_dry_run_with_labels_needs_no_client_or_sleep(
    tool_ctx_kitchen_open,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    block_data = {"dry_run": True, "labels_applied": ["recipe:implementation"]}
    output = f"---prepare-issue-result---\n{json.dumps(block_data)}\n---/prepare-issue-result---\n"
    mock_executor = AsyncMock()
    mock_executor.run.return_value = _make_skill_result(success=True, result=output)
    mock_sleep = AsyncMock()
    monkeypatch.setattr(tool_ctx_kitchen_open, "executor", mock_executor)
    monkeypatch.setattr(tool_ctx_kitchen_open, "github_client", None)
    monkeypatch.setattr(
        "autoskillit.server.tools.tools_issue_headless.asyncio.sleep",
        mock_sleep,
    )

    result = json.loads(await prepare_issue("Title", "Body", labels=["bug", "bug"], dry_run=True))

    assert result["success"] is True
    assert result["labels_applied"] == ["recipe:implementation", "bug"]
    assert "--label" not in mock_executor.run.await_args.args[0]
    mock_sleep.assert_not_awaited()


@pytest.mark.anyio
async def test_prepare_issue_dispatches_only_projected_skill_documents(
    tool_ctx_kitchen_open,
) -> None:
    """Direct lifecycle sessions receive a sanitized ephemeral skill tree."""
    captured: dict[str, object] = {}
    output = (
        "---prepare-issue-result---\n"
        '{"issue_number": 42, "url": "https://example.test/42"}\n'
        "---/prepare-issue-result---\n"
    )

    async def _run_with_projection(
        _command: str,
        _cwd: str,
        *,
        add_dirs=(),
        **_kwargs,
    ) -> SkillResult:
        assert len(add_dirs) == 1
        session_root = Path(add_dirs[0].path)
        documents = list(session_root.rglob("SKILL.md"))
        assert documents
        captured["root"] = session_root
        captured["documents"] = [path.read_text() for path in documents]
        return _make_skill_result(success=True, result=output)

    tool_ctx_kitchen_open.executor = AsyncMock()
    tool_ctx_kitchen_open.executor.run = AsyncMock(side_effect=_run_with_projection)

    result = json.loads(await prepare_issue("Title", "Body"))

    assert result["success"] is True
    documents = captured["documents"]
    assert isinstance(documents, list)
    for content in documents:
        assert "uses_capabilities:" not in content
        assert "execution_role:" not in content
        assert "activate_deps:" not in content
    root = captured["root"]
    assert isinstance(root, Path)
    assert not root.exists()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("handler", "args", "skill_name", "output"),
    [
        (
            prepare_issue,
            ("Title", "Body"),
            "prepare-issue",
            (
                "---prepare-issue-result---\n"
                '{"issue_number": 42, "url": "https://example.test/42"}\n'
                "---/prepare-issue-result---\n"
            ),
        ),
    ],
    ids=("prepare-issue",),
)
async def test_issue_launchers_deliver_winning_override_identity_and_projection(
    tool_ctx_kitchen_open,
    tmp_path,
    handler,
    args,
    skill_name,
    output,
) -> None:
    import hashlib

    from autoskillit.workspace import DefaultSkillResolver

    override = tmp_path / ".claude" / "skills" / skill_name / "SKILL.md"
    override.parent.mkdir(parents=True)
    override.write_text(
        "---\n"
        f"name: {skill_name}\n"
        "description: Winning direct-launch override.\n"
        "uses_capabilities: [github_api_write]\n"
        "execution_role: session\n"
        "---\n"
        f"winning {skill_name} override body\n"
        "gh issue edit 42 --body-file report.md\n"
    )
    source_before = override.read_bytes()
    captured: dict[str, object] = {}

    async def _capture_contract(
        _command: str,
        _cwd: str,
        *,
        capability_contract,
        add_dirs=(),
        **_kwargs,
    ) -> SkillResult:
        assert len(add_dirs) == 1
        projected_paths = [
            path
            for path in Path(add_dirs[0].path).rglob("SKILL.md")
            if path.parent.name == skill_name
        ]
        assert len(projected_paths) == 1
        captured["contract"] = capability_contract
        captured["projected_digest"] = hashlib.sha256(projected_paths[0].read_bytes()).hexdigest()
        return _make_skill_result(success=True, result=output)

    tool_ctx_kitchen_open.project_dir = tmp_path
    tool_ctx_kitchen_open.skill_resolver = DefaultSkillResolver()
    tool_ctx_kitchen_open.executor = AsyncMock()
    tool_ctx_kitchen_open.executor.run = AsyncMock(side_effect=_capture_contract)

    result = json.loads(await handler(*args))

    assert result["success"] is True
    contract = captured["contract"]
    assert contract.source_identities[skill_name]["origin"] == SkillSource.PROJECT_LOCAL.value
    assert contract.execution_role == SkillExecutionRole.SESSION.value
    assert contract.capability_union == frozenset({"github_api_write"})
    assert contract.canonical_digests[skill_name] == hashlib.sha256(source_before).hexdigest()
    assert contract.projected_digests[skill_name] == captured["projected_digest"]
    assert not hasattr(contract, "projected_artifacts")
    assert override.read_bytes() == source_before


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("handler", "args"),
    [
        (prepare_issue, ("Title", "Body")),
    ],
    ids=("prepare-issue",),
)
async def test_issue_headless_handlers_fail_closed_without_skill_resolver(
    tool_ctx_kitchen_open,
    handler,
    args,
) -> None:
    """Missing resolution metadata must stop lifecycle dispatch before executor.run."""
    tool_ctx_kitchen_open.executor = AsyncMock()
    tool_ctx_kitchen_open.skill_resolver = None

    result = json.loads(await handler(*args))

    assert result["success"] is False
    assert result["subtype"] == "crashed"
    assert "resolver" in result["result"].lower()
    tool_ctx_kitchen_open.executor.run.assert_not_awaited()


@pytest.mark.anyio
async def test_claim_issue_gate_closed(tool_ctx) -> None:
    """Gate disabled → gate error JSON."""
    tool_ctx.gate = DefaultGateState(enabled=False)
    result = json.loads(await claim_issue("https://github.com/owner/repo/issues/42"))
    assert result["success"] is False
    assert result["subtype"] == "gate_error"


@pytest.mark.anyio
async def test_claim_issue_no_client(tool_ctx_kitchen_open) -> None:
    """github_client=None → error response."""
    tool_ctx_kitchen_open.github_client = None
    result = json.loads(await claim_issue("https://github.com/owner/repo/issues/42"))
    assert result["success"] is False
    assert "token" in result["error"].lower() or "github" in result["error"].lower()


@pytest.mark.anyio
async def test_claim_issue_already_claimed_returns_not_claimed(
    tool_ctx_kitchen_open,
) -> None:
    """Label already present, allow_reentry=False → claimed=False."""
    issue_data = {
        "success": True,
        "state": "open",
        "labels": [{"name": "autoskillit:in-progress"}],
        "body": "",
    }
    tool_ctx_kitchen_open.github_client = AsyncMock()
    tool_ctx_kitchen_open.github_client.fetch_issue = AsyncMock(return_value=issue_data)

    result = json.loads(
        await claim_issue(
            "https://github.com/owner/repo/issues/42",
            label="autoskillit:in-progress",
            allow_reentry=False,
        )
    )
    assert result["success"] is True
    assert result["claimed"] is False


@pytest.mark.anyio
async def test_claim_issue_reentry_allowed(tool_ctx_kitchen_open) -> None:
    """Label already present, allow_reentry=True → claimed=True, reentry=True."""
    issue_data = {
        "success": True,
        "state": "open",
        "labels": [{"name": "autoskillit:in-progress"}],
        "body": "",
    }
    tool_ctx_kitchen_open.github_client = AsyncMock()
    tool_ctx_kitchen_open.github_client.fetch_issue = AsyncMock(return_value=issue_data)

    result = json.loads(
        await claim_issue(
            "https://github.com/owner/repo/issues/42",
            label="autoskillit:in-progress",
            allow_reentry=True,
        )
    )
    assert result["success"] is True
    assert result["claimed"] is True
    assert result.get("reentry") is True


@pytest.mark.anyio
async def test_claim_issue_success(tool_ctx_kitchen_open) -> None:
    """Label not present → applies label, claimed=True."""
    issue_data = {"success": True, "state": "open", "labels": [], "body": ""}
    tool_ctx_kitchen_open.github_client = AsyncMock()
    tool_ctx_kitchen_open.github_client.fetch_issue = AsyncMock(return_value=issue_data)
    tool_ctx_kitchen_open.github_client.ensure_label = AsyncMock(return_value={"success": True})
    tool_ctx_kitchen_open.github_client.swap_labels = AsyncMock(return_value={"success": True})

    result = json.loads(
        await claim_issue(
            "https://github.com/owner/repo/issues/42",
            label="autoskillit:in-progress",
        )
    )
    assert result["success"] is True
    assert result["claimed"] is True
    assert result["issue_number"] == 42


@pytest.mark.anyio
async def test_release_issue_gate_closed(tool_ctx) -> None:
    """Gate disabled → gate error JSON."""
    tool_ctx.gate = DefaultGateState(enabled=False)
    result = json.loads(await release_issue("https://github.com/owner/repo/issues/42"))
    assert result["success"] is False
    assert result["subtype"] == "gate_error"


@pytest.mark.anyio
async def test_release_issue_no_staging_when_same_branch(
    tool_ctx_kitchen_open,
) -> None:
    """target_branch == promotion_target → staged=False."""
    tool_ctx_kitchen_open.github_client = AsyncMock()
    tool_ctx_kitchen_open.github_client.remove_label = AsyncMock(return_value={"success": True})
    promotion_target = tool_ctx_kitchen_open.config.branching.promotion_target

    result = json.loads(
        await release_issue(
            "https://github.com/owner/repo/issues/42",
            label="autoskillit:in-progress",
            target_branch=promotion_target,
        )
    )
    assert result["success"] is True
    assert result["staged"] is False


@pytest.mark.anyio
async def test_release_issue_stages_when_different_branch(
    tool_ctx_kitchen_open,
) -> None:
    """target_branch != promotion_target → staged=True, staged_label applied."""
    tool_ctx_kitchen_open.github_client = AsyncMock()
    tool_ctx_kitchen_open.github_client.remove_label = AsyncMock(return_value={"success": True})
    tool_ctx_kitchen_open.github_client.ensure_label = AsyncMock(return_value={"success": True})
    tool_ctx_kitchen_open.github_client.swap_labels = AsyncMock(return_value={"success": True})

    result = json.loads(
        await release_issue(
            "https://github.com/owner/repo/issues/42",
            label="autoskillit:in-progress",
            target_branch="integration-branch",
        )
    )
    assert result["success"] is True
    assert result["staged"] is True
    assert result["staged_label"] == "staged"


@pytest.mark.anyio
async def test_prepare_issue_uses_project_dir_as_subprocess_cwd(
    tool_ctx_kitchen_open, tmp_path, monkeypatch
):
    """executor.run must be called with tool_ctx.project_dir as cwd, not Path.cwd().

    Regression test: when project_dir differs from cwd, the headless skill subprocess
    must run in project_dir.
    """
    monkeypatch.chdir(tmp_path)
    different_dir = tmp_path / "project_root"
    different_dir.mkdir()

    mock_ctx = tool_ctx_kitchen_open
    mock_ctx.project_dir = different_dir
    mock_ctx.executor = MagicMock()
    mock_ctx.executor.run = AsyncMock()
    mock_ctx.executor.run.return_value = _make_skill_result(
        success=True,
        result="---prepare-issue-result---\n{}\n---/prepare-issue-result---",
    )

    with patch("autoskillit.server._get_ctx", return_value=mock_ctx):
        with patch("autoskillit.server._state._get_ctx", return_value=mock_ctx):
            with patch("autoskillit.server.logger"):
                await prepare_issue(
                    title="Test issue",
                    body="Test body",
                    repo="owner/repo",
                    dry_run=True,
                    split=False,
                )

    call_args = mock_ctx.executor.run.call_args
    assert call_args is not None, "executor.run was not called"
    actual_cwd = (
        call_args.args[1]
        if call_args.args and len(call_args.args) > 1
        else call_args.kwargs.get("cwd")
    )
    assert actual_cwd == str(different_dir), (
        f"executor.run was called with cwd={actual_cwd!r}, "
        f"expected cwd={str(different_dir)!r}. "
        "The subprocess must run in project_dir, not cwd."
    )
