"""Tests verifying provider_extras, profile_name, provider_name, and provider_fallback_env
forwarding through the headless call chain."""

from __future__ import annotations

import pytest

from autoskillit.core.types import RetryReason, SkillResult
from autoskillit.execution.backends.claude import ClaudeCodeBackend
from tests.execution.conftest import _mock_backend

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

    from autoskillit.core import CmdSpec
    from autoskillit.execution.headless import run_headless_core

    execute_kwargs: dict = {}

    backend = _mock_backend()
    backend.build_skill_session_cmd.return_value = CmdSpec(
        cmd=("claude", "--print", "test"), env={}
    )
    minimal_ctx.backend = backend

    async def fake_execute(spec, cwd, ctx, **kwargs):
        execute_kwargs.update(kwargs)
        return _STUB_RESULT

    monkeypatch.setattr("autoskillit.execution.headless._execute_claude_headless", fake_execute)

    await run_headless_core(
        "/autoskillit:probe",
        str(tmp_path),
        minimal_ctx,
        provider_extras={"AWS_REGION": "us-east-1"},
        profile_name="bedrock",
    )

    config = backend.build_skill_session_cmd.call_args.args[2]
    assert config.provider_extras == {"AWS_REGION": "us-east-1"}
    assert config.profile_name == "bedrock"
    assert execute_kwargs.get("provider_extras") == {"AWS_REGION": "us-east-1"}
    assert "profile_name" not in execute_kwargs


@pytest.mark.anyio
async def test_run_headless_core_defaults_provider_extras_none(
    minimal_ctx, tmp_path, monkeypatch
) -> None:
    from autoskillit.core import CmdSpec
    from autoskillit.execution.headless import run_headless_core

    backend = _mock_backend()
    backend.build_skill_session_cmd.return_value = CmdSpec(
        cmd=("claude", "--print", "test"), env={}
    )
    minimal_ctx.backend = backend

    async def fake_execute(spec, cwd, ctx, **kwargs):
        return _STUB_RESULT

    monkeypatch.setattr("autoskillit.execution.headless._execute_claude_headless", fake_execute)

    await run_headless_core("/autoskillit:probe", str(tmp_path), minimal_ctx)

    config = backend.build_skill_session_cmd.call_args.args[2]
    assert config.provider_extras is None
    assert config.profile_name == ""


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
    from autoskillit.core import CmdSpec
    from autoskillit.execution.headless import run_headless_core

    execute_kwargs: dict = {}

    backend = _mock_backend()
    backend.build_skill_session_cmd.return_value = CmdSpec(
        cmd=("claude", "--print", "test"), env={}
    )
    minimal_ctx.backend = backend

    async def fake_execute(spec, cwd, ctx, **kwargs):  # noqa: ARG001
        execute_kwargs.update(kwargs)
        return _STUB_RESULT

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

    _spec = ClaudeHeadlessCmd(cmd=("echo", "test"), env={})
    _sub_result = _sr()

    async def fake_runner(cmd, **kwargs):  # noqa: ARG001
        return _sub_result

    minimal_ctx.runner = fake_runner
    minimal_ctx.backend = _mock_backend()

    monkeypatch.setattr(
        "autoskillit.execution.headless._headless_execute._build_skill_result",
        lambda *a, **kw: _STUB_RESULT,  # noqa: ARG005
    )
    monkeypatch.setattr(
        "autoskillit.execution.headless._headless_execute._compute_post_session_metrics",
        lambda *a, **kw: PostSessionMetrics(0, 0, str(tmp_path)),  # noqa: ARG005
    )
    monkeypatch.setattr(
        "autoskillit.execution.headless._headless_execute._capture_git_head_sha",
        lambda *a: "",  # noqa: ARG005
    )

    result = await _execute_claude_headless(
        _spec,
        str(tmp_path),
        minimal_ctx,
        timeout=30.0,
        stale_threshold=5.0,
    )

    assert result.provider.provider_used == ""
    assert result.provider.fallback_activated is False


