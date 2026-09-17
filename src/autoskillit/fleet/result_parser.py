"""L3 result block parser with Channel B JSONL fallback."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import regex as re

from autoskillit.core import ClaudeContentBlockType, get_logger
from autoskillit.execution import _collapse_hr_split_delimiters

logger = get_logger(__name__)

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


@dataclass(frozen=True, slots=True)
class L3ParseResult:
    """Tri-state outcome of parsing an L3 result block."""

    outcome: Literal["completed_clean", "completed_dirty", "no_sentinel"]
    payload: dict | None
    raw_body: str | None
    parse_error: str | None
    source: Literal["stdout", "assistant_messages_jsonl", "additional_jsonl", "sidecar"]


def _is_parent_assistant_record(obj: dict) -> bool:
    if obj.get("type") != "assistant":
        return False
    if obj.get("subagent_type"):
        return False
    msg = obj.get("message")
    if isinstance(msg, dict) and msg.get("model") == "<synthetic>":
        return False
    return True


def _extract_text_from_jsonl(path: Path, skip_lines: int = 0) -> str:
    """Read a Claude Code session JSONL and extract assistant text blocks.

    Reads all lines, filters for type=="assistant" records, extracts text
    from message.content blocks. Returns concatenated text (oldest-first).

    When *skip_lines* > 0, the first *skip_lines* lines are skipped before
    extraction — used to scope reads to content written after a resume boundary.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return ""

    texts: list[str] = []
    for line in raw.splitlines()[skip_lines:]:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        if not _is_parent_assistant_record(obj):
            continue
        msg = obj.get("message")
        if not isinstance(msg, dict):
            continue
        content = msg.get("content", "")
        if isinstance(content, list):
            text = "\n".join(
                block.get("text", "")
                for block in content
                if isinstance(block, dict)
                and ClaudeContentBlockType.from_api(block.get("type", ""))
                == ClaudeContentBlockType.TEXT
            ).strip()
        else:
            text = str(content).strip()
        if text:
            texts.append(text)

    return "\n\n".join(texts)


def _scan_for_sentinel(
    text: str,
    open_sentinel: str,
    close_sentinel: str,
) -> tuple[int, int] | None:
    """Find the last valid (open, close) sentinel pair in text.

    Returns (open_pos, close_pos) indices or None if not found / out-of-order.
    """
    open_pos = text.rfind(open_sentinel)
    if open_pos == -1:
        return None
    search_from = open_pos + len(open_sentinel)
    close_pos = text.find(close_sentinel, search_from)
    if close_pos == -1:
        return None
    return (open_pos, close_pos)


def _parse_body(
    text: str,
    open_pos: int,
    close_pos: int,
    open_sentinel: str,
    source: Literal["stdout", "assistant_messages_jsonl", "additional_jsonl", "sidecar"],
) -> L3ParseResult:
    """Extract body between sentinels and attempt JSON decode."""
    after_open = open_pos + len(open_sentinel)
    body = text[after_open:close_pos].strip()

    if not body:
        return L3ParseResult(
            outcome="completed_dirty",
            payload=None,
            raw_body="",
            parse_error="empty body between sentinels",
            source=source,
        )

    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        return L3ParseResult(
            outcome="completed_dirty",
            payload=None,
            raw_body=body,
            parse_error=str(exc),
            source=source,
        )

    if not isinstance(parsed, dict):
        return L3ParseResult(
            outcome="completed_dirty",
            payload=None,
            raw_body=body,
            parse_error=f"expected JSON object, got {type(parsed).__name__}",
            source=source,
        )

    return L3ParseResult(
        outcome="completed_clean",
        payload=parsed,
        raw_body=None,
        parse_error=None,
        source=source,
    )


def _parse_ordered_sources(
    sentinel_dispatch_ids: Sequence[str],
    sources: Sequence[
        tuple[
            str,
            Literal["stdout", "assistant_messages_jsonl", "additional_jsonl", "sidecar"],
        ]
    ],
) -> L3ParseResult | None:
    """Parse the first result block found across ordered IDs and sources."""
    for dispatch_id in sentinel_dispatch_ids:
        open_sentinel = f"---l3-result::{dispatch_id}---"
        close_sentinel = f"---end-l3-result::{dispatch_id}---"
        for text, source in sources:
            positions = _scan_for_sentinel(text, open_sentinel, close_sentinel)
            if positions is not None:
                open_pos, close_pos = positions
                return _parse_body(text, open_pos, close_pos, open_sentinel, source)
    return None


def parse_l3_result_block(
    stdout: str,
    expected_dispatch_id: str,
    assistant_messages_path: Path | None = None,
    prior_dispatch_ids: Sequence[str] | None = None,
    additional_jsonl_paths: Sequence[Path] | None = None,
    resume_line_offset: int = 0,
) -> L3ParseResult:
    """Parse an L3 result block from food truck dispatch output.

    Strips ANSI codes, scans stdout for the last occurrence of the sentinel
    block keyed to expected_dispatch_id, and returns a tri-state outcome.
    Falls back to reading the Channel B JSONL file when stdout is truncated.
    When prior_dispatch_ids is provided, additional fallback scans are performed
    for each prior ID after the primary and JSONL scans fail — this handles
    the resume case where the LLM may emit a sentinel keyed to an earlier ID.
    When additional_jsonl_paths is provided, those files are scanned after all
    other stages fail — this handles the resume case where the sentinel lives
    in a prior session's JSONL file rather than the current session's file.
    """
    cleaned = _collapse_hr_split_delimiters(_ANSI_RE.sub("", stdout))
    sources: list[
        tuple[
            str,
            Literal["stdout", "assistant_messages_jsonl", "additional_jsonl", "sidecar"],
        ]
    ] = [(cleaned, "stdout")]
    if assistant_messages_path is not None:
        jsonl_text = _collapse_hr_split_delimiters(
            _extract_text_from_jsonl(assistant_messages_path, skip_lines=resume_line_offset)
        )
        sources.append((jsonl_text, "assistant_messages_jsonl"))

    parsed = _parse_ordered_sources((expected_dispatch_id,), sources)
    if parsed is not None:
        return parsed

    # Fallback scan through prior dispatch_ids (defense-in-depth for resume)
    if prior_dispatch_ids:
        parsed = _parse_ordered_sources(prior_dispatch_ids, sources)
        if parsed is not None:
            return parsed

    # Stage 4: scan additional JSONL paths (cross-session recovery for resume)
    if additional_jsonl_paths:
        for jsonl_path in additional_jsonl_paths:
            additional_text = _collapse_hr_split_delimiters(_extract_text_from_jsonl(jsonl_path))
            if not additional_text:
                continue
            parsed = _parse_ordered_sources(
                (expected_dispatch_id,), ((additional_text, "additional_jsonl"),)
            )
            if parsed is not None:
                logger.debug("cross-session recovery matched %s", jsonl_path)
                return parsed

    return L3ParseResult(
        outcome="no_sentinel",
        payload=None,
        raw_body=None,
        parse_error=None,
        source="stdout",
    )
