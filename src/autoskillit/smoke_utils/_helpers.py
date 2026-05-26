"""Shared JSON loading helpers for smoke_utils sub-modules."""

from __future__ import annotations

import json
from pathlib import Path


def _load_json(src: str) -> list | dict:
    """Load JSON from a string or file path. Returns a list or dict."""
    try:
        return json.loads(src)
    except (json.JSONDecodeError, TypeError) as string_err:
        try:
            return json.loads(Path(src).read_text())
        except (OSError, json.JSONDecodeError) as file_err:
            raise file_err from string_err


def try_load_json(path: Path) -> dict | None:
    """Attempt to load JSON from path, returning None on failure."""
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
