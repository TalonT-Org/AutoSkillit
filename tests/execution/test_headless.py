"""Tests for headless_runner.py extracted helpers."""

import json

import pytest

from autoskillit.config import AutomationConfig, ModelConfig
from autoskillit.core.types import ChannelConfirmation, SubprocessResult, TerminationReason


@pytest.fixture
def make_config():
    """Factory fixture that creates an AutomationConfig with custom model settings."""

    def _make(model_override=None, model_default=None):
        cfg = AutomationConfig()
        cfg.model = ModelConfig(default=model_default, override=model_override)
        return cfg

    return _make


def test_ensure_skill_prefix_prepends_use_for_slash_commands():
    from autoskillit.execution.headless import _ensure_skill_prefix

    assert _ensure_skill_prefix("/investigate foo") == "Use /investigate foo"


def test_ensure_skill_prefix_leaves_plain_text_unchanged():
    from autoskillit.execution.headless import _ensure_skill_prefix

    assert _ensure_skill_prefix("just a plain prompt") == "just a plain prompt"


def test_inject_completion_directive_appends_marker():
    from autoskillit.execution.headless import _inject_completion_directive

    result = _inject_completion_directive("/investigate foo", "%%DONE%%")
    assert "%%DONE%%" in result
    assert "/investigate foo" in result
    assert "ORCHESTRATION DIRECTIVE" in result


@pytest.mark.parametrize(
    "override,step,default,expected",
    [
        ("opus", "haiku", None, "opus"),  # override wins regardless of step
        (None, "haiku", None, "haiku"),  # step model used when no override/default
        (None, "", "sonnet", "sonnet"),  # config default fills empty step
        (None, "", None, None),  # all empty → None
    ],
)
def test_resolve_model_priority(make_config, override, step, default, expected):
    from autoskillit.execution.headless import _resolve_model

    cfg = make_config(model_override=override, model_default=default)
    assert _resolve_model(step, cfg) == expected


def _sr(returncode=0, stdout="", stderr="", termination=TerminationReason.NATURAL_EXIT):
    """Build a minimal SubprocessResult for _build_skill_result tests."""
    return SubprocessResult(returncode, stdout, stderr, termination, pid=12345)


class TestBuildSkillResult:
    """Coverage for _build_skill_result — the primary output-routing function."""

    def test_natural_exit_with_success_json_returns_success(self):
        """COMPLETED + valid type=result success JSON → success=True, needs_retry=False."""
        from autoskillit.execution.headless import _build_skill_result

        payload = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "Task completed.",
                "session_id": "sess-abc",
            }
        )
        skill = _build_skill_result(_sr(stdout=payload))
        assert skill.success is True
        assert skill.needs_retry is False

    def test_timed_out_returns_failure_no_retry(self):
        """TIMED_OUT termination → success=False, needs_retry=False (timeout is non-retriable)."""
        from autoskillit.execution.headless import _build_skill_result

        skill = _build_skill_result(_sr(returncode=-1, termination=TerminationReason.TIMED_OUT))
        assert skill.success is False
        assert skill.needs_retry is False

    def test_stale_with_valid_result_in_stdout_recovers(self):
        """STALE termination + valid result JSON in stdout → recovered_from_stale."""
        from autoskillit.execution.headless import _build_skill_result

        payload = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "Recovered output.",
                "session_id": "sess-stale",
            }
        )
        skill = _build_skill_result(
            _sr(returncode=-15, stdout=payload, termination=TerminationReason.STALE)
        )
        assert skill.success is True
        assert skill.subtype == "recovered_from_stale"

    def test_stale_with_empty_stdout_returns_failure_and_retry(self):
        """STALE termination + no result in stdout → success=False, needs_retry=True."""
        from autoskillit.execution.headless import _build_skill_result

        skill = _build_skill_result(
            _sr(returncode=-15, stdout="", termination=TerminationReason.STALE)
        )
        assert skill.success is False
        assert skill.needs_retry is True


