"""Tests verifying provider_extras and profile_name forwarding through the headless call chain."""

from __future__ import annotations

import pytest

from autoskillit.core.types import RetryReason, SkillResult

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]

_STUB_RESULT = SkillResult(
    success=True,
    result="done",
    session_id="s1",
    subtype="success",
    is_error=False,
    exit_code=0,
    needs_retry=False,
    retry_reason=RetryReason.NONE,
    stderr="",
)


@pytest.mark.anyio
async def test_run_headless_core_forwards_provider_extras_to_build_cmd(
    minimal_ctx, tmp_path, monkeypatch
) -> None:
    from autoskillit.execution.headless import run_headless_core

    captured: dict = {}

    def fake_build(skill_command, **kwargs):
        captured.update(kwargs)
        return object()

    async def fake_execute(spec, cwd, ctx, **kwargs):
        return _STUB_RESULT

    monkeypatch.setattr("autoskillit.execution.headless.build_leaf_headless_cmd", fake_build)
    monkeypatch.setattr("autoskillit.execution.headless._execute_claude_headless", fake_execute)

    await run_headless_core(
        "/autoskillit:probe",
        str(tmp_path),
        minimal_ctx,
        provider_extras={"AWS_REGION": "us-east-1"},
        profile_name="bedrock",
    )

    assert captured["provider_extras"] == {"AWS_REGION": "us-east-1"}
    assert captured["profile_name"] == "bedrock"


@pytest.mark.anyio
async def test_run_headless_core_defaults_provider_extras_none(
    minimal_ctx, tmp_path, monkeypatch
) -> None:
    from autoskillit.execution.headless import run_headless_core

    captured: dict = {}

    def fake_build(skill_command, **kwargs):
        captured.update(kwargs)
        return object()

    async def fake_execute(spec, cwd, ctx, **kwargs):
        return _STUB_RESULT

    monkeypatch.setattr("autoskillit.execution.headless.build_leaf_headless_cmd", fake_build)
    monkeypatch.setattr("autoskillit.execution.headless._execute_claude_headless", fake_execute)

    await run_headless_core("/autoskillit:probe", str(tmp_path), minimal_ctx)

    assert captured["provider_extras"] is None
    assert captured["profile_name"] == ""


@pytest.mark.anyio
async def test_default_executor_run_forwards_provider_extras(
    minimal_ctx, tmp_path, monkeypatch
) -> None:
    import autoskillit.execution.headless as _headless_mod
    from autoskillit.execution.headless import DefaultHeadlessExecutor

    captured: dict = {}

    async def fake_core(skill_command, cwd, ctx, **kwargs):
        captured.update(kwargs)
        return _STUB_RESULT

    monkeypatch.setattr(_headless_mod, "run_headless_core", fake_core)

    executor = DefaultHeadlessExecutor(minimal_ctx)
    await executor.run(
        "/autoskillit:probe",
        str(tmp_path),
        provider_extras={"KEY": "val"},
        profile_name="vertex",
    )

    assert captured["provider_extras"] == {"KEY": "val"}
    assert captured["profile_name"] == "vertex"


@pytest.mark.anyio
async def test_default_executor_run_defaults_provider_extras(
    minimal_ctx, tmp_path, monkeypatch
) -> None:
    import autoskillit.execution.headless as _headless_mod
    from autoskillit.execution.headless import DefaultHeadlessExecutor

    captured: dict = {}

    async def fake_core(skill_command, cwd, ctx, **kwargs):
        captured.update(kwargs)
        return _STUB_RESULT

    monkeypatch.setattr(_headless_mod, "run_headless_core", fake_core)

    executor = DefaultHeadlessExecutor(minimal_ctx)
    await executor.run("/autoskillit:probe", str(tmp_path))

    assert captured["provider_extras"] is None
    assert captured["profile_name"] == ""
