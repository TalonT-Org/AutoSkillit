"""Strict and tolerant readers for the retained session index."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from autoskillit.core import default_log_dir


def iter_tolerant_session_index_lines(
    index_path: Path,
    *,
    offset: int = 0,
    complete_only: bool = False,
) -> Iterator[tuple[int, dict[str, Any] | None]]:
    """Yield each JSONL line's ending byte offset and its decoded object, if any.

    Malformed UTF-8, malformed JSON, blank lines, and non-object JSON values are
    represented by a ``None`` row. The default mode includes an unterminated
    final line at EOF for compatibility; ``complete_only`` omits it so an
    append-only source can finish that line on a later pass.
    """
    if offset < 0:
        raise ValueError("Session index offset must be nonnegative")
    if not index_path.is_file():
        return
    with index_path.open("rb") as handle:
        handle.seek(offset)
        while raw_line := handle.readline():
            end_offset = handle.tell()
            if not raw_line.endswith(b"\n") and complete_only:
                break
            try:
                line = raw_line.decode("utf-8")
            except UnicodeDecodeError:
                yield end_offset, None
                continue
            if not line.strip():
                yield end_offset, None
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                yield end_offset, None
                continue
            yield end_offset, row if isinstance(row, dict) else None


def read_tolerant_session_index_rows(index_path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for _end_offset, row in iter_tolerant_session_index_lines(index_path):
        if row is not None:
            rows.append(row)
    return rows


def find_stale_session_archive_references(log_root: Path | None = None) -> list[str]:
    root = default_log_dir() if log_root is None else Path(log_root)
    stale: list[str] = []
    seen: set[str] = set()
    for row in read_tolerant_session_index_rows(root / "sessions-archive.jsonl"):
        for field in ("cwd", "claude_code_log", "codex_log"):
            value = row.get(field)
            if isinstance(value, str):
                path = Path(value)
                if path.is_absolute() and not path.exists() and value not in seen:
                    seen.add(value)
                    stale.append(value)
    return stale


def read_session_index_rows(
    index_path: Path,
    *,
    max_bytes: int = 2_000_000,
) -> list[dict[str, Any]]:
    """Read a retained session index strictly within a byte budget."""
    if max_bytes <= 0:
        raise ValueError("Session index byte budget must be positive")
    if not index_path.is_file():
        return []
    with index_path.open("rb") as handle:
        data = handle.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError("Session index exceeds byte budget")
    if data and not data.endswith(b"\n"):
        raise ValueError("Session index ends with an incomplete row")
    try:
        lines = data.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise ValueError("Session index is not valid UTF-8") from exc
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Malformed session index row {line_number}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"Session index row {line_number} is not an object")
        rows.append(row)
    return rows
