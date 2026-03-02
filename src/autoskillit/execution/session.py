"""Domain model for Claude Code headless session results.

L1 module: imports only from L0 (types, _logging). No server side-effects.
Centralizes all session-parsing concerns so callers can work with typed
objects instead of raw JSON strings.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, assert_never

from autoskillit.core import (
    CONTEXT_EXHAUSTION_MARKER,
    ChannelConfirmation,
    RetryReason,
    SessionOutcome,
    SkillResult,
    TerminationReason,
    get_logger,
    truncate_text,
)

logger = get_logger(__name__)

_truncate = truncate_text

# Re-export SkillResult so existing callers (tests, migration_engine) can still
# import from this module.  Canonical definition lives in core/types.py.
__all__ = ["SkillResult"]


_TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)

_FAILURE_SUBTYPES = frozenset({"unknown", "empty_output", "unparseable", "timeout"})


@dataclass
class ClaudeSessionResult:
    """Parsed result from a Claude Code headless session."""

    subtype: str  # "success", "error_max_turns", "error_during_execution", etc.
    is_error: bool
    result: str
    session_id: str
    errors: list[str] = field(default_factory=list)
    token_usage: dict[str, Any] | None = None
    assistant_messages: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not isinstance(self.result, str):
            if isinstance(self.result, list):
                self.result = "\n".join(
                    b.get("text", "") if isinstance(b, dict) else str(b) for b in self.result
                )
            elif not isinstance(self.result, str):
                self.result = "" if self.result is None else str(self.result)
        if not isinstance(self.errors, list):
            self.errors = [] if self.errors is None else [str(self.errors)]
        if not isinstance(self.subtype, str):
            self.subtype = "unknown" if self.subtype is None else str(self.subtype)
        if not isinstance(self.session_id, str):
            self.session_id = "" if self.session_id is None else str(self.session_id)

    def _is_context_exhausted(self) -> bool:
        """Detect context window exhaustion from Claude's error output.

        Requires both ``is_error=True`` AND the marker to appear in the
        ``errors`` list (structured CLI signal).  Falls back to checking
        ``result`` only when the subtype is a known error subtype, to
        narrow false-positives from model prose that happens to contain
        the marker phrase.
        """
        if not self.is_error:
            return False
        # Primary: check the structured errors list from Claude CLI
        marker = CONTEXT_EXHAUSTION_MARKER
        if any(marker in e.lower() for e in self.errors):
            return True
        # Fallback: only trust result text for error subtypes, not execution errors
        # where the model's own output could contain the marker phrase
        if self.subtype in ("success", "error_max_turns") and marker in self.result.lower():
            return True
        return False

    @property
    def agent_result(self) -> str:
        """Result text rewritten for LLM agent consumption.

        When the session ended due to a retriable condition (context exhaustion,
        max turns), the raw result text from Claude CLI can be misleading to
        LLM callers. This property returns semantically correct, actionable text.
        The raw result is preserved in self.result for debugging.
        """
        if self._is_context_exhausted():
            return (
                "Context limit reached during session execution. "
                "The session made partial progress. "
                "Use needs_retry and retry_reason to continue from where it left off."
            )
        if self.subtype == "error_max_turns":
            return (
                "Turn limit reached during session execution. "
                "The session made partial progress. "
                "Use needs_retry and retry_reason to continue from where it left off."
            )
        return self.result

    @property
    def needs_retry(self) -> bool:
        """Whether the session didn't finish and should be retried."""
        if self.subtype == "error_max_turns":
            return True
        if self._is_context_exhausted():
            return True
        return False

    @property
    def retry_reason(self) -> RetryReason:
        """Why retry is needed. NONE if needs_retry is False."""
        if self.needs_retry:
            return RetryReason.RESUME
        return RetryReason.NONE


