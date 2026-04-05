"""End-to-end session failure classification tests using api-simulator FakeClaudeCLI.

These tests exercise the full classification pipeline from raw NDJSON subprocess
output through parse_session_result() and _build_skill_result() to SkillResult,
using api-simulator's fake_claude fixture to produce realistic subprocess output.
"""

from __future__ import annotations

import os
import signal
import subprocess
from collections.abc import Sequence

from api_simulator.claude import ClaudeCLI

from autoskillit.core.types import (
    RetryReason,
    SkillResult,
    SubprocessResult,
    TerminationReason,
    WriteBehaviorSpec,
)
from autoskillit.execution.headless import _build_skill_result
from autoskillit.execution.session import CliSubtype, parse_session_result

# ---------------------------------------------------------------------------
# NDJSON message factories
# ---------------------------------------------------------------------------


def _result_msg(
    result_text: str = "done",
    subtype: str = "success",
    is_error: bool = False,
    session_id: str = "fake-session-001",
    errors: list[str] | None = None,
) -> dict:
    """Build a standard ``type=result`` NDJSON record."""
    return {
        "type": "result",
        "subtype": subtype,
        "is_error": is_error,
        "result": result_text,
        "session_id": session_id,
        "errors": errors or [],
    }


def _assistant_msg(
    model: str = "claude-sonnet-4-6",
    input_tokens: int = 100,
    output_tokens: int = 50,
) -> dict:
    """Build a wrapped ``type=assistant`` NDJSON record with usage data."""
    return {
        "type": "assistant",
        "message": {
            "model": model,
            "content": "working...",
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
        },
    }


def _flat_assistant_msg(text: str, output_tokens: int = 0) -> dict:
    """Build a *flat* assistant record (no ``message`` key) for context exhaustion."""
    return {
        "type": "assistant",
        "content": [{"type": "text", "text": text}],
        "output_tokens": output_tokens,
        "input_tokens": 0,
        "cache_read_input_tokens": 0,
    }


# ---------------------------------------------------------------------------
# Bridge helpers: FakeClaudeCLI → SkillResult / raw stdout
# ---------------------------------------------------------------------------


