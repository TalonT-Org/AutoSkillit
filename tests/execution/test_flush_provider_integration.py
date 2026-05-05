"""Integration seam tests: provider fields forwarded from _execute_claude_headless to flush."""

from __future__ import annotations

import pytest

from autoskillit.core.types import RetryReason, SkillResult

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


_SUCCESS_RESULT = SkillResult(
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


def _patch_common(monkeypatch, tmp_path, skill_result, ctx):
    import autoskillit.execution.session_log as _sl_mod
    from autoskillit.execution.headless import PostSessionMetrics
    from tests.execution.conftest import _sr

    _sub_result = _sr()

    async def fake_runner(cmd, **kwargs):  # noqa: ARG001
        return _sub_result

    monkeypatch.setattr(
        "autoskillit.execution.headless._build_skill_result",
        lambda *a, **kw: skill_result,  # noqa: ARG005
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
        "autoskillit.execution.headless.collect_version_snapshot",
        lambda: {},
    )

    flush_calls: list[dict] = []

    def capture_flush(**kwargs):
        flush_calls.append(kwargs)

    monkeypatch.setattr(_sl_mod, "flush_session_log", capture_flush)
    return fake_runner, flush_calls


class TestProviderFieldsReachFlush:
    """Verify provider fields are forwarded from _execute_claude_headless to flush_session_log."""

    @pytest.mark.anyio
    async def test_normal_path_provider_used_in_flush_kwargs(
        self, minimal_ctx, tmp_path, monkeypatch
    ):
        from autoskillit.execution.commands import ClaudeHeadlessCmd
        from autoskillit.execution.headless import _execute_claude_headless

        fake_runner, flush_calls = _patch_common(
            monkeypatch, tmp_path, _SUCCESS_RESULT, minimal_ctx
        )
        minimal_ctx.runner = fake_runner  # type: ignore[assignment]

        await _execute_claude_headless(
            ClaudeHeadlessCmd(cmd=["echo", "test"], env={}),
            str(tmp_path),
            minimal_ctx,
            timeout=30.0,
            stale_threshold=5.0,
            provider_name="minimax",
            step_name="implement",
        )

        assert len(flush_calls) == 1
        outcome = flush_calls[0]["provider_outcome"]
        assert outcome.provider_used == "minimax"
        assert outcome.fallback_activated is False

    @pytest.mark.anyio
    async def test_normal_path_provider_fallback_in_flush_kwargs(
        self, minimal_ctx, tmp_path, monkeypatch
    ):
        from autoskillit.core.types import RetryReason, SkillResult
        from autoskillit.execution.commands import ClaudeHeadlessCmd
        from autoskillit.execution.headless import _execute_claude_headless

        stale = SkillResult(
            success=False,
            result="",
            session_id="s0",
            subtype="stale",
            is_error=False,
            exit_code=1,
            needs_retry=True,
            retry_reason=RetryReason.STALE,
            stderr="",
        )
        results = [stale, _SUCCESS_RESULT]
        call_count = [0]

        import autoskillit.execution.session_log as _sl_mod
        from autoskillit.execution.headless import PostSessionMetrics
        from tests.execution.conftest import _sr

        _sub_result = _sr()

        async def fake_runner(cmd, **kwargs):  # noqa: ARG001
            return _sub_result

        def build_result(*a, **kw):  # noqa: ARG001
            r = results[min(call_count[0], len(results) - 1)]
            call_count[0] += 1
            return r

        monkeypatch.setattr("autoskillit.execution.headless._build_skill_result", build_result)
        monkeypatch.setattr(
            "autoskillit.execution.headless._compute_post_session_metrics",
            lambda *a, **kw: PostSessionMetrics(0, 0, str(tmp_path)),  # noqa: ARG005
        )
        monkeypatch.setattr(
            "autoskillit.execution.headless._capture_git_head_sha",
            lambda *a: "",  # noqa: ARG005
        )
        monkeypatch.setattr(
            "autoskillit.execution.headless.collect_version_snapshot",
            lambda: {},
        )
        monkeypatch.setattr(minimal_ctx.config.providers, "provider_retry_limit", 2)
        monkeypatch.setattr(
            "autoskillit.execution.headless.is_feature_enabled",
            lambda name, *a, **kw: name == "providers",  # noqa: ARG005
        )

        flush_calls: list[dict] = []
        monkeypatch.setattr(_sl_mod, "flush_session_log", lambda **kw: flush_calls.append(kw))

        minimal_ctx.runner = fake_runner  # type: ignore[assignment]

        result = await _execute_claude_headless(
            ClaudeHeadlessCmd(cmd=["echo", "test"], env={}),
            str(tmp_path),
            minimal_ctx,
            timeout=30.0,
            stale_threshold=5.0,
            provider_name="minimax",
            provider_fallback_env={"ANTHROPIC_API_KEY": "sk-test"},
            provider_fallback_name="anthropic",
            step_name="implement",
        )

        assert result.provider_fallback is True
        assert result.provider_used == "anthropic"
        assert len(flush_calls) == 1
        outcome = flush_calls[0]["provider_outcome"]
        assert outcome.provider_used == "anthropic"
        assert outcome.fallback_activated is True

    @pytest.mark.anyio
    async def test_crash_path_provider_used_in_flush_kwargs(
        self, minimal_ctx, tmp_path, monkeypatch
    ):
        from autoskillit.execution.commands import ClaudeHeadlessCmd
        from autoskillit.execution.headless import _execute_claude_headless

        monkeypatch.setattr(
            "autoskillit.execution.headless.collect_version_snapshot",
            lambda: {},
        )

        flush_calls: list[dict] = []

        def capture_flush(**kwargs):
            flush_calls.append(kwargs)

        monkeypatch.setattr("autoskillit.execution.flush_session_log", capture_flush)

        async def raising_runner(cmd, **kwargs):  # noqa: ARG001
            raise RuntimeError("disk crash")

        minimal_ctx.runner = raising_runner  # type: ignore[assignment]

        result = await _execute_claude_headless(
            ClaudeHeadlessCmd(cmd=["echo", "test"], env={}),
            str(tmp_path),
            minimal_ctx,
            timeout=30.0,
            stale_threshold=5.0,
            provider_name="minimax",
        )

        assert result.subtype == "crashed"
        crashed_calls = [f for f in flush_calls if f.get("termination_reason") == "CRASHED"]
        assert len(crashed_calls) == 1
        outcome = crashed_calls[0]["provider_outcome"]
        assert outcome.provider_used == "minimax"

    @pytest.mark.anyio
    async def test_cancel_path_provider_used_in_flush_kwargs(
        self, minimal_ctx, tmp_path, monkeypatch
    ):
        import anyio

        from autoskillit.execution.commands import ClaudeHeadlessCmd
        from autoskillit.execution.headless import _execute_claude_headless

        monkeypatch.setattr(
            "autoskillit.execution.headless.collect_version_snapshot",
            lambda: {},
        )

        flush_calls: list[dict] = []

        def capture_flush(**kwargs):
            flush_calls.append(kwargs)

        monkeypatch.setattr("autoskillit.execution.flush_session_log", capture_flush)

        async def cancelling_runner(cmd, **kwargs):  # noqa: ARG001
            raise anyio.get_cancelled_exc_class()()

        minimal_ctx.runner = cancelling_runner  # type: ignore[assignment]

        with pytest.raises(BaseException):
            await _execute_claude_headless(
                ClaudeHeadlessCmd(cmd=["echo", "test"], env={}),
                str(tmp_path),
                minimal_ctx,
                timeout=30.0,
                stale_threshold=5.0,
                provider_name="openai",
            )

        cancelled_calls = [f for f in flush_calls if f.get("termination_reason") == "CANCELLED"]
        assert len(cancelled_calls) == 1
        outcome = cancelled_calls[0]["provider_outcome"]
        assert outcome.provider_used == "openai"
