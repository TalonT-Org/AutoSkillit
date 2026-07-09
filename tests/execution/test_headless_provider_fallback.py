"""Tests verifying the provider fallback loop in _execute_claude_headless.

Covers: STALE triggers fallback, BUDGET_EXHAUSTED triggers fallback,
no fallback_env suppresses retry, and empty provider (Anthropic) never falls back.
"""

from __future__ import annotations

import json
from collections import deque

import pytest

from autoskillit.core.types import RetryReason, SkillResult
from tests.execution.conftest import _mock_backend

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]

_PROVIDER_RETRY_LIMIT = 2

_STALE_RESULT = SkillResult(
    success=False,
    result="",
    session_id="s1",
    subtype="stale",
    is_error=False,
    exit_code=1,
    needs_retry=True,
    retry_reason=RetryReason.STALE,
    stderr="",
)

_BUDGET_EXHAUSTED_RESULT = SkillResult(
    success=False,
    result="",
    session_id="s1",
    subtype="budget_exhausted",
    is_error=False,
    exit_code=1,
    needs_retry=False,
    retry_reason=RetryReason.BUDGET_EXHAUSTED,
    stderr="",
)

_SUCCESS_RESULT = SkillResult(
    success=True,
    result="done",
    session_id="s2",
    subtype="success",
    is_error=False,
    exit_code=0,
    needs_retry=False,
    retry_reason=RetryReason.NONE,
    stderr="",
)


def _make_queued_build_result(*results: SkillResult):
    q: deque[SkillResult] = deque(results)

    def _build(*args, **kwargs):  # noqa: ARG001
        return q.popleft()

    return _build


