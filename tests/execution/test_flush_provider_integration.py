"""Integration seam tests: provider fields forwarded from _execute_claude_headless to flush."""

from __future__ import annotations

import json

import pytest

import autoskillit.execution.headless._headless_execute as _patch_headless__headless_execute
from autoskillit.core.types import RetryReason, SkillResult
from tests.execution.conftest import _launch_preparation, _mock_backend
from tests.execution.test_outcome_invariants import _resolve_review_contract

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


def _launch_kwargs(ctx, cwd: str) -> dict[str, object]:
    return {
        "launch_resolver": ctx.launch_resolver,
        "launch_preparation": _launch_preparation(ctx, cwd=cwd),
    }


def _patch_common(monkeypatch, tmp_path, skill_result, ctx):
    import autoskillit.execution.session_log.session_log as _sl_mod
    from autoskillit.execution.headless import PostSessionMetrics
    from tests.execution.conftest import _sr

    _sub_result = _sr()

    async def fake_runner(cmd, **kwargs):  # noqa: ARG001
        return _sub_result

    monkeypatch.setattr(
        _patch_headless__headless_execute,
        "_build_skill_result",
        lambda *a, **kw: skill_result,  # noqa: ARG005
    )
    monkeypatch.setattr(
        _patch_headless__headless_execute,
        "_compute_post_session_metrics",
        lambda *a, **kw: PostSessionMetrics(0, 0, str(tmp_path)),  # noqa: ARG005
    )
    monkeypatch.setattr(
        _patch_headless__headless_execute,
        "_capture_git_head_sha",
        lambda *a: "",  # noqa: ARG005
    )
    monkeypatch.setattr(
        _patch_headless__headless_execute,
        "collect_version_snapshot",
        lambda backend=None: {},
    )

    flush_calls: list[dict] = []

    def capture_flush(**kwargs):
        flush_calls.append(kwargs)

    monkeypatch.setattr(_sl_mod, "flush_session_log", capture_flush)
    return fake_runner, flush_calls


