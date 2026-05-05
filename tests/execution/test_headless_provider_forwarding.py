"""Tests verifying provider_extras, profile_name, provider_name, and provider_fallback_env
forwarding through the headless call chain."""

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
    execute_kwargs: dict = {}

    def fake_build(skill_command, **kwargs):
        captured.update(kwargs)
        return object()

    async def fake_execute(spec, cwd, ctx, **kwargs):
        execute_kwargs.update(kwargs)
        return _STUB_RESULT

    monkeypatch.setattr("autoskillit.execution.headless.build_skill_session_cmd", fake_build)
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
    assert "provider_extras" not in execute_kwargs
    assert "profile_name" not in execute_kwargs


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

    monkeypatch.setattr("autoskillit.execution.headless.build_skill_session_cmd", fake_build)
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


def test_execute_claude_headless_accepts_provider_name_and_fallback_env() -> None:
    import inspect

    from autoskillit.execution.headless import _execute_claude_headless

    sig = inspect.signature(_execute_claude_headless)
    assert sig.parameters["provider_name"].default == ""
    assert sig.parameters["provider_fallback_env"].default is None


def test_run_headless_core_accepts_provider_name_and_fallback_env() -> None:
    import inspect

    from autoskillit.execution.headless import run_headless_core

    sig = inspect.signature(run_headless_core)
    assert sig.parameters["provider_name"].default == ""
    assert sig.parameters["provider_fallback_env"].default is None


def test_default_executor_run_accepts_provider_name_and_fallback_env() -> None:
    import inspect

    from autoskillit.execution.headless import DefaultHeadlessExecutor

    sig = inspect.signature(DefaultHeadlessExecutor.run)
    assert sig.parameters["provider_name"].default == ""
    assert sig.parameters["provider_fallback_env"].default is None


@pytest.mark.anyio
async def test_run_headless_core_forwards_provider_name_and_fallback_env(
    minimal_ctx, tmp_path, monkeypatch
) -> None:
    from autoskillit.execution.headless import run_headless_core

    execute_kwargs: dict = {}

    def fake_build(skill_command, **kwargs):  # noqa: ARG001
        return object()

    async def fake_execute(spec, cwd, ctx, **kwargs):  # noqa: ARG001
        execute_kwargs.update(kwargs)
        return _STUB_RESULT

    monkeypatch.setattr("autoskillit.execution.headless.build_skill_session_cmd", fake_build)
    monkeypatch.setattr("autoskillit.execution.headless._execute_claude_headless", fake_execute)

    await run_headless_core(
        "/autoskillit:probe",
        str(tmp_path),
        minimal_ctx,
        provider_name="bedrock",
        provider_fallback_env={"KEY": "val"},
    )

    assert execute_kwargs["provider_name"] == "bedrock"
    assert execute_kwargs["provider_fallback_env"] == {"KEY": "val"}


@pytest.mark.anyio
async def test_default_executor_run_forwards_provider_name_and_fallback_env(
    minimal_ctx, tmp_path, monkeypatch
) -> None:
    import autoskillit.execution.headless as _headless_mod
    from autoskillit.execution.headless import DefaultHeadlessExecutor

    captured: dict = {}

    async def fake_core(skill_command, cwd, ctx, **kwargs):  # noqa: ARG001
        captured.update(kwargs)
        return _STUB_RESULT

    monkeypatch.setattr(_headless_mod, "run_headless_core", fake_core)

    executor = DefaultHeadlessExecutor(minimal_ctx)
    await executor.run(
        "/autoskillit:probe",
        str(tmp_path),
        provider_name="vertex",
        provider_fallback_env={"K": "v"},
    )

    assert captured["provider_name"] == "vertex"
    assert captured["provider_fallback_env"] == {"K": "v"}


@pytest.mark.anyio
async def test_no_fallback_env_returns_empty_provider_used(
    minimal_ctx, tmp_path, monkeypatch
) -> None:
    from autoskillit.execution.commands import ClaudeHeadlessCmd
    from autoskillit.execution.headless import PostSessionMetrics, _execute_claude_headless
    from tests.execution.conftest import _sr

    _spec = ClaudeHeadlessCmd(cmd=["echo", "test"], env={})
    _sub_result = _sr()

    async def fake_runner(cmd, **kwargs):  # noqa: ARG001
        return _sub_result

    minimal_ctx.runner = fake_runner

    monkeypatch.setattr(
        "autoskillit.execution.headless._build_skill_result",
        lambda *a, **kw: _STUB_RESULT,  # noqa: ARG005
    )
    monkeypatch.setattr(
        "autoskillit.execution.headless._compute_post_session_metrics",
        lambda *a, **kw: PostSessionMetrics(0, 0, str(tmp_path)),  # noqa: ARG005
    )
    monkeypatch.setattr(
        "autoskillit.execution.headless._capture_git_head_sha",
        lambda *a: "",  # noqa: ARG005
    )

    result = await _execute_claude_headless(
        _spec,
        str(tmp_path),
        minimal_ctx,
        timeout=30.0,
        stale_threshold=5.0,
    )

    assert result.provider_used == ""
    assert result.provider_fallback is False


@pytest.mark.anyio
async def test_provider_name_stamps_provider_used_on_result(
    minimal_ctx, tmp_path, monkeypatch
) -> None:
    from autoskillit.execution.commands import ClaudeHeadlessCmd
    from autoskillit.execution.headless import PostSessionMetrics, _execute_claude_headless
    from tests.execution.conftest import _sr

    _spec = ClaudeHeadlessCmd(cmd=["echo", "test"], env={})
    _sub_result = _sr()

    async def fake_runner(cmd, **kwargs):  # noqa: ARG001
        return _sub_result

    minimal_ctx.runner = fake_runner

    monkeypatch.setattr(
        "autoskillit.execution.headless._build_skill_result",
        lambda *a, **kw: _STUB_RESULT,  # noqa: ARG005
    )
    monkeypatch.setattr(
        "autoskillit.execution.headless._compute_post_session_metrics",
        lambda *a, **kw: PostSessionMetrics(0, 0, str(tmp_path)),  # noqa: ARG005
    )
    monkeypatch.setattr(
        "autoskillit.execution.headless._capture_git_head_sha",
        lambda *a: "",  # noqa: ARG005
    )

    result = await _execute_claude_headless(
        _spec,
        str(tmp_path),
        minimal_ctx,
        timeout=30.0,
        stale_threshold=5.0,
        provider_name="bedrock",
    )

    assert result.provider_used == "bedrock"
    assert result.provider_fallback is False


def test_headless_executor_protocol_includes_provider_params() -> None:
    import inspect

    from autoskillit.core.types import HeadlessExecutor

    sig = inspect.signature(HeadlessExecutor.run)
    assert sig.parameters["provider_name"].default == ""
    assert sig.parameters["provider_fallback_env"].default is None