@pytest.mark.anyio
async def test_provider_name_stamps_provider_used_on_result(
    minimal_ctx, tmp_path, monkeypatch
) -> None:
    from autoskillit.execution.commands import ClaudeHeadlessCmd
    from autoskillit.execution.headless import PostSessionMetrics, _execute_claude_headless
    from tests.execution.conftest import _sr

    _spec = ClaudeHeadlessCmd(cmd=("echo", "test"), env={})
    _sub_result = _sr()

    async def fake_runner(cmd, **kwargs):  # noqa: ARG001
        return _sub_result

    minimal_ctx.runner = fake_runner
    minimal_ctx.backend = _mock_backend()

    monkeypatch.setattr(
        "autoskillit.execution.headless._headless_execute._build_skill_result",
        lambda *a, **kw: _STUB_RESULT,  # noqa: ARG005
    )
    monkeypatch.setattr(
        "autoskillit.execution.headless._headless_execute._compute_post_session_metrics",
        lambda *a, **kw: PostSessionMetrics(0, 0, str(tmp_path)),  # noqa: ARG005
    )
    monkeypatch.setattr(
        "autoskillit.execution.headless._headless_execute._capture_git_head_sha",
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

    assert result.provider.provider_used == "bedrock"
    assert result.provider.fallback_activated is False


def test_headless_executor_protocol_includes_provider_params() -> None:
    import inspect

    from autoskillit.core.types import HeadlessExecutor

    sig = inspect.signature(HeadlessExecutor.run)
    assert sig.parameters["provider_name"].default == ""
    assert sig.parameters["provider_fallback_env"].default is None


def test_build_skill_result_accepts_provider_used_kwarg() -> None:
    import inspect

    from autoskillit.execution.headless._headless_result import _build_skill_result

    sig = inspect.signature(_build_skill_result)
    param = sig.parameters["provider_used"]
    assert param.default == ""
    assert param.kind == inspect.Parameter.KEYWORD_ONLY


def test_build_skill_result_stamps_provider_used_on_result() -> None:
    from autoskillit.execution.headless._headless_result import _build_skill_result
    from tests.execution.conftest import _sr

    sub_result = _sr(stdout="result: done\n", returncode=0)
    sr = _build_skill_result(sub_result, provider_used="vertex", backend=ClaudeCodeBackend())
    assert sr.provider.provider_used == "vertex"
    sr_default = _build_skill_result(sub_result, backend=ClaudeCodeBackend())
    assert sr_default.provider.provider_used == ""


def test_build_skill_result_provider_used_survives_budget_guard() -> None:
    from autoskillit.execution.headless._headless_result import _build_skill_result
    from autoskillit.pipeline.audit import DefaultAuditLog
    from tests.execution.conftest import _sr

    audit = DefaultAuditLog()
    sub_result = _sr(stdout="", returncode=1)
    sr = _build_skill_result(
        sub_result,
        skill_command="/test:cmd",
        audit=audit,
        max_consecutive_retries=3,
        provider_used="bedrock",
        backend=ClaudeCodeBackend(),
    )
    assert sr.provider.provider_used == "bedrock"


# ── marker_dir / session_id forwarding tests ───────────────────────────────────


def test_execute_claude_headless_accepts_marker_dir_and_session_id() -> None:
    import inspect

    from autoskillit.execution.headless import _execute_claude_headless

    sig = inspect.signature(_execute_claude_headless)
    params = sig.parameters
    assert "marker_dir" in params
    assert params["marker_dir"].default is None
    assert params["marker_dir"].kind == inspect.Parameter.KEYWORD_ONLY
    assert "session_id" in params
    assert params["session_id"].default is None
    assert params["session_id"].kind == inspect.Parameter.KEYWORD_ONLY


def test_dispatch_food_truck_accepts_marker_dir_and_session_id() -> None:
    import inspect

    from autoskillit.execution.headless import DefaultHeadlessExecutor

    sig = inspect.signature(DefaultHeadlessExecutor.dispatch_food_truck)
    params = sig.parameters
    assert "marker_dir" in params
    assert params["marker_dir"].default is None
    assert params["marker_dir"].kind == inspect.Parameter.KEYWORD_ONLY
    assert "session_id" in params
    assert params["session_id"].default is None
    assert params["session_id"].kind == inspect.Parameter.KEYWORD_ONLY


@pytest.mark.anyio
async def test_dispatch_food_truck_forwards_marker_dir_and_session_id(
    minimal_ctx, tmp_path, monkeypatch
) -> None:

    from autoskillit.execution.headless import DefaultHeadlessExecutor

    execute_kwargs: dict = {}

    async def fake_execute(spec, cwd, ctx, **kwargs):
        execute_kwargs.update(kwargs)
        return SkillResult(
            success=True,
            result="ok",
            session_id="s1",
            subtype="success",
            is_error=False,
            exit_code=0,
            needs_retry=False,
            retry_reason=RetryReason.NONE,
            stderr="",
        )

    monkeypatch.setattr("autoskillit.execution.headless._execute_claude_headless", fake_execute)
    monkeypatch.setattr(
        "autoskillit.execution.headless._headless_execute._capture_git_head_sha", lambda *a: ""
    )
    monkeypatch.setattr(
        "autoskillit.execution.headless._headless_execute._compute_post_session_metrics",
        lambda *a, **kw: object(),
    )

    minimal_ctx.backend = _mock_backend()
    executor = DefaultHeadlessExecutor(minimal_ctx)
    marker = tmp_path / "markers"
    await executor.dispatch_food_truck(
        "prompt",
        str(tmp_path),
        completion_marker="%%DONE%%",
        marker_dir=marker,
        session_id="dispatch-uuid-123",
    )

    assert execute_kwargs["marker_dir"] == marker
    assert execute_kwargs["session_id"] == "dispatch-uuid-123"


@pytest.mark.anyio
async def test_dispatch_food_truck_derives_marker_dir_from_cwd(
    minimal_ctx, tmp_path, monkeypatch
) -> None:
    from pathlib import Path

    from autoskillit.execution.headless import DefaultHeadlessExecutor

    execute_kwargs: dict = {}

    async def fake_execute(spec, cwd, ctx, **kwargs):
        execute_kwargs.update(kwargs)
        return SkillResult(
            success=True,
            result="ok",
            session_id="s1",
            subtype="success",
            is_error=False,
            exit_code=0,
            needs_retry=False,
            retry_reason=RetryReason.NONE,
            stderr="",
        )

    monkeypatch.setattr("autoskillit.execution.headless._execute_claude_headless", fake_execute)
    monkeypatch.setattr(
        "autoskillit.execution.headless._resolve_session_log_dir",
        lambda cwd, backend: Path("/derived/project"),
    )
    monkeypatch.setattr(
        "autoskillit.execution.headless._headless_execute._capture_git_head_sha", lambda *a: ""
    )
    monkeypatch.setattr(
        "autoskillit.execution.headless._headless_execute._compute_post_session_metrics",
        lambda *a, **kw: object(),
    )

    minimal_ctx.backend = _mock_backend()
    executor = DefaultHeadlessExecutor(minimal_ctx)
    await executor.dispatch_food_truck(
        "prompt",
        str(tmp_path),
        completion_marker="%%DONE%%",
    )

    assert execute_kwargs["marker_dir"] == Path("/derived/project")


@pytest.mark.anyio
async def test_dispatch_food_truck_marker_dir_none_when_cwd_falsy(
    minimal_ctx, monkeypatch
) -> None:
    execute_kwargs: dict = {}

    async def fake_execute(spec, cwd, ctx, **kwargs):
        execute_kwargs.update(kwargs)
        return SkillResult(
            success=True,
            result="ok",
            session_id="s1",
            subtype="success",
            is_error=False,
            exit_code=0,
            needs_retry=False,
            retry_reason=RetryReason.NONE,
            stderr="",
        )

    monkeypatch.setattr("autoskillit.execution.headless._execute_claude_headless", fake_execute)
    monkeypatch.setattr(
        "autoskillit.execution.headless._headless_execute._capture_git_head_sha", lambda *a: ""
    )
    monkeypatch.setattr(
        "autoskillit.execution.headless._headless_execute._compute_post_session_metrics",
        lambda *a, **kw: object(),
    )

    from autoskillit.execution.headless import DefaultHeadlessExecutor

    minimal_ctx.backend = _mock_backend()
    executor = DefaultHeadlessExecutor(minimal_ctx)
    await executor.dispatch_food_truck(
        "prompt",
        "",
        completion_marker="%%DONE%%",
    )

    assert execute_kwargs["marker_dir"] is None


@pytest.mark.anyio
async def test_execute_claude_headless_forwards_marker_dir_to_runner(
    minimal_ctx, tmp_path, monkeypatch
) -> None:
    from pathlib import Path

    from autoskillit.execution.commands import ClaudeHeadlessCmd
    from autoskillit.execution.headless import PostSessionMetrics, _execute_claude_headless
    from tests.execution.conftest import _sr

    spec = ClaudeHeadlessCmd(cmd=("echo", "test"), env={})
    runner_kwargs: dict = {}

    async def fake_runner(cmd, **kwargs):
        runner_kwargs.update(kwargs)
        return _sr()

    minimal_ctx.runner = fake_runner
    minimal_ctx.backend = _mock_backend()
    monkeypatch.setattr(
        "autoskillit.execution.headless._headless_execute._build_skill_result",
        lambda *a, **kw: SkillResult(
            success=True,
            result="ok",
            session_id="s1",
            subtype="success",
            is_error=False,
            exit_code=0,
            needs_retry=False,
            retry_reason=RetryReason.NONE,
            stderr="",
        ),
    )
    monkeypatch.setattr(
        "autoskillit.execution.headless._headless_execute._compute_post_session_metrics",
        lambda *a, **kw: PostSessionMetrics(0, 0, str(tmp_path)),
    )
    monkeypatch.setattr(
        "autoskillit.execution.headless._headless_execute._capture_git_head_sha", lambda *a: ""
    )

    await _execute_claude_headless(
        spec,
        str(tmp_path),
        minimal_ctx,
        timeout=60,
        stale_threshold=30,
        marker_dir=Path("/custom/markers"),
        session_id="sess-abc",
    )

    assert runner_kwargs["marker_dir"] == Path("/custom/markers")
    assert runner_kwargs["session_id"] == "sess-abc"


# ── pty_mode / session_log_dir capability forwarding tests ───────────────────────


@pytest.mark.anyio
async def test_execute_claude_headless_pty_mode_from_backend(
    minimal_ctx, tmp_path, monkeypatch
) -> None:

    from autoskillit.execution.commands import ClaudeHeadlessCmd
    from autoskillit.execution.headless import PostSessionMetrics, _execute_claude_headless
    from tests.execution.conftest import _sr

    spec = ClaudeHeadlessCmd(cmd=("echo", "test"), env={})
    runner_kwargs: dict = {}

    async def fake_runner(cmd, **kwargs):
        runner_kwargs.update(kwargs)
        return _sr()

    minimal_ctx.runner = fake_runner
    minimal_ctx.backend = _mock_backend(pty_required=False)
    monkeypatch.setattr(
        "autoskillit.execution.headless._headless_execute._build_skill_result",
        lambda *a, **kw: SkillResult(
            success=True,
            result="ok",
            session_id="s1",
            subtype="success",
            is_error=False,
            exit_code=0,
            needs_retry=False,
            retry_reason=RetryReason.NONE,
            stderr="",
        ),
    )
    monkeypatch.setattr(
        "autoskillit.execution.headless._headless_execute._compute_post_session_metrics",
        lambda *a, **kw: PostSessionMetrics(0, 0, str(tmp_path)),
    )
    monkeypatch.setattr(
        "autoskillit.execution.headless._headless_execute._capture_git_head_sha", lambda *a: ""
    )

    await _execute_claude_headless(
        spec,
        str(tmp_path),
        minimal_ctx,
        timeout=60,
        stale_threshold=30,
    )

    assert runner_kwargs["pty_mode"] is False


@pytest.mark.anyio
async def test_execute_claude_headless_session_log_dir_none_when_no_channel_b(
    minimal_ctx, tmp_path, monkeypatch
) -> None:

    from autoskillit.execution.commands import ClaudeHeadlessCmd
    from autoskillit.execution.headless import PostSessionMetrics, _execute_claude_headless
    from tests.execution.conftest import _sr

    spec = ClaudeHeadlessCmd(cmd=("echo", "test"), env={})
    runner_kwargs: dict = {}

    async def fake_runner(cmd, **kwargs):
        runner_kwargs.update(kwargs)
        return _sr()

    minimal_ctx.runner = fake_runner
    minimal_ctx.backend = _mock_backend(channel_b_capable=False)
    monkeypatch.setattr(
        "autoskillit.execution.headless._headless_execute._build_skill_result",
        lambda *a, **kw: SkillResult(
            success=True,
            result="ok",
            session_id="s1",
            subtype="success",
            is_error=False,
            exit_code=0,
            needs_retry=False,
            retry_reason=RetryReason.NONE,
            stderr="",
        ),
    )
    monkeypatch.setattr(
        "autoskillit.execution.headless._headless_execute._compute_post_session_metrics",
        lambda *a, **kw: PostSessionMetrics(0, 0, str(tmp_path)),
    )
    monkeypatch.setattr(
        "autoskillit.execution.headless._headless_execute._capture_git_head_sha", lambda *a: ""
    )

    await _execute_claude_headless(
        spec,
        str(tmp_path),
        minimal_ctx,
        timeout=60,
        stale_threshold=30,
    )

    assert runner_kwargs["session_log_dir"] is None


@pytest.mark.anyio
async def test_dispatch_food_truck_marker_dir_none_when_no_channel_b(
    minimal_ctx, tmp_path, monkeypatch
) -> None:
    execute_kwargs: dict = {}

    async def fake_execute(spec, cwd, ctx, **kwargs):
        execute_kwargs.update(kwargs)
        return SkillResult(
            success=True,
            result="ok",
            session_id="s1",
            subtype="success",
            is_error=False,
            exit_code=0,
            needs_retry=False,
            retry_reason=RetryReason.NONE,
            stderr="",
        )

    monkeypatch.setattr("autoskillit.execution.headless._execute_claude_headless", fake_execute)
    monkeypatch.setattr(
        "autoskillit.execution.headless._headless_execute._capture_git_head_sha", lambda *a: ""
    )
    monkeypatch.setattr(
        "autoskillit.execution.headless._headless_execute._compute_post_session_metrics",
        lambda *a, **kw: object(),
    )

    minimal_ctx.backend = _mock_backend(channel_b_capable=False)

    from autoskillit.execution.headless import DefaultHeadlessExecutor

    executor = DefaultHeadlessExecutor(minimal_ctx)
    await executor.dispatch_food_truck(
        "prompt",
        str(tmp_path),
        completion_marker="%%DONE%%",
    )

    assert execute_kwargs["marker_dir"] is None


# ── stream_parser injection tests ────────────────────────────────────────────


@pytest.mark.anyio
async def test_execute_claude_headless_passes_stream_parser_to_runner(
    minimal_ctx, tmp_path, monkeypatch
) -> None:
    from autoskillit.execution.commands import ClaudeHeadlessCmd
    from autoskillit.execution.headless import PostSessionMetrics, _execute_claude_headless
    from tests.execution.conftest import _sr

    spec = ClaudeHeadlessCmd(cmd=("echo", "test"), env={})
    runner_kwargs: dict = {}

    async def fake_runner(cmd, **kwargs):
        runner_kwargs.update(kwargs)
        return _sr()

    minimal_ctx.runner = fake_runner

    sentinel = object()
    backend = _mock_backend()
    backend.stream_parser.return_value = sentinel
    minimal_ctx.backend = backend

    monkeypatch.setattr(
        "autoskillit.execution.headless._headless_execute._build_skill_result",
        lambda *a, **kw: _STUB_RESULT,
    )
    monkeypatch.setattr(
        "autoskillit.execution.headless._headless_execute._compute_post_session_metrics",
        lambda *a, **kw: PostSessionMetrics(0, 0, str(tmp_path)),
    )
    monkeypatch.setattr(
        "autoskillit.execution.headless._headless_execute._capture_git_head_sha",
        lambda *a: "",
    )

    await _execute_claude_headless(
        spec, str(tmp_path), minimal_ctx, timeout=60, stale_threshold=30
    )

    assert runner_kwargs["stream_parser"] is sentinel
    backend.stream_parser.assert_called()


@pytest.mark.anyio
async def test_execute_claude_headless_stream_parser_receives_completion_marker(
    minimal_ctx, tmp_path, monkeypatch
) -> None:
    from autoskillit.execution.commands import ClaudeHeadlessCmd
    from autoskillit.execution.headless import PostSessionMetrics, _execute_claude_headless
    from tests.execution.conftest import _sr

    spec = ClaudeHeadlessCmd(cmd=("echo", "test"), env={})
    runner_kwargs: dict = {}

    async def fake_runner(cmd, **kwargs):
        runner_kwargs.update(kwargs)
        return _sr()

    minimal_ctx.runner = fake_runner

    backend = _mock_backend()
    minimal_ctx.backend = backend

    monkeypatch.setattr(
        "autoskillit.execution.headless._headless_execute._build_skill_result",
        lambda *a, **kw: _STUB_RESULT,
    )
    monkeypatch.setattr(
        "autoskillit.execution.headless._headless_execute._compute_post_session_metrics",
        lambda *a, **kw: PostSessionMetrics(0, 0, str(tmp_path)),
    )
    monkeypatch.setattr(
        "autoskillit.execution.headless._headless_execute._capture_git_head_sha",
        lambda *a: "",
    )

    await _execute_claude_headless(
        spec,
        str(tmp_path),
        minimal_ctx,
        timeout=60,
        stale_threshold=30,
        completion_marker="%%TEST_MARKER%%",
    )

    backend.stream_parser.assert_called_once_with(completion_marker="%%TEST_MARKER%%")