class TestProviderFieldsReachFlush:
    """Verify provider fields are forwarded from _execute_claude_headless to flush_session_log."""

    @pytest.mark.parametrize(
        ("provider_name", "expected"),
        [("minimax", "minimax"), ("", "anthropic")],
    )
    @pytest.mark.anyio
    async def test_normal_path_provider_used_in_flush_kwargs(
        self, minimal_ctx, tmp_path, monkeypatch, provider_name: str, expected: str
    ):
        from autoskillit.execution.headless import _execute_claude_headless
        from autoskillit.execution.runtime.commands import ClaudeHeadlessCmd

        fake_runner, flush_calls = _patch_common(
            monkeypatch, tmp_path, _SUCCESS_RESULT, minimal_ctx
        )
        minimal_ctx.runner = fake_runner  # type: ignore[assignment]
        minimal_ctx.backend = _mock_backend(pty_required=True, channel_b_capable=True)

        await _execute_claude_headless(
            lambda _binding, _extras: ClaudeHeadlessCmd(cmd=("echo", "test"), env={}),
            str(tmp_path),
            minimal_ctx,
            timeout=30.0,
            stale_threshold=5.0,
            provider_name=provider_name,
            step_name="implement",
            **_launch_kwargs(minimal_ctx, str(tmp_path)),
        )

        assert len(flush_calls) == 1
        outcome = flush_calls[0]["provider_outcome"]
        assert outcome.provider_used == expected
        assert outcome.fallback_activated is False

    @pytest.mark.anyio
    async def test_post_start_stale_does_not_switch_provider_in_flush_kwargs(
        self, minimal_ctx, tmp_path, monkeypatch
    ):
        from autoskillit.core.types import RetryReason, SkillResult
        from autoskillit.execution.headless import _execute_claude_headless
        from autoskillit.execution.runtime.commands import ClaudeHeadlessCmd

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

        import autoskillit.execution.session_log.session_log as _sl_mod
        from autoskillit.execution.headless import PostSessionMetrics
        from tests.execution.conftest import _sr

        _sub_result = _sr()

        async def fake_runner(cmd, **kwargs):  # noqa: ARG001
            return _sub_result

        def build_result(*a, **kw):  # noqa: ARG001
            r = results[min(call_count[0], len(results) - 1)]
            call_count[0] += 1
            return r

        monkeypatch.setattr(_patch_headless__headless_execute, "_build_skill_result", build_result)
        monkeypatch.setattr(
            _patch_headless__headless_execute,
            "_compute_post_session_metrics",
            lambda *a, **kw: PostSessionMetrics(0, 0, str(tmp_path)),  # noqa: ARG005
        )
        monkeypatch.setattr(
            _patch_headless__headless_execute,
            "_capture_git_head_sha",
            lambda *a: "",  # noqa: ARG005
        )
        monkeypatch.setattr(
            _patch_headless__headless_execute,
            "collect_version_snapshot",
            lambda backend=None: {},
        )
        flush_calls: list[dict] = []
        monkeypatch.setattr(_sl_mod, "flush_session_log", lambda **kw: flush_calls.append(kw))

        minimal_ctx.runner = fake_runner  # type: ignore[assignment]
        minimal_ctx.backend = _mock_backend(pty_required=True, channel_b_capable=True)

        result = await _execute_claude_headless(
            lambda _binding, _extras: ClaudeHeadlessCmd(cmd=("echo", "test"), env={}),
            str(tmp_path),
            minimal_ctx,
            timeout=30.0,
            stale_threshold=5.0,
            provider_name="minimax",
            step_name="implement",
            **_launch_kwargs(minimal_ctx, str(tmp_path)),
        )

        assert result.provider.fallback_activated is False
        assert result.provider.provider_used == "minimax"
        assert call_count == [1]
        assert len(flush_calls) == 1
        outcome = flush_calls[0]["provider_outcome"]
        assert outcome.provider_used == "minimax"
        assert outcome.fallback_activated is False

    @pytest.mark.parametrize(
        ("provider_name", "expected"),
        [("minimax", "minimax"), ("", "anthropic")],
    )
    @pytest.mark.anyio
    async def test_crash_path_provider_used_in_flush_kwargs(
        self, minimal_ctx, tmp_path, monkeypatch, provider_name: str, expected: str
    ):
        from autoskillit.execution.headless import _execute_claude_headless
        from autoskillit.execution.runtime.commands import ClaudeHeadlessCmd

        monkeypatch.setattr(
            _patch_headless__headless_execute,
            "collect_version_snapshot",
            lambda backend=None: {},
        )

        flush_calls: list[dict] = []

        def capture_flush(**kwargs):
            flush_calls.append(kwargs)

        monkeypatch.setattr("autoskillit.execution.flush_session_log", capture_flush)

        async def raising_runner(cmd, **kwargs):  # noqa: ARG001
            raise RuntimeError("disk crash")

        minimal_ctx.runner = raising_runner  # type: ignore[assignment]
        minimal_ctx.backend = _mock_backend(pty_required=True, channel_b_capable=True)

        result = await _execute_claude_headless(
            lambda _binding, _extras: ClaudeHeadlessCmd(cmd=("echo", "test"), env={}),
            str(tmp_path),
            minimal_ctx,
            timeout=30.0,
            stale_threshold=5.0,
            provider_name=provider_name,
            **_launch_kwargs(minimal_ctx, str(tmp_path)),
        )

        assert result.subtype == "crashed"
        crashed_calls = [f for f in flush_calls if f.get("termination_reason") == "CRASHED"]
        assert len(crashed_calls) == 1
        outcome = crashed_calls[0]["provider_outcome"]
        assert outcome.provider_used == expected
        assert "comm_aliases" in crashed_calls[0]

    @pytest.mark.parametrize(
        ("provider_name", "expected"),
        [("openai", "openai"), ("", "anthropic")],
    )
    @pytest.mark.anyio
    async def test_cancel_path_provider_used_in_flush_kwargs(
        self, minimal_ctx, tmp_path, monkeypatch, provider_name: str, expected: str
    ):
        import anyio

        from autoskillit.execution.headless import _execute_claude_headless
        from autoskillit.execution.runtime.commands import ClaudeHeadlessCmd

        monkeypatch.setattr(
            _patch_headless__headless_execute,
            "collect_version_snapshot",
            lambda backend=None: {},
        )

        flush_calls: list[dict] = []

        def capture_flush(**kwargs):
            flush_calls.append(kwargs)

        monkeypatch.setattr("autoskillit.execution.flush_session_log", capture_flush)

        async def cancelling_runner(cmd, **kwargs):  # noqa: ARG001
            raise anyio.get_cancelled_exc_class()()

        minimal_ctx.runner = cancelling_runner  # type: ignore[assignment]
        minimal_ctx.backend = _mock_backend()

        with pytest.raises(anyio.get_cancelled_exc_class()):
            await _execute_claude_headless(
                lambda _binding, _extras: ClaudeHeadlessCmd(cmd=("echo", "test"), env={}),
                str(tmp_path),
                minimal_ctx,
                timeout=30.0,
                stale_threshold=5.0,
                provider_name=provider_name,
                **_launch_kwargs(minimal_ctx, str(tmp_path)),
            )

        cancelled_calls = [f for f in flush_calls if f.get("termination_reason") == "CANCELLED"]
        assert len(cancelled_calls) == 1
        outcome = cancelled_calls[0]["provider_outcome"]
        assert outcome.provider_used == expected
        assert "comm_aliases" in cancelled_calls[0]

    @pytest.mark.anyio
    async def test_model_identifier_reaches_flush_session_log(
        self, minimal_ctx, tmp_path, monkeypatch
    ):
        """model_identity must be forwarded to flush_session_log — not silently dropped."""
        from autoskillit.core.types._type_results import ModelIdentity
        from autoskillit.execution.headless import _execute_claude_headless
        from autoskillit.execution.runtime.commands import ClaudeHeadlessCmd

        fake_runner, flush_calls = _patch_common(
            monkeypatch, tmp_path, _SUCCESS_RESULT, minimal_ctx
        )
        minimal_ctx.runner = fake_runner  # type: ignore[assignment]
        minimal_ctx.backend = _mock_backend()

        await _execute_claude_headless(
            lambda _binding, _extras: ClaudeHeadlessCmd(cmd=("echo", "test"), env={}),
            str(tmp_path),
            minimal_ctx,
            timeout=30.0,
            stale_threshold=5.0,
            step_name="implement",
            model_identity=ModelIdentity.anthropic("claude-opus-4-6"),
            **_launch_kwargs(minimal_ctx, str(tmp_path)),
        )

        assert len(flush_calls) == 1
        assert flush_calls[0]["model_identity"].configured_model == "claude-opus-4-6"

    @pytest.mark.anyio
    async def test_invariant_verdict_reaches_the_real_session_log_flush(
        self, minimal_ctx, tmp_path, monkeypatch
    ) -> None:
        """The terminal-builder payload remains compatible with the actual index writer."""
        import autoskillit.execution.session_log.session_log as session_log
        from autoskillit.execution.headless import PostSessionMetrics, _execute_claude_headless
        from autoskillit.execution.runtime.commands import ClaudeHeadlessCmd
        from tests.execution.conftest import _sr

        expected_fields = {"accept_count": 2, "fix_failures": 1}
        expected_detail = "invariant violated: when 'accept_count > 0' require 'fix_failures == 0'"
        contract = _resolve_review_contract()
        raw_result = _sr(
            stdout=json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "result": "accept_count = 2\nfix_failures = 1",
                    "session_id": "invariant-flush",
                }
            )
        )

        async def fake_runner(cmd, **kwargs):  # noqa: ARG001
            return raw_result

        monkeypatch.setattr(
            _patch_headless__headless_execute,
            "_compute_post_session_metrics",
            lambda *a, **kw: PostSessionMetrics(0, 0, str(tmp_path)),  # noqa: ARG005
        )
        monkeypatch.setattr(
            _patch_headless__headless_execute,
            "_capture_git_head_sha",
            lambda *a: "",  # noqa: ARG005
        )
        monkeypatch.setattr(
            _patch_headless__headless_execute,
            "collect_version_snapshot",
            lambda backend=None: {},
        )
        real_flush = session_log.flush_session_log
        captured_flushes: list[dict] = []
        monkeypatch.setattr(
            session_log,
            "flush_session_log",
            lambda **kwargs: captured_flushes.append(kwargs),
        )
        minimal_ctx.runner = fake_runner  # type: ignore[assignment]
        minimal_ctx.backend = _mock_backend()

        result = await _execute_claude_headless(
            lambda _binding, _extras: ClaudeHeadlessCmd(cmd=("echo", "test"), env={}),
            str(tmp_path),
            minimal_ctx,
            timeout=30.0,
            stale_threshold=5.0,
            skill_contract=contract,
            step_name="implement",
            **_launch_kwargs(minimal_ctx, str(tmp_path)),
        )

        assert result.subtype == "outcome_invariant_violation"
        assert len(captured_flushes) == 1
        flush_kwargs = captured_flushes[0]
        expected_verdict = {
            "reason_kind": RetryReason.OUTCOME_INVARIANT.value,
            "subtype": "outcome_invariant_violation",
            "detail": expected_detail,
            "outcome_fields": expected_fields,
            "defects": [],
        }
        assert flush_kwargs["adjudication_verdict"] == expected_verdict
        flush_kwargs["log_dir"] = str(tmp_path / "logs")
        real_flush(**flush_kwargs)

        entry = json.loads((tmp_path / "logs" / "sessions.jsonl").read_text())
        assert entry["adjudication_verdict"] == expected_verdict
        assert entry["outcome_fields"] == expected_fields
