"""Tests verifying the provider fallback loop in _execute_claude_headless.

Covers: STALE triggers fallback, BUDGET_EXHAUSTED triggers fallback,
no fallback_env suppresses retry, and empty provider (Anthropic) never falls back.
"""

from __future__ import annotations

from collections import deque

import pytest

from autoskillit.core.types import RetryReason, SkillResult

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]

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
    def _patch_common(self, monkeypatch, tmp_path, build_result_fn):
        import autoskillit.execution.session_log as _sl_mod
        from autoskillit.execution.headless import PostSessionMetrics
        from tests.execution.conftest import _sr

        _sub_result = _sr()
        call_count: list[int] = [0]

        async def fake_runner(cmd, **kwargs):  # noqa: ARG001
            call_count[0] += 1
            return _sub_result

        monkeypatch.setattr(
            "autoskillit.execution.headless._build_skill_result",
            build_result_fn,
        )
        monkeypatch.setattr(
            "autoskillit.execution.headless._compute_post_session_metrics",
            lambda *a, **kw: PostSessionMetrics(0, 0, str(tmp_path)),  # noqa: ARG005
        )
        monkeypatch.setattr(
            "autoskillit.execution.headless._capture_git_head_sha",
            lambda *a: "",  # noqa: ARG005
        )
        monkeypatch.setattr(
            "autoskillit.execution.headless.is_feature_enabled",
            lambda name, *a, **kw: name == "providers",  # noqa: ARG005
        )
        monkeypatch.setattr(
            "autoskillit.execution.headless.collect_version_snapshot",
            lambda: {},
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
        )
        minimal_ctx.runner = fake_runner

        result = await _execute_claude_headless(
            ClaudeHeadlessCmd(cmd=["echo", "test"], env={}),
            str(tmp_path),
            minimal_ctx,
            timeout=30.0,
            stale_threshold=5.0,
            provider_name="minimax",
            provider_fallback_env={"ANTHROPIC_API_KEY": "sk-test"},
            provider_fallback_name="anthropic",
        )

        assert call_count[0] == 2
        assert result.provider_fallback is True
        assert result.provider_used == "anthropic"

    @pytest.mark.anyio
    async def test_budget_exhausted_triggers_fallback(self, minimal_ctx, tmp_path, monkeypatch):
        from autoskillit.execution.commands import ClaudeHeadlessCmd
        from autoskillit.execution.headless import _execute_claude_headless

        fake_runner, call_count = self._patch_common(
            monkeypatch,
            tmp_path,
            _make_queued_build_result(_BUDGET_EXHAUSTED_RESULT, _SUCCESS_RESULT),
        )
        minimal_ctx.runner = fake_runner

        result = await _execute_claude_headless(
            ClaudeHeadlessCmd(cmd=["echo", "test"], env={}),
            str(tmp_path),
            minimal_ctx,
            timeout=30.0,
            stale_threshold=5.0,
            provider_name="minimax",
            provider_fallback_env={"ANTHROPIC_API_KEY": "sk-test"},
            provider_fallback_name="anthropic",
        )

        assert call_count[0] == 2
        assert result.provider_fallback is True
        assert result.provider_used == "anthropic"

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

        result = await _execute_claude_headless(
            ClaudeHeadlessCmd(cmd=["echo", "test"], env={}),
            str(tmp_path),
            minimal_ctx,
            timeout=30.0,
            stale_threshold=5.0,
            provider_name="minimax",
        )

        assert call_count[0] == 1
        assert result.provider_fallback is False

    @pytest.mark.anyio
    async def test_anthropic_provider_never_falls_back(self, minimal_ctx, tmp_path, monkeypatch):
        from autoskillit.execution.commands import ClaudeHeadlessCmd
        from autoskillit.execution.headless import _execute_claude_headless

        fake_runner, call_count = self._patch_common(
            monkeypatch,
            tmp_path,
            _make_queued_build_result(_STALE_RESULT),
        )
        minimal_ctx.runner = fake_runner

        result = await _execute_claude_headless(
            ClaudeHeadlessCmd(cmd=["echo", "test"], env={}),
            str(tmp_path),
            minimal_ctx,
            timeout=30.0,
            stale_threshold=5.0,
            provider_name="",
        )

        assert call_count[0] == 1
        assert result.provider_fallback is False
