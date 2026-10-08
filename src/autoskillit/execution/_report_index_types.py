"""Private typed row contracts for the derived report index."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, Final, TypedDict

from autoskillit.core import SerializedTokenMeasure

SESSION_KIND: Final[str] = "session"
REQUEST_KIND: Final[str] = "request"
TOOL_KIND: Final[str] = "tool"
SUBAGENT_KIND: Final[str] = "subagent"
TURN_KIND: Final[str] = "turn"


class ReportRowBase(TypedDict):
    """Common fields carried by each report-index fact row."""

    schema_version: int
    kind: str
    key: str
    session_id: str | None
    time_ms: int | None


class ReportSessionRow(ReportRowBase):
    """Session facts from one session or archive walk item.

    The provider is persisted verbatim from ``provider_used``; token
    classification casefolds it only for the source-pair lookup.
    """

    harness: str
    provider: str
    model: str | None
    skill: str | None
    recipe: str | None
    step: str | None
    level: str | None
    kitchen_id: str | None
    order_id: str | None
    dispatch_id: str | None
    campaign_id: str | None
    caller_session_id: str | None
    parent_session_id: str | None
    success: bool | None
    subtype: str | None
    adjudication_reason: str | None
    adjudication_subtype: str | None
    duration_seconds: float | None
    input_tokens: SerializedTokenMeasure
    output_tokens: SerializedTokenMeasure
    cache_read_tokens: SerializedTokenMeasure
    cache_write_tokens: SerializedTokenMeasure
    assistant_turn_count: int | None
    tool_counts: dict[str, int] | None
    turn_usage_state: str | None
    turn_usage_reason: str | None


class ReportRequestRow(ReportRowBase):
    """Request facts derived from a Claude Code ``api_request`` log record."""

    harness: str
    request_id: str
    agent_name: str | None
    query_source: str | None
    model: str | None
    event_sequence: int | None
    input_tokens: int | None
    output_tokens: int | None
    cache_read_tokens: int | None
    cache_write_tokens: int | None
    cost_usd: float | None
    duration_ms: int | None


class ReportToolRow(ReportRowBase):
    """Tool facts derived from a Claude Code ``tool_result`` log record."""

    harness: str
    agent_name: str | None
    tool_name: str | None
    tool_use_id: str | None
    error_type: str | None
    success: bool | None
    duration_ms: int | None
    tool_input_size_bytes: int | None
    tool_result_size_bytes: int | None
    event_sequence: int | None


class ReportSubagentRow(ReportRowBase):
    """Native child or OTLP completion facts, kept in one subagent fact family."""

    harness: str
    agent_type: str | None
    model: str | None
    final_model: str | None
    model_swapped: bool | None
    event_sequence: int | None
    child_id: str | None
    native_parent_session_id: str | None
    parent_session_key: str | None
    role: str | None
    actor_level: str | None
    provider: str
    skill: str | None
    recipe: str | None
    step: str | None
    level: str | None
    token_usage: dict[str, SerializedTokenMeasure]
    tool_counts: dict[str, int] | None
    transcript_state: str | None
    usage_state: str | None
    parent_context_spans: list[dict[str, Any]]


class ReportTurnRow(ReportRowBase):
    """One owner-attributed, already-normalized session turn usage fact."""

    source_id: str
    session_key: str
    ordinal: int
    request_id: str | None
    message_id: str | None
    harness: str
    provider: str
    model: str | None
    skill: str | None
    recipe: str | None
    step: str | None
    level: str | None
    input_tokens: SerializedTokenMeasure
    output_tokens: SerializedTokenMeasure
    cache_read_tokens: SerializedTokenMeasure
    cache_write_tokens: SerializedTokenMeasure
    context_window_tokens: int | None
    context_fraction: float | None


REPORT_ROW_TYPES: Mapping[str, type] = MappingProxyType(
    {
        SESSION_KIND: ReportSessionRow,
        REQUEST_KIND: ReportRequestRow,
        TOOL_KIND: ReportToolRow,
        SUBAGENT_KIND: ReportSubagentRow,
        TURN_KIND: ReportTurnRow,
    }
)
