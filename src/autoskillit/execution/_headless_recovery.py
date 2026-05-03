"""Recovery helpers for headless Claude session result reconstruction."""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from autoskillit.core import CliSubtype, OutputFormat, RetryReason, SkillResult, get_logger
from autoskillit.execution._headless_path_tokens import _RECOVERABLE_PATH_TOKENS
from autoskillit.execution.commands import build_headless_resume_cmd
from autoskillit.execution.process import _marker_is_standalone
from autoskillit.execution.session import (
    ClaudeSessionResult,
    _check_expected_patterns,
    parse_session_result,
)

if TYPE_CHECKING:
    from autoskillit.core import SubprocessResult, SubprocessRunner

logger = get_logger(__name__)

# Subtypes eligible for Channel B drain-race recovery.
#
# These are the failure subtypes that can arise when Claude Code defers ``type=result``
# until all background agents finish.  The deferred record is never flushed if the
# process tree is killed after Channel B fires on the session JSONL marker.
#
# TIMEOUT is excluded — it indicates a genuine time limit breach, not a drain-race.
# UNKNOWN is excluded — it indicates unrecognised CLI behaviour, not a missing record.
_CHANNEL_B_RECOVERABLE_SUBTYPES: frozenset[CliSubtype] = frozenset(
    {CliSubtype.UNPARSEABLE, CliSubtype.EMPTY_OUTPUT}
)

_TOKEN_NAME_RE: re.Pattern[str] = re.compile(r"^(\w+)")

_NUDGE_TIMEOUT: float = 60.0


def _is_path_capture_pattern(pattern: str) -> str | None:
    """Return the token name if pattern is a path-capture pattern, else None.

    Classification uses outputs[].type metadata from skill_contracts.yaml rather than
    the pattern string suffix format, so all path-capture patterns are covered regardless
    of whether they end in /.+, \\S+, .+, or any other suffix.
    """
    m = _TOKEN_NAME_RE.match(pattern)
    if not m:
        return None
    token_name = m.group(1)
    if token_name not in _RECOVERABLE_PATH_TOKENS:
        return None
    remainder = pattern[m.end() :]
    if not re.match(r"\s*=", remainder):
        return None
    return token_name


def _recover_from_separate_marker(
    session: ClaudeSessionResult,
    completion_marker: str,
) -> ClaudeSessionResult | None:
    """Attempt recovery when the model emitted the completion marker as a standalone
    final message rather than inline with its substantive output.

    Returns a reconstructed ClaudeSessionResult whose result field contains the
    combined assistant message content (including the marker), or None if recovery
    is not possible (no assistant content, or no substantive content beyond the marker).
    """
    if not session.assistant_messages:
        return None
    if not any(
        _marker_is_standalone(msg, completion_marker) for msg in session.assistant_messages
    ):
        return None
    combined = "\n\n".join(session.assistant_messages)
    stripped = combined.replace(completion_marker, "").strip()
    if not stripped:
        return None  # only the marker exists — genuine failure, do not recover
    logger.warning(
        "completion_marker_in_separate_message",
        recovery="rebuilding result from assistant_messages",
    )
    return dataclasses.replace(session, result=combined)


def _recover_block_from_assistant_messages(
    session: ClaudeSessionResult,
    expected_output_patterns: Sequence[str],
) -> ClaudeSessionResult | None:
    """When session.result lacks expected_output_patterns (drain-race condition
    on either channel), attempt to find the patterns in session.assistant_messages.
    If found, return a new ClaudeSessionResult with result reconstructed from
    assistant_messages. Return None if patterns cannot be found there either.
    """
    if not session.assistant_messages or not expected_output_patterns:
        return None
    combined = "\n\n".join(session.assistant_messages)
    if not _check_expected_patterns(combined, expected_output_patterns):
        return None
    logger.warning(
        "pattern_recovered_from_assistant_messages",
        patterns=list(expected_output_patterns),
    )
    # Preserve any content already drained into session.result before appending
    # the recovered assistant_messages block.
    recovered = (session.result + "\n\n" + combined) if session.result else combined
    return dataclasses.replace(session, result=recovered)


def _synthesize_from_write_artifacts(
    session: ClaudeSessionResult,
    expected_output_patterns: list[str],
    write_call_count: int,
    fs_writes_detected: bool = False,
) -> ClaudeSessionResult | None:
    """Synthesize missing structured output tokens from Write/Edit tool_use file_path data.

    When the session has write evidence (write_call_count >= 1) and expected_output_patterns
    contain path-capture patterns (e.g., ``plan_path\\s*=\\s*/.+``), scan tool_uses for
    Write/Edit entries with absolute file_path values. For each pattern whose token name can
    be extracted, inject ``{token_name} = {file_path}`` into session.result so that
    _compute_outcome sees the token as if the model had emitted it.

    Returns a new ClaudeSessionResult with the injected line prepended to result, or None if
    synthesis is not possible (no matching file_path, no path-capture patterns, or pattern
    already satisfied).
    """
    if write_call_count == 0:
        return None

    # Only synthesize for path-capture patterns (token_name\s*=\s*/.+).
    # Non-path patterns (verdict=, merged=) must remain text-compliance-only.

    synthesized_lines: list[str] = []
    for pattern in expected_output_patterns:
        token_name = _is_path_capture_pattern(pattern)
        if not token_name:
            continue
        # Skip if the pattern is already satisfied in the current result.
        if re.search(pattern, session.result):
            continue
        # Collect ALL absolute Write/Edit paths; use the LAST one (final deliverable).
        # Multi-artifact skills write intermediate files first, final deliverable last.
        candidate_paths = [
            t.get("file_path", "")
            for t in session.tool_uses
            if t.get("name") in {"Write", "Edit"} and t.get("file_path", "").startswith("/")
        ]
        if candidate_paths:
            synthesized_lines.append(f"{token_name} = {candidate_paths[-1]}")

    if not synthesized_lines:
        return None

    injected = "\n".join(synthesized_lines) + "\n" + session.result
    return dataclasses.replace(session, result=injected)