def extract_token_usage(stdout: str) -> dict[str, Any] | None:
    """Extract token usage from Claude CLI NDJSON output.

    Takes raw NDJSON *stdout* (not a parsed ClaudeSessionResult) because this
    function is called inside parse_session_result() *during construction* of
    ClaudeSessionResult — before that object exists. A (result: ClaudeSessionResult)
    parameter would create a circular bootstrapping dependency and is therefore
    architecturally incorrect for this call site.

    Scans assistant records for per-model usage and the result record for
    authoritative aggregated totals. Returns None if no usage data is found.
    """
    if not stdout.strip():
        return None

    model_buckets: dict[str, dict[str, int]] = {}
    result_usage: dict[str, int] | None = None

    for line in stdout.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue

        record_type = obj.get("type")

        if record_type == "assistant":
            msg = obj.get("message")
            if not isinstance(msg, dict):
                continue
            usage = msg.get("usage")
            if not isinstance(usage, dict):
                continue
            model = msg.get("model", "unknown")
            bucket = model_buckets.setdefault(model, {f: 0 for f in _TOKEN_FIELDS})
            for f in _TOKEN_FIELDS:
                bucket[f] += usage.get(f, 0)

        elif record_type == "result":
            usage = obj.get("usage")
            if isinstance(usage, dict):
                result_usage = {f: usage.get(f, 0) for f in _TOKEN_FIELDS}

    if not model_buckets and result_usage is None:
        return None

    # Aggregated totals: prefer result record, fall back to assistant sum
    if result_usage is not None:
        totals = dict(result_usage)
    else:
        totals = {f: 0 for f in _TOKEN_FIELDS}
        for bucket in model_buckets.values():
            for f in _TOKEN_FIELDS:
                totals[f] += bucket[f]

    return {
        **totals,
        "model_breakdown": dict(model_buckets) if model_buckets else {},
    }


_KNOWN_RESULT_KEYS: frozenset[str] = frozenset(
    {"type", "subtype", "is_error", "result", "session_id", "errors", "usage"}
)


def parse_session_result(stdout: str) -> ClaudeSessionResult:
    """Parse Claude Code's --output-format json stdout into a typed result.

    Handles multi-line NDJSON (Claude Code may emit multiple JSON objects;
    the last 'result' type object is authoritative).
    Falls back gracefully for non-JSON or missing fields.
    """
    if not stdout.strip():
        return ClaudeSessionResult(
            subtype="empty_output",
            is_error=True,
            result="",
            session_id="",
            errors=[],
        )

    result_obj = None
    assistant_messages: list[str] = []
    for line in stdout.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if not isinstance(obj, dict):
                continue
            record_type = obj.get("type")
            if record_type == "result":
                result_obj = obj
            elif record_type == "assistant":
                msg = obj.get("message")
                if not isinstance(msg, dict):
                    continue
                content = msg.get("content", "")
                if isinstance(content, list):
                    text = "\n".join(
                        block.get("text", "") for block in content if isinstance(block, dict)
                    ).strip()
                else:
                    text = str(content).strip()
                if text:
                    assistant_messages.append(text)
        except json.JSONDecodeError:
            continue

    if result_obj is None:
        try:
            fallback = json.loads(stdout)
            if isinstance(fallback, dict) and fallback.get("type") == "result":
                result_obj = fallback
            else:
                return ClaudeSessionResult(
                    subtype="unparseable",
                    is_error=True,
                    result=stdout,
                    session_id="",
                    errors=[],
                )
        except json.JSONDecodeError:
            return ClaudeSessionResult(
                subtype="unparseable",
                is_error=True,
                result=stdout,
                session_id="",
                errors=[],
            )

    token_usage = extract_token_usage(stdout)

    extra_keys = frozenset(result_obj.keys()) - _KNOWN_RESULT_KEYS
    if extra_keys:
        logger.debug("unknown_result_keys", unknown_fields=sorted(extra_keys))

    return ClaudeSessionResult(
        subtype=result_obj.get("subtype", "unknown"),
        is_error=result_obj.get("is_error", False),
        result=result_obj.get("result", ""),
        session_id=result_obj.get("session_id", ""),
        errors=result_obj.get("errors", []),
        token_usage=token_usage,
        assistant_messages=assistant_messages,
    )