class TestRecoverFromSeparateMarker:
    """Recovery path integration: marker in separate assistant message."""

    def _make_result(
        self,
        *,
        stdout: str,
        marker: str = "%%DONE%%",
        termination: TerminationReason = TerminationReason.NATURAL_EXIT,
        returncode: int = 0,
        channel: ChannelConfirmation = ChannelConfirmation.UNMONITORED,
        stderr: str = "",
    ):
        from autoskillit.execution.headless import _build_skill_result

        result = SubprocessResult(
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            termination=termination,
            pid=0,
            channel_confirmation=channel,
        )
        return _build_skill_result(result, completion_marker=marker)

    def test_recovery_yields_success_when_marker_in_separate_message(self):
        """CHANNEL_B + standalone marker in separate assistant msg → result text populated.

        Old code: success=True via CHANNEL_B bypass, result="" (recovery skipped by
        ``if not success`` gate). New code: recovery runs before _compute_outcome so the
        result field is populated from assistant message content.
        """
        msg1 = json.dumps(
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "Substantive work completed."}]},
            }
        )
        msg2 = json.dumps(
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "%%DONE%%"}]},
            }
        )
        result_rec = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "",
                "session_id": "s1",
            }
        )
        stdout = "\n".join([msg1, msg2, result_rec])

        skill = self._make_result(
            stdout=stdout, marker="%%DONE%%", channel=ChannelConfirmation.CHANNEL_B
        )
        assert skill.success is True
        assert "Substantive work completed." in skill.result

    def test_recovery_skipped_when_no_marker(self):
        """No completion_marker → _recover_from_separate_marker is not attempted."""
        msg1 = json.dumps(
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "Some output."}]},
            }
        )
        result_rec = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "",
                "session_id": "s1",
            }
        )
        stdout = "\n".join([msg1, result_rec])

        skill = self._make_result(stdout=stdout, marker="")
        assert skill.success is False  # empty result, no recovery possible

    def test_recovery_skipped_when_marker_inline(self):
        """Marker is inline in the result → _marker_is_standalone returns False → no recovery."""
        payload = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "Task done. %%DONE%% and more text.",
                "session_id": "s1",
            }
        )

        skill = self._make_result(stdout=payload, marker="%%DONE%%")
        assert skill.success is True  # marker found inline → success
        assert "%%DONE%%" not in skill.result  # marker stripped from result_text

    def test_recovery_fails_gracefully_when_only_marker_content(self):
        """Standalone marker message with no other substantive content → no recovery."""
        msg_only_marker = json.dumps(
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "%%DONE%%"}]},
            }
        )
        result_rec = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "",
                "session_id": "s1",
            }
        )
        stdout = "\n".join([msg_only_marker, result_rec])

        skill = self._make_result(stdout=stdout, marker="%%DONE%%")
        # Only the marker exists — stripped content is empty → _recover_from_separate_marker
        # returns None → no session replacement → success=False
        assert skill.success is False

    def test_recovery_fires_with_unmonitored_channel_and_realistic_cli_output(self):
        """UNMONITORED + assistant messages with standalone marker + empty result → success.

        Exercises the process-exits-first scenario: Channel B was never detected
        (UNMONITORED), but stdout contains type=assistant records with the marker
        on a standalone line. Recovery via _recover_from_separate_marker produces
        success=True.

        The marker occupies its own content block so that the newline-join fix
        (session.py) is required for _marker_is_standalone to return True.
        """
        msg_work = json.dumps(
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "Task completed successfully."}]},
            }
        )
        msg_marker = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "text", "text": "Signalling completion."},
                        {"type": "text", "text": "%%DONE%%"},
                    ]
                },
            }
        )
        result_rec = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "",
                "session_id": "s1",
            }
        )
        stdout = "\n".join([msg_work, msg_marker, result_rec])

        skill = self._make_result(
            stdout=stdout,
            marker="%%DONE%%",
            channel=ChannelConfirmation.UNMONITORED,
        )
        assert skill.success is True
        assert skill.needs_retry is False
        assert "Task completed successfully." in skill.result


class TestStaleRecoveryPipelineAdjudication:
    """STALE path must produce the same SkillResult values before and after the refactor."""

    def test_stale_with_valid_stdout_recovers(self):
        """STALE + valid session result in stdout → recovered_from_stale SkillResult."""
        from autoskillit.execution.headless import _build_skill_result

        payload = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "Stale recovery output.",
                "session_id": "stale-sess",
            }
        )
        skill = _build_skill_result(
            SubprocessResult(
                returncode=-15,
                stdout=payload,
                stderr="",
                termination=TerminationReason.STALE,
                pid=0,
            )
        )
        assert skill.success is True
        assert skill.subtype == "recovered_from_stale"
        assert skill.needs_retry is False

    def test_stale_without_valid_stdout_fails_retriable(self):
        """STALE + no valid result → stale SkillResult with success=False, needs_retry=True."""
        from autoskillit.execution.headless import _build_skill_result

        skill = _build_skill_result(
            SubprocessResult(
                returncode=-15,
                stdout="",
                stderr="",
                termination=TerminationReason.STALE,
                pid=0,
            )
        )
        assert skill.success is False
        assert skill.needs_retry is True
        assert skill.subtype == "stale"

    def test_stale_recovery_sets_correct_fields(self):
        """Recovered stale result has success=True, needs_retry=False, correct subtype."""
        from autoskillit.execution.headless import _build_skill_result

        payload = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "Stale recovery content.",
                "session_id": "sess-1",
            }
        )
        skill = _build_skill_result(
            SubprocessResult(
                returncode=-15,
                stdout=payload,
                stderr="",
                termination=TerminationReason.STALE,
                pid=0,
            )
        )
        assert skill.success is True
        assert skill.needs_retry is False
        assert skill.is_error is False
        assert skill.subtype == "recovered_from_stale"


