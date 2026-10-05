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


def token_measure(value: int | None, *, state: str | None = None) -> dict[str, Any]:
    if state is None:
        state = "unknown" if value is None else ("measured_zero" if value == 0 else "measured")
    return {"state": state, "value": value}


def subagent_row(
    child_id: str,
    *,
    parent: dict[str, Any],
    role: str,
    skill: str,
    provider: str,
    input_tokens: int | None,
    output_tokens: int | None,
    cache_read_tokens: int | None,
    cache_write_tokens: int | None,
    tool_counts: dict[str, int],
    transcript_state: str = "observed",
    usage_state: str = "observed",
) -> dict[str, Any]:
    """Build one canonical child fact linked to its parent cohort row."""
    return {
        "child_id": child_id,
        "role": role,
        "parent_session_key": parent["key"],
        "native_parent_session_id": parent["session_id"],
        "skill": skill,
        "provider": provider,
        "model": "test-child-model",
        "actor_level": "L0",
        "time_ms": parent["time_ms"],
        "level": parent["level"],
        "recipe": parent["recipe"],
        "step": parent["step"],
        "token_usage": {
            "input_tokens": token_measure(input_tokens),
            "output_tokens": token_measure(output_tokens),
            "cache_read_tokens": token_measure(cache_read_tokens),
            "cache_write_tokens": token_measure(cache_write_tokens),
        },
        "tool_counts": tool_counts,
        "transcript_state": transcript_state,
        "usage_state": usage_state,
    }
