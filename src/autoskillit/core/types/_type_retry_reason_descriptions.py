"""RetryReason description prose for prompt rendering.

Lives in its own shard so ``_type_enums.py`` stays under the
REQ-CNST-010 file-length hard cap while the RetryReason enum-and-its-
descriptions pair remains co-located through wildcard re-export.
"""

from __future__ import annotations

from ._type_enums import RetryReason

_TERMINAL_RETRY_POLICY_SUFFIX = "; route on_failure, never on_context_limit, and do not resume"


RETRY_REASON_DESCRIPTIONS: dict[RetryReason, str] = {
    RetryReason.RESUME: (
        "transient infrastructure failure; Resume is safe with the recorded recovery context"
    ),
    RetryReason.STALE: "start a fresh retry because the prior session is stale",
    RetryReason.NONE: "no retry reason was supplied",
    RetryReason.BUDGET_EXHAUSTED: "budget exhausted" + _TERMINAL_RETRY_POLICY_SUFFIX,
    RetryReason.EARLY_STOP: "retry after the early stop using the recorded progress",
    RetryReason.ZERO_WRITES: "retry because the implementation produced no write evidence",
    RetryReason.EMPTY_OUTPUT: "retry because the session exited without output",
    RetryReason.COMPLETED_NO_FLUSH: "retry because completed output was not flushed",
    RetryReason.DRAIN_RACE: "retry after the output drain race",
    RetryReason.PATH_CONTAMINATION: "retry from a clean worktree after path contamination",
    RetryReason.CONTRACT_RECOVERY: "retry using the artifact-contract recovery route",
    RetryReason.CLONE_CONTAMINATION: "retry from an uncontaminated clone",
    RetryReason.THINKING_STALL: "retry after the thinking-only stall",
    RetryReason.IDLE_STALL: "idle timeout; Resume is safe with the existing session",
    RetryReason.RATE_LIMITED: "wait for the rate-limit window and retry",
    RetryReason.CANCELLED: "session cancelled" + _TERMINAL_RETRY_POLICY_SUFFIX,
    RetryReason.OUTCOME_INVARIANT: "outcome invariant failed" + _TERMINAL_RETRY_POLICY_SUFFIX,
    RetryReason.OUTCOME_REPORT_MALFORMED: (
        "outcome report malformed" + _TERMINAL_RETRY_POLICY_SUFFIX
    ),
    RetryReason.ASYNC_OBLIGATION: "retry after resolving the outstanding asynchronous obligation",
    RetryReason.CONTEXT_EXHAUSTED: "context exhausted" + _TERMINAL_RETRY_POLICY_SUFFIX,
}

if set(RETRY_REASON_DESCRIPTIONS) != set(RetryReason):
    raise AssertionError("RETRY_REASON_DESCRIPTIONS must cover every RetryReason exactly")


__all__ = ["RETRY_REASON_DESCRIPTIONS"]