def _check_session_content(
    session: ClaudeSessionResult,
    completion_marker: str,
) -> bool:
    """Validate session content fields after termination-specific gates pass."""
    if session.is_error:
        return False
    if not session.result.strip():
        return False
    if session.subtype in _FAILURE_SUBTYPES:
        return False
    if completion_marker:
        result_text = session.result.strip()
        marker_stripped = result_text.replace(completion_marker, "").strip()
        if not marker_stripped:
            return False
        if completion_marker not in result_text:
            return False
    return True


def _compute_success(
    session: ClaudeSessionResult,
    returncode: int,
    termination: TerminationReason,
    completion_marker: str = "",
    channel_confirmation: ChannelConfirmation = ChannelConfirmation.UNMONITORED,
) -> bool:
    """Cross-validate all signals to determine unambiguous success/failure.

    Exhaustive match dispatch over TerminationReason ensures mypy flags any
    unhandled value when the enum is extended (ARCH-007).

    Gate 0.5 (provenance bypass): when ``channel_confirmation=CHANNEL_B``,
    Channel B's session-JSONL marker is the authoritative signal that the
    session completed successfully. Stdout content is not required.
    """
    # Gate 0.5: Channel B provenance bypass — session JSONL is authoritative.
    match channel_confirmation:
        case ChannelConfirmation.CHANNEL_B:
            return True
        case ChannelConfirmation.CHANNEL_A | ChannelConfirmation.UNMONITORED:
            pass  # fall through to termination dispatch
        case _ as _unreachable_cc:
            assert_never(_unreachable_cc)

    match termination:
        case TerminationReason.TIMED_OUT:
            return False

        case TerminationReason.STALE:
            return False

        case TerminationReason.COMPLETED:
            # The process was killed by our own async_kill_process_tree
            # (signal -15 or -9), so a non-zero returncode is expected and
            # trustworthy when the session envelope says "success".
            if returncode != 0 and not (session.subtype == "success" and session.result.strip()):
                return False
            return _check_session_content(session, completion_marker)

        case TerminationReason.NATURAL_EXIT:
            # The process exited on its own. A non-zero returncode is treated
            # as authoritative evidence of failure — no asymmetric bypass.
            if returncode != 0:
                return False
            return _check_session_content(session, completion_marker)

        case _ as unreachable:
            assert_never(unreachable)


_KILL_ANOMALY_SUBTYPES: frozenset[str] = frozenset(
    {
        "unparseable",  # killed mid-write → partial NDJSON
        "empty_output",  # killed before any stdout was written
        "interrupted",  # killed mid-generation → real Claude CLI subtype
    }
)


def _is_kill_anomaly(session: ClaudeSessionResult) -> bool:
    """True if the session result looks like a kill-induced incomplete flush.

    Covers anomalies from both infrastructure kills (COMPLETED) and voluntary
    self-exits (NATURAL_EXIT with returncode==0). Callers must apply their own
    termination-specific discriminators before calling this function.

    Covers:
    - unparseable: process killed mid-write, stdout is partial NDJSON
    - empty_output: process killed before any stdout was written
    - interrupted: process killed mid-generation (real Claude CLI subtype)
    - success with empty result: kill occurred after result record was written
      but with empty content (Channel B / Channel A drain-race, or
      CLAUDE_CODE_EXIT_AFTER_STOP_DELAY timer-based self-exit)
    """
    if session.subtype in _KILL_ANOMALY_SUBTYPES:
        return True
    if session.subtype == "success" and not session.result.strip():
        return True
    return False


