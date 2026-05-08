"""Tests for _compute_outcome, content state evaluation, and session adjudication consistency."""

from __future__ import annotations

import json
from collections.abc import Callable

import pytest

from autoskillit.core.types import (
    ChannelConfirmation,
    RetryReason,
    SessionOutcome,
    TerminationReason,
)
from autoskillit.execution.session import (
    ClaudeSessionResult,
    _check_expected_patterns,
    _check_session_content,
    _compute_outcome,
    _compute_retry,
    _compute_success,
    parse_session_result,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


class TestAdjudicationConsistency:
    """Contract documentation for known-impossible adjudication states.

    These tests intentionally cover (returncode, termination) combinations that
    cannot occur in production. They serve as specification — documenting that the
    adjudicator is deterministic and exhaustive, not that these paths are reachable.
    """

    @pytest.mark.parametrize(
        "termination,channel,result_content,returncode,subtype,is_error,"
        "expected_success,expected_retry,completion_marker",
        [
            # NATURAL_EXIT + CHANNEL_A: dead-end state
            # (known bug, corrected by guard in _build_skill_result)
            (
                TerminationReason.NATURAL_EXIT,
                ChannelConfirmation.CHANNEL_A,
                "",
                0,
                "success",
                False,
                False,
                False,
                "",
            ),
            # NATURAL_EXIT + CHANNEL_A: valid success with content
            (
                TerminationReason.NATURAL_EXIT,
                ChannelConfirmation.CHANNEL_A,
                "done",
                0,
                "success",
                False,
                True,
                False,
                "",
            ),
            # NATURAL_EXIT + CHANNEL_B: contradiction
            # (known bug, corrected by guard in _build_skill_result)
            (
                TerminationReason.NATURAL_EXIT,
                ChannelConfirmation.CHANNEL_B,
                "",
                0,
                "error_max_turns",
                True,
                True,
                True,
                "",
            ),
            # NATURAL_EXIT + CHANNEL_B: valid success
            (
                TerminationReason.NATURAL_EXIT,
                ChannelConfirmation.CHANNEL_B,
                "",
                0,
                "success",
                False,
                True,
                False,
                "",
            ),
            (
                TerminationReason.NATURAL_EXIT,
                ChannelConfirmation.CHANNEL_B,
                "done",
                0,
                "success",
                False,
                True,
                False,
                "",
            ),
            # COMPLETED + CHANNEL_A: valid retriable (kill anomaly)
            (
                TerminationReason.COMPLETED,
                ChannelConfirmation.CHANNEL_A,
                "",
                -15,
                "success",
                False,
                False,
                True,
                "",
            ),
            # COMPLETED + CHANNEL_B: valid success
            (
                TerminationReason.COMPLETED,
                ChannelConfirmation.CHANNEL_B,
                "",
                -15,
                "success",
                False,
                True,
                False,
                "",
            ),
            # COMPLETED + CHANNEL_B: contradiction
            # (known bug, corrected by guard in _build_skill_result)
            (
                TerminationReason.COMPLETED,
                ChannelConfirmation.CHANNEL_B,
                "",
                -15,
                "error_max_turns",
                True,
                True,
                True,
                "",
            ),
            # UNMONITORED baselines — all should already be valid states
            (
                TerminationReason.NATURAL_EXIT,
                ChannelConfirmation.UNMONITORED,
                "",
                0,
                "success",
                False,
                False,
                True,
                "",
            ),
            (
                TerminationReason.NATURAL_EXIT,
                ChannelConfirmation.UNMONITORED,
                "done",
                0,
                "success",
                False,
                True,
                False,
                "",
            ),
            (
                TerminationReason.TIMED_OUT,
                ChannelConfirmation.UNMONITORED,
                "",
                -1,
                "timeout",
                True,
                False,
                False,
                "",
            ),
            (
                TerminationReason.STALE,
                ChannelConfirmation.UNMONITORED,
                "",
                0,
                "success",
                False,
                False,
                False,
                "",
            ),
            # NATURAL_EXIT + UNMONITORED: substantive result without marker
            # (premature exit / early stop — retriable via EARLY_STOP)
            pytest.param(
                TerminationReason.NATURAL_EXIT,
                ChannelConfirmation.UNMONITORED,
                "Here is the PR context block with substantive content...",
                0,
                "success",
                False,
                False,
                True,
                "%%ORDER_UP%%",
                id="natural_exit-unmonitored-substantive_result_no_marker",
            ),
        ],
    )
    def test_raw_adjudication_pair(
        self,
        termination: TerminationReason,
        channel: ChannelConfirmation,
        result_content: str,
        returncode: int,
        subtype: str,
        is_error: bool,
        expected_success: bool,
        expected_retry: bool,
        completion_marker: str,
    ) -> None:
        """Document exact raw outputs of the individual adjudication functions.

        Known bad states (dead end, contradiction) are documented as expected values
        rather than as invariant failures — the guards in _build_skill_result correct
        those states before they reach the orchestrator.
        """
        session = ClaudeSessionResult(
            subtype=subtype,
            result=result_content,
            is_error=is_error,
            session_id="cross-val",
            errors=[],
        )
        success = _compute_success(
            session,
            returncode,
            termination,
            channel_confirmation=channel,
            completion_marker=completion_marker,
        )
        needs_retry, _ = _compute_retry(
            session,
            returncode,
            termination,
            channel_confirmation=channel,
            completion_marker=completion_marker,
        )
        assert success == expected_success
        assert needs_retry == expected_retry

    def test_premature_exit_substantive_result_no_marker_is_early_stop(self) -> None:
        """NATURAL_EXIT + UNMONITORED + substantive result without marker → EARLY_STOP.

        When the model produces substantive output but stops before emitting
        the completion marker, the session is classified as retriable with
        EARLY_STOP reason. This is the text-then-tool boundary fix.
        """
        session = ClaudeSessionResult(
            subtype="success",
            result="Here is the PR context block with substantive content...",
            is_error=False,
            session_id="premature",
            errors=[],
        )
        success = _compute_success(
            session,
            returncode=0,
            termination=TerminationReason.NATURAL_EXIT,
            channel_confirmation=ChannelConfirmation.UNMONITORED,
            completion_marker="%%ORDER_UP%%",
        )
        needs_retry, reason = _compute_retry(
            session,
            returncode=0,
            termination=TerminationReason.NATURAL_EXIT,
            channel_confirmation=ChannelConfirmation.UNMONITORED,
            completion_marker="%%ORDER_UP%%",
        )
        assert success is False
        assert needs_retry is True
        assert reason == RetryReason.EARLY_STOP

    def test_channel_a_empty_result_raw_dead_end(self) -> None:
        """Document: NATURAL_EXIT + CHANNEL_A + empty result is a dead end at raw function level.

        Both _compute_success and _compute_retry return False for this combination:
        - _compute_success: CHANNEL_A falls through content check, empty result → False
        - _compute_retry: NATURAL_EXIT + CHANNEL_A confirmation suppresses retry → False

        The composition guard in _build_skill_result escalates this to retriable.
        See TestAdjudicationGuards.test_channel_a_empty_result_not_dead_end.
        """
        session = ClaudeSessionResult(
            subtype="success",
            result="",
            is_error=False,
            session_id="regression",
            errors=[],
        )
        success = _compute_success(
            session,
            returncode=0,
            termination=TerminationReason.NATURAL_EXIT,
            channel_confirmation=ChannelConfirmation.CHANNEL_A,
        )
        needs_retry, _ = _compute_retry(
            session,
            returncode=0,
            termination=TerminationReason.NATURAL_EXIT,
            channel_confirmation=ChannelConfirmation.CHANNEL_A,
        )
        # Raw functions produce the dead-end state — this is the known bug at the
        # individual function level, corrected at the _build_skill_result boundary.
        assert success is False
        assert needs_retry is False


class TestEarlyStop:
    """Tests for EARLY_STOP retry classification."""

    def test_natural_exit_substantive_content_no_marker_is_retriable(self) -> None:
        """A session that exits cleanly with substantive output but without
        the completion marker should be classified as RETRIABLE with EARLY_STOP,
        because the model may have stopped early at a text-then-tool boundary.
        """
        session = ClaudeSessionResult(
            subtype="success",
            result="Here is the PR context block with substantive content...",
            is_error=False,
            session_id="early-stop",
            errors=[],
        )
        outcome, reason = _compute_outcome(
            session,
            returncode=0,
            termination=TerminationReason.NATURAL_EXIT,
            completion_marker="%%ORDER_UP%%",
            channel_confirmation=ChannelConfirmation.UNMONITORED,
        )
        assert outcome == SessionOutcome.RETRIABLE
        assert reason == RetryReason.EARLY_STOP

    def test_early_stop_not_triggered_without_marker(self) -> None:
        """When no completion_marker is configured, EARLY_STOP should NOT fire.

        Sessions without a marker are not subject to early-stop detection.
        """
        session = ClaudeSessionResult(
            subtype="success",
            result="Some output without any marker",
            is_error=False,
            session_id="no-marker",
            errors=[],
        )
        needs_retry, reason = _compute_retry(
            session,
            returncode=0,
            termination=TerminationReason.NATURAL_EXIT,
            channel_confirmation=ChannelConfirmation.UNMONITORED,
            completion_marker="",
        )
        assert needs_retry is False
        assert reason == RetryReason.NONE

    def test_early_stop_not_triggered_when_marker_present(self) -> None:
        """When the completion marker IS present, EARLY_STOP should NOT fire."""
        session = ClaudeSessionResult(
            subtype="success",
            result="Result with %%ORDER_UP%% marker present",
            is_error=False,
            session_id="has-marker",
            errors=[],
        )
        needs_retry, reason = _compute_retry(
            session,
            returncode=0,
            termination=TerminationReason.NATURAL_EXIT,
            channel_confirmation=ChannelConfirmation.UNMONITORED,
            completion_marker="%%ORDER_UP%%",
        )
        assert needs_retry is False
        assert reason == RetryReason.NONE

    def test_early_stop_not_triggered_for_errors(self) -> None:
        """Error sessions should not be classified as EARLY_STOP."""
        session = ClaudeSessionResult(
            subtype="error_during_execution",
            result="Error occurred but substantive output...",
            is_error=True,
            session_id="error-session",
            errors=["something broke"],
        )
        needs_retry, reason = _compute_retry(
            session,
            returncode=0,
            termination=TerminationReason.NATURAL_EXIT,
            channel_confirmation=ChannelConfirmation.UNMONITORED,
            completion_marker="%%ORDER_UP%%",
        )
        # is_error sessions may trigger API-level retry via needs_retry property,
        # but EARLY_STOP specifically should not fire for non-success subtypes
        assert reason != RetryReason.EARLY_STOP

    def test_early_stop_not_triggered_for_empty_result(self) -> None:
        """Empty result should be classified as kill anomaly, not EARLY_STOP."""
        session = ClaudeSessionResult(
            subtype="success",
            result="",
            is_error=False,
            session_id="empty",
            errors=[],
        )
        needs_retry, reason = _compute_retry(
            session,
            returncode=0,
            termination=TerminationReason.NATURAL_EXIT,
            channel_confirmation=ChannelConfirmation.UNMONITORED,
            completion_marker="%%ORDER_UP%%",
        )
        # Empty result triggers kill_anomaly path (EMPTY_OUTPUT), not EARLY_STOP.
        # No context exhaustion detected → EMPTY_OUTPUT, not RESUME.
        assert needs_retry is True
        assert reason == RetryReason.EMPTY_OUTPUT


class TestArtifactValidation:
    """Tests for expected_output_patterns artifact validation."""

    def test_check_session_content_validates_expected_artifacts(self) -> None:
        """When expected_output_patterns are configured, _check_session_content
        must verify that the session result contains at least one match.
        """
        session = ClaudeSessionResult(
            subtype="success",
            result="Done! %%ORDER_UP%%",
            is_error=False,
            session_id="no-artifact",
            errors=[],
        )
        # With marker present but no matching artifact
        result = _check_session_content(
            session,
            completion_marker="%%ORDER_UP%%",
            expected_output_patterns=[r"https://github\.com/.*/pull/\d+"],
        )
        assert result is False

    def test_check_session_content_passes_with_matching_artifact(self) -> None:
        """When artifacts match, content check passes."""
        session = ClaudeSessionResult(
            subtype="success",
            result="PR created: https://github.com/user/repo/pull/42 %%ORDER_UP%%",
            is_error=False,
            session_id="has-artifact",
            errors=[],
        )
        result = _check_session_content(
            session,
            completion_marker="%%ORDER_UP%%",
            expected_output_patterns=[r"https://github\.com/.*/pull/\d+"],
        )
        assert result is True

    def test_check_session_content_no_patterns_skips_validation(self) -> None:
        """When no patterns are provided, artifact validation is skipped."""
        session = ClaudeSessionResult(
            subtype="success",
            result="Done! %%ORDER_UP%%",
            is_error=False,
            session_id="no-patterns",
            errors=[],
        )
        result = _check_session_content(
            session,
            completion_marker="%%ORDER_UP%%",
            expected_output_patterns=[],
        )
        assert result is True

    def test_compute_outcome_threads_expected_output_patterns(self) -> None:
        """_compute_outcome must thread expected_output_patterns through to
        _compute_success and _check_session_content."""
        session = ClaudeSessionResult(
            subtype="success",
            result="Done! %%ORDER_UP%%",
            is_error=False,
            session_id="threaded",
            errors=[],
        )
        # Without patterns: success
        outcome_no_patterns, _ = _compute_outcome(
            session,
            returncode=0,
            termination=TerminationReason.NATURAL_EXIT,
            completion_marker="%%ORDER_UP%%",
            channel_confirmation=ChannelConfirmation.UNMONITORED,
            expected_output_patterns=[],
        )
        assert outcome_no_patterns == SessionOutcome.SUCCEEDED

        # With patterns that don't match: failed (EARLY_STOP since marker
        # absent from _compute_success perspective when artifact check fails)
        outcome_with_patterns, _ = _compute_outcome(
            session,
            returncode=0,
            termination=TerminationReason.NATURAL_EXIT,
            completion_marker="%%ORDER_UP%%",
            channel_confirmation=ChannelConfirmation.UNMONITORED,
            expected_output_patterns=[r"https://github\.com/.*/pull/\d+"],
        )
        assert outcome_with_patterns != SessionOutcome.SUCCEEDED


class TestToolUseParsing:
    """Tests for tool_use NDJSON record extraction."""

    @staticmethod
    def _result_line() -> str:
        return json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "done",
                "session_id": "s1",
            }
        )

    @staticmethod
    def _assistant_line(*content_blocks: dict) -> str:  # type: ignore[type-arg]
        return json.dumps(
            {
                "type": "assistant",
                "message": {"content": list(content_blocks)},
            }
        )

    def test_parse_session_result_captures_tool_uses(self) -> None:
        """parse_session_result must extract tool_use records."""
        ndjson = "\n".join(
            [
                self._assistant_line(
                    {"type": "tool_use", "name": "Skill", "id": "tu_1"},
                    {"type": "text", "text": "loading skill"},
                ),
                self._result_line(),
            ]
        )
        session = parse_session_result(ndjson)
        assert len(session.tool_uses) == 1
        assert session.tool_uses[0]["name"] == "Skill"
        assert session.tool_uses[0]["id"] == "tu_1"

    def test_parse_session_result_no_tool_uses(self) -> None:
        """Sessions without tool_use records have an empty list."""
        ndjson = "\n".join(
            [
                self._assistant_line(
                    {"type": "text", "text": "just text"},
                ),
                self._result_line(),
            ]
        )
        session = parse_session_result(ndjson)
        assert session.tool_uses == []

    def test_parse_session_result_multiple_tool_uses(self) -> None:
        """Multiple tool_use records across messages are captured."""
        ndjson = "\n".join(
            [
                self._assistant_line(
                    {"type": "tool_use", "name": "Write", "id": "tu_1"},
                ),
                self._assistant_line(
                    {"type": "tool_use", "name": "Skill", "id": "tu_2"},
                ),
                self._result_line(),
            ]
        )
        session = parse_session_result(ndjson)
        assert len(session.tool_uses) == 2
        assert session.tool_uses[0]["name"] == "Write"
        assert session.tool_uses[1]["name"] == "Skill"


