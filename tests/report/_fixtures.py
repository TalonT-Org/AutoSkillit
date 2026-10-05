"""Deterministic sample data shared by deck tests."""

from datetime import UTC, datetime
from typing import Any

from autoskillit.report.deck._registry import SESSION_COLUMNS

DECK_GENERATED_AT = datetime(2026, 10, 4, tzinfo=UTC)


def session_row(key: str, **fields: Any) -> dict[str, Any]:
    row: dict[str, Any] = dict.fromkeys(SESSION_COLUMNS)
    row.update(
        {
            "schema_version": 1,
            "kind": "session",
            "key": key,
            "session_id": key,
            "harness": "claude-code",
            "provider": "anthropic",
            "input_tokens": {"state": "unknown", "value": None},
            "output_tokens": {"state": "unknown", "value": None},
            "cache_write_tokens": {"state": "unknown", "value": None},
            "cache_read_tokens": {"state": "unknown", "value": None},
        }
    )
    row.update(fields)
    return row