class TestProviderFallbackLoop:
    def _patch_common(self, monkeypatch, tmp_path, build_result_fn, ctx=None):
        import autoskillit.execution.session_log as _sl_mod
        from autoskillit.execution.headless import PostSessionMetrics
        from tests.execution.conftest import _sr

        _sub_result = _sr()
        call_count: list[int] = [0]

        async def fake_runner(cmd, **kwargs):  # noqa: ARG001
            call_count[0] += 1
            return _sub_result

        if ctx is not None:
            monkeypatch.setattr(
                ctx.config.providers, "provider_retry_limit", _PROVIDER_RETRY_LIMIT
            )

        monkeypatch.setattr(
            "autoskillit.execution.headless._headless_execute._build_skill_result",
            build_result_fn,
        )
        monkeypatch.setattr(
            "autoskillit.execution.headless._headless_execute._compute_post_session_metrics",
            lambda *a, **kw: PostSessionMetrics(0, 0, str(tmp_path)),  # noqa: ARG005
        )
        monkeypatch.setattr(
            "autoskillit.execution.headless._headless_execute._capture_git_head_sha",
            lambda *a: "",  # noqa: ARG005
        )
        monkeypatch.setattr(
            "autoskillit.execution.headless._headless_execute.is_feature_enabled",
            lambda name, *a, **kw: name == "providers",  # noqa: ARG005
        )
        monkeypatch.setattr(
            "autoskillit.execution.headless._headless_execute.collect_version_snapshot",
            lambda backend=None: {},
        )
        monkeypatch.setattr(_sl_mod, "flush_session_log", lambda **kw: None)  # noqa: ARG005

        return fake_runner, call_count

    @pytest.mark.anyio
    async def test_stale_triggers_fallback(self, minimal_ctx, tmp_path, monkeypatch):
        from autoskillit.execution.commands import ClaudeHeadlessCmd
        from autoskillit.execution.headless import _execute_claude_headless

        fake_runner, call_count = self._patch_common(
            monkeypatch,
            tmp_path,
            _make_queued_build_result(_STALE_RESULT, _SUCCESS_RESULT),
            ctx=minimal_ctx,
        )
        minimal_ctx.runner = fake_runner
        minimal_ctx.backend = _mock_backend(pty_required=True, channel_b_capable=True)

        result = await _execute_claude_headless(
            ClaudeHeadlessCmd(cmd=("echo", "test"), env={}),
            str(tmp_path),
            minimal_ctx,
            timeout=30.0,
            stale_threshold=5.0,
            provider_name="minimax",
            provider_fallback_env={"ANTHROPIC_API_KEY": "sk-test"},
            provider_fallback_name="anthropic",
        )

        assert call_count[0] == 2
        assert result.provider.fallback_activated is True
        assert result.provider.provider_used == "anthropic"

    @pytest.mark.anyio
    async def test_budget_exhausted_triggers_fallback(self, minimal_ctx, tmp_path, monkeypatch):
        from autoskillit.execution.commands import ClaudeHeadlessCmd
        from autoskillit.execution.headless import _execute_claude_headless

        fake_runner, call_count = self._patch_common(
            monkeypatch,
            tmp_path,
            _make_queued_build_result(_BUDGET_EXHAUSTED_RESULT, _SUCCESS_RESULT),
            ctx=minimal_ctx,
        )
        minimal_ctx.runner = fake_runner
        minimal_ctx.backend = _mock_backend(pty_required=True, channel_b_capable=True)

        result = await _execute_claude_headless(
            ClaudeHeadlessCmd(cmd=("echo", "test"), env={}),
            str(tmp_path),
            minimal_ctx,
            timeout=30.0,
            stale_threshold=5.0,
            provider_name="minimax",
            provider_fallback_env={"ANTHROPIC_API_KEY": "sk-test"},
            provider_fallback_name="anthropic",
        )

        assert call_count[0] == 2
        assert result.provider.fallback_activated is True
        assert result.provider.provider_used == "anthropic"

    @pytest.mark.anyio
    async def test_codex_fallback_resets_stream_parser_and_liveness_supervisor_per_attempt(
        self,
        minimal_ctx,
        tmp_path,
        monkeypatch,
    ):
        import autoskillit.execution.process as process_mod
        from autoskillit.execution.backends import CodexBackend
        from autoskillit.execution.commands import ClaudeHeadlessCmd
        from autoskillit.execution.headless import _execute_claude_headless
        from autoskillit.execution.process import ProcessLivenessSupervisor
        from tests.execution.conftest import _sr

        self._patch_common(
            monkeypatch,
            tmp_path,
            _make_queued_build_result(_STALE_RESULT, _SUCCESS_RESULT),
            ctx=minimal_ctx,
        )
        attempt_records: list[dict[str, object]] = []
        supervisors: list[ProcessLivenessSupervisor] = []
        original_liveness_context = process_mod.process_liveness_context

        def observed_liveness_context(supervisor: ProcessLivenessSupervisor):
            supervisors.append(supervisor)
            return original_liveness_context(supervisor)

        monkeypatch.setattr(process_mod, "process_liveness_context", observed_liveness_context)

        async def fake_runner(cmd, **kwargs):  # noqa: ARG001
            parser = kwargs["stream_parser"]
            supervisor = supervisors[-1]
            assert supervisor is not None
            record: dict[str, object] = {
                "supervisor": supervisor,
                "parser": parser,
                "operations_before": set(supervisor.operations),
                "in_flight_before": supervisor.in_flight_operation(),
            }
            attempt_records.append(record)

            if len(attempt_records) == 1:
                for payload in (
                    {"type": "thread.started", "thread_id": "codex-attempt-1"},
                    {
                        "type": "item.started",
                        "item": {
                            "id": "mcp-1",
                            "type": "mcp_tool_call",
                            "name": "open_kitchen",
                        },
                    },
                    {
                        "type": "item.updated",
                        "status": "in_progress",
                        "item": {
                            "id": "mcp-1",
                            "type": "mcp_tool_call",
                            "name": "open_kitchen",
                            "status": "in_progress",
                        },
                    },
                ):
                    event = parser.parse_line(json.dumps(payload))
                    assert event is not None
                    supervisor.publish_event(event)
                record["operations_after"] = set(supervisor.operations)
                record["in_flight_after"] = supervisor.in_flight_operation()

            return _sr()

        minimal_ctx.runner = fake_runner
        minimal_ctx.backend = CodexBackend()

        result = await _execute_claude_headless(
            ClaudeHeadlessCmd(cmd=("codex", "exec", "test"), env={}),
            str(tmp_path),
            minimal_ctx,
            timeout=30.0,
            stale_threshold=5.0,
            provider_name="minimax",
            provider_fallback_env={"ANTHROPIC_API_KEY": "sk-test"},
            provider_fallback_name="anthropic",
        )

        assert result.provider.fallback_activated is True
        assert result.provider.provider_used == "anthropic"
        assert len(attempt_records) >= 2
        first, second = attempt_records[0], attempt_records[1]
        assert first["supervisor"] is not second["supervisor"]
        assert first["parser"] is not second["parser"]
        assert first["operations_before"] == set()
        assert first["operations_after"] == {"mcp-1"}
        assert first["in_flight_after"] is True
        assert second["operations_before"] == set()
        assert second["in_flight_before"] is False

    @pytest.mark.anyio
    async def test_no_fallback_env_suppresses_retry(self, minimal_ctx, tmp_path, monkeypatch):
        from autoskillit.execution.commands import ClaudeHeadlessCmd
        from autoskillit.execution.headless import _execute_claude_headless

        fake_runner, call_count = self._patch_common(
            monkeypatch,
            tmp_path,
            _make_queued_build_result(_STALE_RESULT),
        )
        minimal_ctx.runner = fake_runner
        minimal_ctx.backend = _mock_backend(pty_required=True, channel_b_capable=True)

        result = await _execute_claude_headless(
            ClaudeHeadlessCmd(cmd=("echo", "test"), env={}),
            str(tmp_path),
            minimal_ctx,
            timeout=30.0,
            stale_threshold=5.0,
            provider_name="minimax",
        )

        assert call_count[0] == 1
        assert result.provider.fallback_activated is False

    @pytest.mark.anyio
    async def test_anthropic_provider_never_falls_back(self, minimal_ctx, tmp_path, monkeypatch):
        from autoskillit.execution.commands import ClaudeHeadlessCmd
        from autoskillit.execution.headless import _execute_claude_headless

        fake_runner, call_count = self._patch_common(
            monkeypatch,
            tmp_path,
            _make_queued_build_result(_STALE_RESULT),
            ctx=minimal_ctx,
        )
        minimal_ctx.runner = fake_runner
        minimal_ctx.backend = _mock_backend(pty_required=True, channel_b_capable=True)

        result = await _execute_claude_headless(
            ClaudeHeadlessCmd(cmd=("echo", "test"), env={}),
            str(tmp_path),
            minimal_ctx,
            timeout=30.0,
            stale_threshold=5.0,
            provider_name="",
            provider_fallback_env={"ANTHROPIC_API_KEY": "sk-test"},
            provider_fallback_name="anthropic",
        )

        assert call_count[0] == 1
        assert result.provider.fallback_activated is False