class TestCheckExpectedPatterns:
    """Unit tests for the standalone _check_expected_patterns function."""

    def test_check_expected_patterns_present(self) -> None:
        assert (
            _check_expected_patterns(
                result="some text ---my-block--- more text",
                patterns=["---my-block---"],
            )
            is True
        )

    def test_check_expected_patterns_absent(self) -> None:
        assert (
            _check_expected_patterns(
                result="some text without the block",
                patterns=["---my-block---"],
            )
            is False
        )

    def test_check_expected_patterns_empty_patterns_always_true(self) -> None:
        assert _check_expected_patterns(result="anything", patterns=[]) is True

    def test_check_expected_patterns_bold_wrapped_token_matches(self) -> None:
        """Bold-wrapped token name must match after normalization."""
        result = "**plan_path** = /abs/path/plan.md\n%%ORDER_UP%%"
        assert _check_expected_patterns(result, ["plan_path\\s*=\\s*/.+"]) is True

    def test_check_expected_patterns_italic_wrapped_token_matches(self) -> None:
        """Italic-wrapped token name must match after normalization."""
        result = "*plan_path* = /abs/path/plan.md\n%%ORDER_UP%%"
        assert _check_expected_patterns(result, ["plan_path\\s*=\\s*/.+"]) is True

    def test_check_expected_patterns_bold_verdict_matches(self) -> None:
        """Bold-wrapped verdict token must match after normalization."""
        result = "**verdict** = GO\n%%ORDER_UP%%"
        assert _check_expected_patterns(result, ["verdict\\s*=\\s*(GO|NO GO)"]) is True

    def test_check_expected_patterns_multiple_bold_tokens_all_match(self) -> None:
        """Multiple bold-wrapped tokens must all match (AND semantics preserved)."""
        result = (
            "**plan_path** = /abs/path/plan.md\n**plan_parts** = /abs/path/plan.md\n%%ORDER_UP%%"
        )
        assert (
            _check_expected_patterns(result, ["plan_path\\s*=\\s*/.+", "plan_parts\\s*=\\s*/.+"])
            is True
        )

    def test_check_expected_patterns_bold_relative_path_still_fails(self) -> None:
        """Bold wrapping on a relative path must still fail — normalization must not
        mask a genuine contract violation (wrong value type)."""
        result = "**worktree_path** = ../worktrees/impl\n%%ORDER_UP%%"
        assert _check_expected_patterns(result, ["worktree_path\\s*=\\s*/.+"]) is False

    def test_check_expected_patterns_bold_absent_value_still_fails(self) -> None:
        """Bold key with no value must still fail — semantic content must be present."""
        result = "**plan_path** =\n%%ORDER_UP%%"
        assert _check_expected_patterns(result, ["plan_path\\s*=\\s*/.+"]) is False


