"""Subprocess execution types and contracts.

Zero autoskillit imports outside this sub-package. Provides SubprocessResult,
SubprocessRunner, and the termination contract sentinel.
"""

from __future__ import annotations

from collections.abc import Awaitable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from ._type_enums import ChannelConfirmation, TerminationReason

__all__ = [
    "SubprocessResult",
    "SubprocessRunner",
    "_TERMINATION_CONTRACT",
]

#: Semantic contract for SubprocessResult fields per TerminationReason.
#: These invariants are enforced by tests/test_process_lifecycle.py
#: TestAdjudicationCoverageMatrix.
#:
#: NATURAL_EXIT:
#:   channel_confirmation=UNMONITORED (typical: process exited before channels fired)
#:   channel_confirmation=CHANNEL_A (simultaneous: process exit + heartbeat in same tick)
#:   channel_confirmation=CHANNEL_B (simultaneous: process exit + session monitor completion)
#:   returncode=process's actual exit code (0 = voluntary, nonzero = crash)
#:   stdout=whatever was flushed to the temp file before exit
#:   Kill-anomaly possible when returncode==0, UNMONITORED, and stdout is success+empty,
#:   empty_output, or unparseable → _is_kill_anomaly returns True.
#:   When CHANNEL_A or CHANNEL_B: no kill anomaly; session completed.
#:
#: COMPLETED (Channel A):
#:   channel_confirmation=CHANNEL_A (heartbeat confirmed type=result in stdout)
#:   returncode=nonzero (SIGTERM/SIGKILL from async_kill_process_tree)
#:   stdout=contains a complete type=result NDJSON record
#:
#: COMPLETED (Channel B, drain expired OR no heartbeat configured):
#:   channel_confirmation=CHANNEL_B (session JSONL is sole authority)
#:   returncode=nonzero (SIGTERM/SIGKILL)
#:   stdout=may be empty (CLI not yet flushed type=result before kill)
#:   _compute_success provenance bypass applies: return True immediately.
#:
#: STALE:
#:   channel_confirmation=UNMONITORED (typical: stale monitor fired alone)
#:   channel_confirmation=CHANNEL_A (simultaneous: stale monitor + heartbeat in same tick)
#:   returncode=nonzero (SIGTERM/SIGKILL)
#:   _build_skill_result intercepts before _compute_success: attempts
#:   stdout recovery; if successful returns subtype="recovered_from_stale".
#:   STALE+CHANNEL_B is structurally impossible: session_monitor returns either
#:   "stale" or "completion", never both; stale path sets UNMONITORED.
#:
#: TIMED_OUT:
#:   channel_confirmation=UNMONITORED (never modified)
#:   returncode=-1 (hardcoded in _build_skill_result, not from process)
#:   _build_skill_result constructs synthetic ClaudeSessionResult(subtype="timeout").
#:   Always returns success=False, needs_retry=False.
_TERMINATION_CONTRACT = None  # Marker — contract is documented above in comments.


@dataclass
class SubprocessResult:
    """Result from a managed subprocess execution."""

    returncode: int
    stdout: str
    stderr: str
    termination: TerminationReason
    pid: int
    channel_confirmation: ChannelConfirmation = ChannelConfirmation.UNMONITORED
    """How completion was confirmed by the two-channel detection system.

    CHANNEL_A: heartbeat confirmed type=result in stdout; data availability guaranteed.
    CHANNEL_B: session JSONL marker fired; drain expired or no heartbeat configured.
               stdout may be empty — callers must trust JSONL signal, not stdout content.
    UNMONITORED: no channel monitoring active (NATURAL_EXIT, STALE, TIMED_OUT, sync path).
    """
    proc_snapshots: list[dict[str, object]] | None = None
    channel_b_session_id: str = ""
    start_ts: str = ""
    end_ts: str = ""
    elapsed_seconds: float = 0.0
    """Pre-computed monotonic elapsed time in seconds (always >= 0).

    Set by headless.py using time.monotonic() brackets around the subprocess run.
    Consumers (session_log, tokens) must use this float directly — never re-derive
    duration from start_ts/end_ts ISO strings.
    """


@runtime_checkable
class SubprocessRunner(Protocol):
    """Protocol for async subprocess execution. Matches run_managed_async signature."""

    def __call__(
        self,
        cmd: list[str],
        *,
        cwd: Path,
        timeout: float,
        env: dict[str, str] | None = None,
        stale_threshold: float = 1200,
        completion_marker: str = "",
        session_log_dir: Path | None = None,
        pty_mode: bool = False,
        input_data: str | None = None,
        completion_drain_timeout: float = 5.0,
        linux_tracing_config: Any | None = None,
    ) -> Awaitable[SubprocessResult]: ...
