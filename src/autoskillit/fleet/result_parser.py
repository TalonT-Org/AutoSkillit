"""L3 result block parser with Channel B JSONL fallback."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from autoskillit.core import get_logger

logger = get_logger(__name__)

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


@dataclass(frozen=True, slots=True)
class L3ParseResult:
    """Tri-state outcome of parsing an L3 result block."""

    outcome: Literal["completed_clean", "completed_dirty", "no_sentinel"]
    payload: dict | None
    raw_body: str | None
    parse_error: str | None
    source: Literal["stdout", "assistant_messages_jsonl"]


def _extract_text_from_jsonl(path: Path) -> str:
    """Read a Claude Code session JSONL and extract assistant text blocks.

    Reads all lines, filters for type=="assistant" records, extracts text
    from message.content blocks. Returns concatenated text (oldest-first).
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return ""

    texts: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        if obj.get("type") != "assistant":
            continue
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
    source: Literal["stdout", "assistant_messages_jsonl"],
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


def parse_l3_result_block(
    stdout: str,
    expected_dispatch_id: str,
    assistant_messages_path: Path | None = None,
) -> L3ParseResult:
    """Parse an L3 result block from food truck dispatch output.

    Strips ANSI codes, scans stdout for the last occurrence of the sentinel
    block keyed to expected_dispatch_id, and returns a tri-state outcome.
    Falls back to reading the Channel B JSONL file when stdout is truncated.
    """
    open_sentinel = f"---l3-result::{expected_dispatch_id}---"
    close_sentinel = f"---end-l3-result::{expected_dispatch_id}---"

    cleaned = _ANSI_RE.sub("", stdout)

    positions = _scan_for_sentinel(cleaned, open_sentinel, close_sentinel)
    if positions is not None:
        open_pos, close_pos = positions
        return _parse_body(cleaned, open_pos, close_pos, open_sentinel, "stdout")

    if assistant_messages_path is None:
        return L3ParseResult(
            outcome="no_sentinel",
            payload=None,
            raw_body=None,
            parse_error=None,
            source="stdout",
        )

    jsonl_text = _extract_text_from_jsonl(assistant_messages_path)
    positions = _scan_for_sentinel(jsonl_text, open_sentinel, close_sentinel)
    if positions is not None:
        open_pos, close_pos = positions
        return _parse_body(
            jsonl_text, open_pos, close_pos, open_sentinel, "assistant_messages_jsonl"
        )

    return L3ParseResult(
        outcome="no_sentinel",
        payload=None,
        raw_body=None,
        parse_error=None,
        source="stdout",
    )
