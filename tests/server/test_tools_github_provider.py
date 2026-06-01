"""Tests for provider_name forwarding through the report_bug call chain."""

from __future__ import annotations

import json

import pytest

from autoskillit.server.tools.tools_github import report_bug

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


@pytest.mark.anyio
async def test_report_bug_forwards_provider_name_as_distinct_parameter(
    tool_ctx_kitchen_open, tmp_path, monkeypatch
) -> None:
    """_run_report_session must thread provider_name as a distinct parameter to executor.run()."""
    from tests.fakes import InMemoryHeadlessExecutor

    tool_ctx_kitchen_open.config.report_bug.report_dir = str(tmp_path / "bug-reports")
    tool_ctx_kitchen_open.config.report_bug.github_filing = False

    executor = InMemoryHeadlessExecutor()
    tool_ctx_kitchen_open.executor = executor

    _feat = "autoskillit.server.tools.tools_github.is_feature_enabled"
    monkeypatch.setattr(_feat, lambda *a, **kw: True)
    monkeypatch.setattr(
        "autoskillit.server._guards._resolve_provider_profile",
        lambda *a, **kw: ("bedrock", {"AWS_REGION": "us-east-1"}),
    )

    result = json.loads(await report_bug("test error context", str(tmp_path), severity="blocking"))

    assert result["success"] is True
    assert len(executor.calls) == 1
    assert executor.calls[0].profile_name == "bedrock"
    assert executor.calls[0].provider_name == "bedrock"