class TestBuildSkillResultUsesComputeOutcome:
    """_build_skill_result derives success/needs_retry from _compute_outcome."""

    def test_success_maps_from_succeeded_outcome(self):
        """NATURAL_EXIT, returncode=0, valid result → success=True, needs_retry=False."""
        from autoskillit.execution.headless import _build_skill_result

        marker = "%%ORDER_UP%%"
        payload = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": f"Task done. {marker}",
                "session_id": "s1",
            }
        )
        skill = _build_skill_result(_sr(stdout=payload), completion_marker=marker)
        assert skill.success is True
        assert skill.needs_retry is False

    def test_needs_retry_maps_from_retriable_outcome(self):
        """error_max_turns session → success=False, needs_retry=True."""
        from autoskillit.execution.headless import _build_skill_result

        payload = json.dumps(
            {
                "type": "result",
                "subtype": "error_max_turns",
                "is_error": True,
                "result": "Reached max turns.",
                "session_id": "s1",
            }
        )
        skill = _build_skill_result(_sr(returncode=1, stdout=payload))
        assert skill.success is False
        assert skill.needs_retry is True

    def test_failed_maps_from_failed_outcome(self):
        """Timeout session → success=False, needs_retry=False."""
        from autoskillit.execution.headless import _build_skill_result

        skill = _build_skill_result(_sr(returncode=-1, termination=TerminationReason.TIMED_OUT))
        assert skill.success is False
        assert skill.needs_retry is False

    def test_contradiction_guard_inside_compute_outcome(self):
        """CHANNEL_B + error_max_turns → success=False, needs_retry=True (retry wins)."""
        from autoskillit.execution.headless import _build_skill_result

        payload = json.dumps(
            {
                "type": "result",
                "subtype": "error_max_turns",
                "is_error": True,
                "result": "Reached max turns.",
                "session_id": "s1",
            }
        )
        skill = _build_skill_result(
            SubprocessResult(
                returncode=1,
                stdout=payload,
                stderr="",
                termination=TerminationReason.NATURAL_EXIT,
                pid=0,
                channel_confirmation=ChannelConfirmation.CHANNEL_B,
            )
        )
        # Contradiction guard: CHANNEL_B bypass makes success=True, error_max_turns
        # makes needs_retry=True. Retry signal is authoritative → success=False.
        assert skill.success is False
        assert skill.needs_retry is True

    def test_dead_end_guard_escalates_channel_a(self):
        """Empty result + CHANNEL_A → needs_retry=True (escalated from dead end)."""
        from autoskillit.execution.headless import _build_skill_result

        payload = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "",
                "session_id": "s1",
            }
        )
        skill = _build_skill_result(
            SubprocessResult(
                returncode=0,
                stdout=payload,
                stderr="",
                termination=TerminationReason.NATURAL_EXIT,
                pid=0,
                channel_confirmation=ChannelConfirmation.CHANNEL_A,
            )
        )
        # Dead-end guard: success=False (empty result), needs_retry=False (CHANNEL_A
        # returns False from _compute_retry), but CHANNEL_A confirms completion →
        # escalate to needs_retry=True.
        assert skill.success is False
        assert skill.needs_retry is True


class TestRunHeadlessCore:
    """Integration test for run_headless_core via the injected mock runner."""

    @pytest.mark.anyio
    async def test_run_headless_core_returns_success_result(self, tool_ctx):
        from autoskillit.execution.headless import run_headless_core

        marker = tool_ctx.config.run_skill.completion_marker
        payload = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": f"Task completed. {marker}",
                "session_id": "sess-xyz",
            }
        )
        tool_ctx.runner.push(
            SubprocessResult(0, payload, "", TerminationReason.NATURAL_EXIT, pid=1)
        )
        result = await run_headless_core("/investigate foo", cwd="/tmp", ctx=tool_ctx)
        assert result.success is True
        assert result.needs_retry is False
        assert result.result == "Task completed."
