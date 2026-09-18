"""Human guidance for every retry-reason discriminator."""

from __future__ import annotations

from ._type_enums import RetryReason

__all__ = ["RETRY_REASON_DESCRIPTIONS"]

RETRY_REASON_DESCRIPTIONS: dict[RetryReason, str] = {
    RetryReason.RESUME: (
        "transient infrastructure failure; Resume is safe with the recorded recovery context"
    ),
    RetryReason.STALE: "start a fresh retry because the prior session is stale",
    RetryReason.NONE: "no retry reason was supplied",
    RetryReason.BUDGET_EXHAUSTED: (
        "budget exhausted; route on_failure, never on_context_limit, and do not resume"
    ),
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
    RetryReason.CANCELLED: (
        "session cancelled; route on_failure, never on_context_limit, and do not resume"
    ),
    RetryReason.OUTCOME_INVARIANT: (
        "outcome invariant failed; route on_failure, never on_context_limit, and do not resume"
    ),
    RetryReason.OUTCOME_REPORT_MALFORMED: (
        "outcome report malformed; route on_failure, never on_context_limit, and do not resume"
    ),
    RetryReason.ASYNC_OBLIGATION: "retry after resolving the outstanding asynchronous obligation",
    RetryReason.CONTEXT_EXHAUSTED: (
        "context exhausted; route on_failure, never on_context_limit, and do not resume"
    ),
}

if set(RETRY_REASON_DESCRIPTIONS) != set(RetryReason):
    raise AssertionError("RETRY_REASON_DESCRIPTIONS must cover every RetryReason exactly")
