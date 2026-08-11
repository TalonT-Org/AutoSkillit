"""Strict and tolerant readers for the retained session index."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def read_tolerant_session_index_rows(index_path: Path) -> list[dict[str, Any]]:
    if not index_path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in index_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


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