def _compute_retry(
    session: ClaudeSessionResult,
    returncode: int,
    termination: TerminationReason,
    channel_confirmation: ChannelConfirmation = ChannelConfirmation.UNMONITORED,
) -> tuple[bool, RetryReason]:
    """Compute whether the session result warrants a retry.

    Phase 1: API-level signals are termination-agnostic.
    Phase 2: Exhaustive match dispatch over TerminationReason ensures mypy
             flags any unhandled value when the enum is extended.

    When ``channel_confirmation=CHANNEL_B`` and ``termination=COMPLETED``,
    the provenance bypass applies: Channel B's session-JSONL signal is
    authoritative, so kill-anomaly appearance is a drain-race artifact.
    No retry is needed.
    """
    # Phase 1: API-level retry signals (context exhaustion, max_turns)
    if session.needs_retry:
        return True, RetryReason.RESUME

    # Phase 2: Exhaustive termination dispatch
    match termination:
        case TerminationReason.NATURAL_EXIT:
            # NATURAL_EXIT covers both deliberate CLI exits AND
            # CLAUDE_CODE_EXIT_AFTER_STOP_DELAY timer-based self-exits.
            # returncode==0 discriminates: clean exit (possible kill-race artifact)
            # vs genuine crash (nonzero returncode — not a timing artifact).
            # Channel confirmation means the session completed — any kill-anomaly
            # appearance is a drain artifact, not a real incomplete flush.
            if channel_confirmation in (
                ChannelConfirmation.CHANNEL_A,
                ChannelConfirmation.CHANNEL_B,
            ):
                return False, RetryReason.NONE
            if returncode == 0 and _is_kill_anomaly(session):
                return True, RetryReason.RESUME
            return False, RetryReason.NONE

        case TerminationReason.COMPLETED:
            # Infrastructure killed the process. SIGTERM/SIGKILL produce nonzero
            # returncode by design — do not gate on returncode here.
            # Exhaustive ChannelConfirmation dispatch (ARCH-007 extension):
            match channel_confirmation:
                case ChannelConfirmation.CHANNEL_B:
                    # Channel B is authoritative — kill-anomaly appearance is
                    # a drain-race artifact, not a real incomplete flush.
                    return False, RetryReason.NONE
                case ChannelConfirmation.CHANNEL_A | ChannelConfirmation.UNMONITORED:
                    if _is_kill_anomaly(session):
                        return True, RetryReason.RESUME
                    return False, RetryReason.NONE
                case _ as _unreachable_cc:
                    assert_never(_unreachable_cc)

        case TerminationReason.STALE:
            # _build_skill_result intercepts STALE before calling _compute_retry.
            # Explicit arm exists for exhaustiveness; unreachable in production.
            return False, RetryReason.NONE

        case TerminationReason.TIMED_OUT:
            # Wall-clock timeout: non-retriable (permanent infrastructure limit).
            return False, RetryReason.NONE

        case _ as unreachable:
            assert_never(unreachable)


def _compute_outcome(
    session: ClaudeSessionResult,
    returncode: int,
    termination: TerminationReason,
    completion_marker: str = "",
    channel_confirmation: ChannelConfirmation = ChannelConfirmation.UNMONITORED,
) -> tuple[SessionOutcome, RetryReason]:
    """Compose _compute_success and _compute_retry into a single SessionOutcome.

    Applies both composition guards (contradiction and dead-end) before mapping
    the resulting (success, needs_retry) pair to the bijective SessionOutcome enum.

    Returns (SessionOutcome, RetryReason). The outcome is never the impossible
    (success=True, needs_retry=True) state — the contradiction guard structurally
    prevents it from reaching the mapping step.

    Does NOT handle _recover_from_separate_marker recovery; that recovery path
    lives in _build_skill_result (headless.py), which calls _compute_success on
    the recovered session then delegates to _compute_outcome.
    """
    success = _compute_success(
        session, returncode, termination, completion_marker, channel_confirmation
    )
    needs_retry, retry_reason = _compute_retry(
        session, returncode, termination, channel_confirmation
    )

    # Contradiction guard: retry signal is authoritative over the channel bypass.
    if success and needs_retry:
        success = False

    # Dead-end guard: channel confirmation means the session reached a natural end;
    # failure to parse content is a data-availability issue, not a terminal failure.
    if not success and not needs_retry:
        match channel_confirmation:
            case ChannelConfirmation.CHANNEL_A | ChannelConfirmation.CHANNEL_B:
                needs_retry = True
                retry_reason = RetryReason.RESUME
            case ChannelConfirmation.UNMONITORED:
                pass  # legitimate terminal failure — no channel confirmed completion
            case _ as unreachable_cc:
                assert_never(unreachable_cc)

    if success:
        return SessionOutcome.SUCCEEDED, retry_reason
    if needs_retry:
        return SessionOutcome.RETRIABLE, retry_reason
    return SessionOutcome.FAILED, retry_reason