def _extract_missing_token_hints(
    stdout: str,
    expected_output_patterns: Sequence[str],
) -> list[tuple[str, str]]:
    """Extract (token_name, write_path) pairs for patterns missing from the result.

    Parses raw NDJSON stdout to find Write/Edit tool_use file_path entries,
    then matches them against path-capture patterns that are NOT satisfied in
    the result text. Returns the hints needed to build the nudge prompt.
    """
    session = parse_session_result(stdout)
    hints: list[tuple[str, str]] = []

    for pattern in expected_output_patterns:
        token_name = _is_path_capture_pattern(pattern)
        if not token_name:
            continue
        # Skip if already satisfied
        if re.search(pattern, session.result):
            continue
        # Collect absolute Write/Edit paths; use the LAST one (final deliverable)
        candidate_paths = [
            t.get("file_path", "")
            for t in session.tool_uses
            if t.get("name") in {"Write", "Edit"} and t.get("file_path", "").startswith("/")
        ]
        if candidate_paths:
            hints.append((token_name, candidate_paths[-1]))

    return hints


async def _attempt_contract_nudge(
    skill_result: SkillResult,
    subprocess_result: SubprocessResult,
    expected_output_patterns: Sequence[str],
    completion_marker: str,
    cwd: str,
    runner: SubprocessRunner,
) -> SkillResult | None:
    """Attempt a lightweight resume nudge to recover missing structured output tokens.

    When ``_build_skill_result`` returns CONTRACT_RECOVERY, the model wrote the artifact
    but omitted the structured output token. Instead of a full retry, resume the same
    session with a short feedback prompt asking the model to emit the missing token.

    Returns a patched SkillResult(success=True) on success, or None to indicate the
    nudge failed and the caller should fall through to the original CONTRACT_RECOVERY path.
    """
    hints = _extract_missing_token_hints(subprocess_result.stdout, expected_output_patterns)
    if not hints:
        logger.debug("nudge_skip_no_hints")
        return None

    # Build the feedback prompt
    token_lines = "\n".join(f"{name} = {path}" for name, path in hints)
    prompt = (
        "You completed your task and wrote the output file, but you omitted the "
        "required structured output token in your final text response.\n\n"
        f"Please emit ONLY the following (no other text):\n"
        f"{token_lines}\n"
        f"{completion_marker}"
    )

    spec = build_headless_resume_cmd(
        resume_session_id=skill_result.session_id,
        prompt=prompt,
        output_format=OutputFormat.JSON,
    )

    try:
        nudge_result = await runner(
            spec.cmd,
            cwd=Path(cwd),
            timeout=_NUDGE_TIMEOUT,
            env=spec.env,
        )
    except OSError:
        logger.debug("nudge_runner_failed", exc_info=True)
        return None
    except Exception:
        logger.warning("nudge_runner_failed_unexpected", exc_info=True)
        return None

    # Parse the nudge response and check for the missing patterns
    nudge_session = parse_session_result(nudge_result.stdout)
    combined_result = skill_result.result + "\n" + nudge_session.result

    if not _check_expected_patterns(combined_result, list(expected_output_patterns)):
        logger.debug(
            "nudge_patterns_not_found",
            nudge_result_len=len(nudge_session.result),
        )
        return None

    logger.info(
        "nudge_recovery_success",
        session_id=skill_result.session_id,
        nudge_output_count=nudge_session.token_usage.get("output_tokens", 0)
        if nudge_session.token_usage
        else 0,
    )
    return dataclasses.replace(
        skill_result,
        success=True,
        result=combined_result,
        subtype="success",
        needs_retry=False,
        retry_reason=RetryReason.NONE,
        token_usage=_merge_token_usage(skill_result.token_usage, nudge_session.token_usage),
    )


def _merge_token_usage(
    base: dict[str, object] | None,
    nudge: dict[str, object] | None,
) -> dict[str, object] | None:
    """Additively merge token usage dicts from main session and nudge."""
    if base is None:
        return nudge
    if nudge is None:
        return base
    merged = dict(base)
    for key in (
        "input_tokens",
        "output_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
    ):
        base_val = base.get(key, 0)
        nudge_val = nudge.get(key, 0)
        if isinstance(base_val, (int, float)) and isinstance(nudge_val, (int, float)):
            merged[key] = base_val + nudge_val
    return merged