def _run_with_timeout(
    fake_claude: ClaudeCLI,
    timeout: float = 3,
    kill_timeout: float = 5,
) -> str:
    """Run fake_claude via Popen with timeout + process-group SIGKILL.

    Installs fake on PATH, spawns claude, reads stdout until timeout, then
    kills the entire process group (shim + child _runner.py) so no child
    holds the pipe open.  Returns accumulated stdout — Python's subprocess
    preserves data between communicate() calls via _fileobj2output, so no
    output is lost when the first call raises TimeoutExpired.
    """
    with fake_claude:  # installs fake on PATH
        proc = subprocess.Popen(
            ["claude", "--output-format", "stream-json", "-p", "test"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            stdout, _ = proc.communicate(timeout=timeout)
            return stdout
        except subprocess.TimeoutExpired:
            # Kill the entire process group — the shim spawns _runner.py
            # as a child; killing only the shim leaves the child holding
            # the pipe open, causing communicate() to block forever.
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            try:
                stdout, _ = proc.communicate(timeout=kill_timeout)
            except subprocess.TimeoutExpired:
                # Process group did not exit within kill_timeout after SIGKILL;
                # return whatever was accumulated before the first timeout.
                stdout = ""
            return stdout


def _classify(
    fake_claude: ClaudeCLI,
    termination: TerminationReason = TerminationReason.NATURAL_EXIT,
    completion_marker: str = "",
    expected_output_patterns: Sequence[str] = (),
    write_behavior: WriteBehaviorSpec | None = None,
) -> SkillResult:
    """Run fake_claude, parse stdout, classify through _build_skill_result."""
    proc = fake_claude.run()
    sr = SubprocessResult(
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
        termination=termination,
        pid=0,
    )
    return _build_skill_result(
        sr,
        completion_marker=completion_marker,
        expected_output_patterns=expected_output_patterns,
        write_behavior=write_behavior,
    )


# ===========================================================================
# Group 1: NDJSON Stream Robustness
# ===========================================================================


class TestNdjsonStreamRobustness:
    """Verify the parser gracefully handles non-result NDJSON events."""

    def test_api_retry_events_skipped(self, fake_claude: ClaudeCLI) -> None:
        """API retry system events before the result record are ignored."""
        fake_claude.inject_api_retry(at_message=0, error_status=529, attempts=3)
        fake_claude.add_message(_result_msg())

        skill = _classify(fake_claude)

        assert skill.success is True
        assert skill.subtype == "success"
        assert skill.exit_code == 0

    def test_stream_corruption_skipped(self, fake_claude: ClaudeCLI) -> None:
        """A corrupted (non-JSON) line mid-stream is skipped; result still parsed."""
        fake_claude.add_message(_assistant_msg())
        fake_claude.add_message(_assistant_msg())  # placeholder to corrupt
        fake_claude.add_message(_result_msg())
        fake_claude.corrupt_stream(at_message=1, error_text="API Error (503 upstream...)")

        skill = _classify(fake_claude)

        assert skill.success is True
        assert skill.subtype == "success"

    def test_multiple_result_records_last_wins(self, fake_claude: ClaudeCLI) -> None:
        """When multiple result records exist, the last one determines the outcome."""
        fake_claude.add_message(
            _result_msg("first", "success"),
        )
        fake_claude.add_message(
            _result_msg("second", "error", is_error=True, errors=["something failed"]),
        )

        skill = _classify(fake_claude)

        assert skill.success is False
        assert skill.is_error is True
        assert "second" in skill.result

    def test_api_retry_exhaustion_no_result(self, fake_claude: ClaudeCLI) -> None:
        """Exhausted retries suppress all subsequent messages — no result record."""
        fake_claude.inject_api_retry(at_message=0, error_status=529, attempts=10, exhaust=True)
        fake_claude.add_message(_result_msg())  # unreachable due to exhaust=True

        skill = _classify(fake_claude)

        assert skill.success is False
        assert skill.is_error is True
        assert skill.subtype == "unparseable"
        assert skill.exit_code != 0


# ===========================================================================
# Group 2: Context Exhaustion Edge Cases
# ===========================================================================


class TestContextExhaustionEdgeCases:
    """Verify context-exhaustion detection from different NDJSON shapes."""

    def test_flat_assistant_context_exhaustion(self, fake_claude: ClaudeCLI) -> None:
        """Flat assistant record with 'prompt is too long' triggers context exhaustion."""
        fake_claude.add_message(_flat_assistant_msg("prompt is too long"))

        skill = _classify(fake_claude)

        assert skill.success is False
        assert skill.subtype == "context_exhaustion"
        assert skill.needs_retry is True
        assert skill.retry_reason == RetryReason.RESUME

    def test_result_errors_context_exhaustion(self, fake_claude: ClaudeCLI) -> None:
        """Result record with 'prompt is too long' in errors triggers context exhaustion."""
        fake_claude.add_message(
            _result_msg(
                result_text="prompt is too long",
                subtype="error",
                is_error=True,
                errors=["prompt is too long"],
            ),
        )

        skill = _classify(fake_claude)

        assert skill.success is False
        assert skill.needs_retry is True
        assert skill.retry_reason == RetryReason.RESUME


# ===========================================================================
# Group 3: Kill Boundary Scenarios
# ===========================================================================


class TestKillBoundaryScenarios:
    """Verify classification at truncation and interrupt boundaries."""

    def test_truncated_stream_mid_write(self, fake_claude: ClaudeCLI) -> None:
        """Truncation after message index 1 exits with code 1 — classified as failure."""
        fake_claude.add_message(_assistant_msg())
        fake_claude.add_message(_result_msg())
        fake_claude.add_message(_assistant_msg())
        fake_claude.truncate_after(1)

        skill = _classify(fake_claude)

        # truncate_after(1) emits messages 0 only (indices < 1), exits code 1
        assert skill.success is False
        assert skill.exit_code != 0

    def test_interrupted_nonzero_exit_no_retry(self, fake_claude: ClaudeCLI) -> None:
        """Interrupted subtype + nonzero exit → failure without retry."""
        fake_claude.add_message(_result_msg(subtype="interrupted"))
        fake_claude.set_exit_code(1)

        skill = _classify(fake_claude)

        assert skill.success is False
        assert skill.needs_retry is False
        assert skill.exit_code == 1


# ===========================================================================
# Group 4: Process Behavior Simulation
# ===========================================================================


class TestProcessBehaviorSimulation:
    """Verify classification with realistic process behaviors (hang, mid-exit)."""

    def test_hang_after_result_emits_output(self, fake_claude: ClaudeCLI) -> None:
        """Result record is emitted to stdout before the process hangs."""
        fake_claude.add_message(_result_msg("completed work"))
        fake_claude.hang_after_result()

        stdout = _run_with_timeout(fake_claude)
        session = parse_session_result(stdout)
        assert session.subtype == CliSubtype.SUCCESS
        assert "completed work" in session.result

    def test_inject_exit_mid_stream(self, fake_claude: ClaudeCLI) -> None:
        """inject_exit after message 1 captures the result but exits with code 2."""
        fake_claude.add_message(_assistant_msg())
        fake_claude.add_message(_result_msg("mid-stream"))
        fake_claude.add_message(_assistant_msg())
        fake_claude.inject_exit(1, code=2)

        proc = fake_claude.run()
        assert proc.returncode == 2

        # Result at index 1 was emitted before exit
        session = parse_session_result(proc.stdout)
        assert "mid-stream" in session.result

        # Full classification: nonzero exit → failure
        sr = SubprocessResult(
            returncode=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            termination=TerminationReason.NATURAL_EXIT,
            pid=0,
        )
        skill = _build_skill_result(sr)
        assert skill.exit_code == 2
        assert skill.success is False