class TestDeadEndGuardContentState:
    """Dead-end guard must distinguish drain-race artifacts from terminal failures."""

    def test_compute_outcome_channel_b_pattern_contract_violation_is_terminal(
        self,
        make_session: Callable[..., ClaudeSessionResult],
    ) -> None:
        """Dead-end guard must NOT promote to RETRIABLE when session has content + marker
        but expected_output_patterns are absent — contract violation, not a drain race."""
        session = make_session(
            subtype="success",
            is_error=False,
            result="Investigation complete. %%ORDER_UP%%",
            assistant_messages=[],  # no assistant_messages to recover from
        )
        outcome, retry_reason = _compute_outcome(
            session=session,
            returncode=0,
            termination=TerminationReason.NATURAL_EXIT,
            completion_marker="%%ORDER_UP%%",
            channel_confirmation=ChannelConfirmation.CHANNEL_B,
            expected_output_patterns=["investigation_path\\s*=\\s*/.+"],
        )
        # Contract violation: result is non-empty, marker is present, but patterns are absent.
        # The dead-end guard must NOT promote this to RETRIABLE — retrying will never help.
        assert outcome == SessionOutcome.FAILED, (
            f"Expected FAILED for pattern contract violation, got {outcome}. "
            "Dead-end guard is incorrectly treating contract violations as drain-race artifacts."
        )
        assert retry_reason == RetryReason.NONE

    def test_compute_outcome_completed_channel_b_pattern_contract_violation_is_terminal(
        self,
        make_session: Callable[..., ClaudeSessionResult],
    ) -> None:
        """Same contract-violation guard check for COMPLETED + CHANNEL_B termination path."""
        session = make_session(
            subtype="success",
            is_error=False,
            result="Done. %%ORDER_UP%%",
            assistant_messages=[],
        )
        outcome, retry_reason = _compute_outcome(
            session=session,
            returncode=0,
            termination=TerminationReason.COMPLETED,
            completion_marker="%%ORDER_UP%%",
            channel_confirmation=ChannelConfirmation.CHANNEL_B,
            expected_output_patterns=["verdict\\s*=\\s*(GO|NO GO)"],
        )
        assert outcome == SessionOutcome.FAILED
        assert retry_reason == RetryReason.NONE

    def test_compute_outcome_channel_b_empty_result_is_still_retriable(
        self,
        make_session: Callable[..., ClaudeSessionResult],
    ) -> None:
        """Regression test: drain-race rescue (empty result) must still be promoted to RETRIABLE.
        The ContentState fix must NOT break the existing drain-race handling."""
        session = make_session(
            subtype="success",
            is_error=False,
            result="",  # empty — drain race candidate
            assistant_messages=[],
        )
        outcome, retry_reason = _compute_outcome(
            session=session,
            returncode=0,
            termination=TerminationReason.NATURAL_EXIT,
            completion_marker="%%ORDER_UP%%",
            channel_confirmation=ChannelConfirmation.CHANNEL_B,
            expected_output_patterns=["investigation_path\\s*=\\s*/.+"],
        )
        assert outcome == SessionOutcome.RETRIABLE, (
            "Empty result with channel confirmation must remain RETRIABLE (drain-race rescue)."
        )
        assert retry_reason == RetryReason.DRAIN_RACE

    def test_compute_outcome_channel_b_missing_marker_is_still_retriable(
        self,
        make_session: Callable[..., ClaudeSessionResult],
    ) -> None:
        """Regression: result present but marker absent is still RETRIABLE (partial drain)."""
        session = make_session(
            subtype="success",
            is_error=False,
            result="Some output without the marker",
            assistant_messages=[],
        )
        outcome, retry_reason = _compute_outcome(
            session=session,
            returncode=0,
            termination=TerminationReason.NATURAL_EXIT,
            completion_marker="%%ORDER_UP%%",
            channel_confirmation=ChannelConfirmation.CHANNEL_B,
            expected_output_patterns=["investigation_path\\s*=\\s*/.+"],
        )
        assert outcome == SessionOutcome.RETRIABLE, (
            "Missing completion marker with channel confirmation must remain RETRIABLE."
        )
        assert retry_reason == RetryReason.DRAIN_RACE

    def test_dead_end_guard_channel_confirmed_absent_emits_drain_race(
        self,
        make_session: Callable[..., ClaudeSessionResult],
    ) -> None:
        """Channel confirmed + content ABSENT → DRAIN_RACE, not RESUME.

        DRAIN_RACE distinguishes "infrastructure confirmed completion, stdout not
        fully flushed" from "session hit context limit." Both route to on_context_limit
        because progress was confirmed, but the provenance is now explicit.
        """
        session = make_session(
            subtype="success",
            is_error=False,
            result="",  # empty — drain race candidate
            assistant_messages=[],
        )
        outcome, reason = _compute_outcome(
            session=session,
            returncode=0,
            termination=TerminationReason.NATURAL_EXIT,
            completion_marker="%%ORDER_UP%%",
            channel_confirmation=ChannelConfirmation.CHANNEL_A,
            expected_output_patterns=(),
        )
        assert outcome == SessionOutcome.RETRIABLE
        assert reason == RetryReason.DRAIN_RACE  # not RESUME

    def test_compute_outcome_bold_wrapped_token_is_success_not_violation(
        self,
        make_session: Callable[..., ClaudeSessionResult],
    ) -> None:
        """A session with bold-wrapped structured output tokens must succeed,
        not be classified as CONTRACT_VIOLATION and returned as adjudicated_failure."""
        session = make_session(result="**plan_path** = /abs/path/plan.md\n%%ORDER_UP%%")
        outcome, reason = _compute_outcome(
            session,
            returncode=0,
            termination=TerminationReason.NATURAL_EXIT,
            completion_marker="%%ORDER_UP%%",
            channel_confirmation=ChannelConfirmation.CHANNEL_B,
            expected_output_patterns=["plan_path\\s*=\\s*/.+"],
        )
        assert outcome == SessionOutcome.SUCCEEDED
        assert reason == RetryReason.NONE


