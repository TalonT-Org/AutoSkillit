"""Tests verifying the provider fallback loop in _execute_claude_headless.

Covers: STALE triggers fallback, BUDGET_EXHAUSTED triggers fallback,
no fallback_env suppresses retry, and empty provider (Anthropic) never falls back.
"""

from __future__ import annotations

from collections import deque
from unittest.mock import Mock

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
        call_kwargs: list[dict[str, object]] = []

        async def fake_runner(cmd, **kwargs):  # noqa: ARG001
            call_count[0] += 1
            call_kwargs.append(kwargs)
            return _sub_result

        setattr(fake_runner, "call_kwargs", call_kwargs)

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
    async def test_lifecycle_callables_bound_once_and_reused_across_retry(
        self, minimal_ctx, tmp_path, monkeypatch
    ):
        from autoskillit.execution.commands import ClaudeHeadlessCmd
        from autoskillit.execution.headless import _execute_claude_headless

        fake_runner, call_count = self._patch_common(
            monkeypatch,
            tmp_path,
            _make_queued_build_result(_STALE_RESULT, _SUCCESS_RESULT),
            ctx=minimal_ctx,
        )
        factory = Mock()
        normalizer = Mock()
        backend = _mock_backend(pty_required=True, channel_b_capable=True)
        backend.stream_parser_factory.return_value = factory
        backend.parent_candidate_normalizer.return_value = normalizer
        minimal_ctx.runner = fake_runner
        minimal_ctx.backend = backend

        await _execute_claude_headless(
            ClaudeHeadlessCmd(cmd=("echo", "test"), env={}),
            str(tmp_path),
            minimal_ctx,
            timeout=30.0,
            stale_threshold=5.0,
            completion_marker="%%ORDER_UP%%",
            provider_name="minimax",
            provider_fallback_env={"ANTHROPIC_API_KEY": "sk-test"},
            provider_fallback_name="anthropic",
        )

        assert call_count[0] == 2
        calls = getattr(fake_runner, "call_kwargs")
        assert len(calls) == 2
        assert all(call["stream_parser_factory"] is factory for call in calls)
        assert all(call["parent_candidate_normalizer"] is normalizer for call in calls)
        backend.stream_parser_factory.assert_called_once_with(completion_marker="%%ORDER_UP%%")
        backend.parent_candidate_normalizer.assert_called_once_with(
            completion_marker="%%ORDER_UP%%"
        )

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
