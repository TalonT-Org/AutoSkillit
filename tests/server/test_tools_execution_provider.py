"""Tests for provider_extras/profile_name forwarding through run_skill()."""

from __future__ import annotations

import pytest

from autoskillit.server.tools.tools_execution import run_skill

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


@pytest.mark.anyio
async def test_run_skill_provider_extras_none_when_feature_disabled(
    tool_ctx, tmp_path, monkeypatch
) -> None:
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx.executor = executor
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx)
    monkeypatch.setattr("autoskillit.core.is_feature_enabled", lambda *a, **kw: False)

    captured: dict = {}
    original_run = executor.run

    async def spy_run(*args, **kwargs):
        captured.update(kwargs)
        return await original_run(*args, **kwargs)

    monkeypatch.setattr(executor, "run", spy_run)

    await run_skill("/autoskillit:probe", str(tmp_path))

    assert captured.get("provider_extras") is None
    assert captured.get("profile_name") == ""


@pytest.mark.anyio
async def test_run_skill_provider_extras_none_for_anthropic_sentinel(
    tool_ctx, tmp_path, monkeypatch
) -> None:
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx.executor = executor
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx)
    monkeypatch.setattr("autoskillit.core.is_feature_enabled", lambda *a, **kw: True)
    monkeypatch.setattr(
        "autoskillit.server._guards._resolve_provider_profile",
        lambda *a: ("anthropic", {"SOME_KEY": "val"}),
    )

    captured: dict = {}
    original_run = executor.run

    async def spy_run(*args, **kwargs):
        captured.update(kwargs)
        return await original_run(*args, **kwargs)

    monkeypatch.setattr(executor, "run", spy_run)

    await run_skill("/autoskillit:probe", str(tmp_path))

    assert captured.get("provider_extras") is None
    assert captured.get("profile_name") == ""


@pytest.mark.anyio
async def test_run_skill_provider_extras_forwarded_for_non_anthropic(
    tool_ctx, tmp_path, monkeypatch
) -> None:
    from tests.fakes import InMemoryHeadlessExecutor

    executor = InMemoryHeadlessExecutor()
    tool_ctx.executor = executor
    monkeypatch.setattr("autoskillit.server._ctx", tool_ctx)
    monkeypatch.setattr("autoskillit.core.is_feature_enabled", lambda *a, **kw: True)
    monkeypatch.setattr(
        "autoskillit.server._guards._resolve_provider_profile",
        lambda *a: ("bedrock", {"AWS_REGION": "us-east-1"}),
    )

    captured: dict = {}
    original_run = executor.run

    async def spy_run(*args, **kwargs):
        captured.update(kwargs)
        return await original_run(*args, **kwargs)

    monkeypatch.setattr(executor, "run", spy_run)

    await run_skill("/autoskillit:probe", str(tmp_path))

    assert captured.get("provider_extras") == {"AWS_REGION": "us-east-1"}
    assert captured.get("profile_name") == "bedrock"