# ---------------------------------------------------------------------------
# T1: parse_session_result preserves file_path from Write/Edit tool_use input
# ---------------------------------------------------------------------------


@pytest.fixture
def make_ndjson():
    """Build a minimal NDJSON string with assistant tool_use records and a result record.

    tool_uses entries use the raw NDJSON form: each dict must have 'name', 'id', and
    optionally 'input' (a dict whose 'file_path' key will be preserved by Step 3's changes).
    """

    def _factory(
        tool_uses: list[dict] | None = None,
        result_text: str = "done",
    ) -> str:
        records = []
        if tool_uses:
            content = [
                {
                    "type": "tool_use",
                    "name": tu["name"],
                    "id": tu["id"],
                    "input": tu.get("input", {}),
                }
                for tu in tool_uses
            ]
            records.append(json.dumps({"type": "assistant", "message": {"content": content}}))
        records.append(
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "result": result_text,
                    "session_id": "test-session",
                }
            )
        )
        return "\n".join(records)

    return _factory


def test_parse_session_result_preserves_write_file_path(make_ndjson):
    """Write tool_use input.file_path must be preserved in tool_uses entries."""
    ndjson = make_ndjson(
        tool_uses=[{"name": "Write", "id": "tu1", "input": {"file_path": "/abs/plan.md"}}]
    )
    session = parse_session_result(ndjson)
    assert session.tool_uses == [{"name": "Write", "id": "tu1", "file_path": "/abs/plan.md"}]


def test_parse_session_result_preserves_edit_file_path(make_ndjson):
    """Edit tool_use input.file_path must be preserved in tool_uses entries."""
    ndjson = make_ndjson(
        tool_uses=[{"name": "Edit", "id": "tu2", "input": {"file_path": "/abs/file.py"}}]
    )
    session = parse_session_result(ndjson)
    assert session.tool_uses == [{"name": "Edit", "id": "tu2", "file_path": "/abs/file.py"}]


def test_parse_session_result_non_write_tools_no_file_path(make_ndjson):
    """Non-Write/Edit tool_uses must not gain a file_path key."""
    ndjson = make_ndjson(tool_uses=[{"name": "Bash", "id": "tu3", "input": {"command": "ls"}}])
    session = parse_session_result(ndjson)
    assert session.tool_uses == [{"name": "Bash", "id": "tu3"}]
    assert "file_path" not in session.tool_uses[0]
