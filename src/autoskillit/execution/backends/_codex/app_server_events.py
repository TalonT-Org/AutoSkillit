"""JSON-RPC-to-exec-shape adapter and the (moved) Codex streaming parser.

``_app_server_to_exec_event`` is the one canonical-event-shape adapter: it
converts one parsed app-server JSON-RPC line (a notification) into the same
``{"type": ..., ...}`` dict shape ``codex exec --json``'s NDJSON stream
already produces, so ``CodexStreamParser.parse_line`` and
``_scan_codex_ndjson`` (in the sibling ``_codex_parse.py``) need exactly one
parsing implementation regardless of which transport produced the line. A
JSON-RPC *response* (an ``id`` with no ``method``) and any object that is
neither app-server- nor exec-shaped both adapt to ``None`` — the caller
treats that identically to an already-ignored record, never as "unknown".
An unrecognized *notification* method instead echoes its method name as
``"type"``, so the existing ``CodexEventType.from_ndjson`` -> ``UNKNOWN``
fallback (and its counter/warning) applies uniformly without a second
unknown-tracking mechanism here.

``CodexStreamParser`` moved here (from ``_codex_parse.py``, now nearly at
its 750-line diff cap) verbatim, plus the usage-tracking this adapter
requires; ``_codex_parse.py`` imports and re-exports the same class so it
remains the sole parser implementation its production and test consumers
already depend on.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from functools import partial
from typing import Any

from autoskillit.core import (
    BackendEventKind,
    CodexEventData,
    CodexEventType,
    CodexItemType,
    SessionEvent,
    fast_loads,
    get_logger,
)
from autoskillit.execution.process import _marker_is_standalone

logger = get_logger(__name__)

#: app-server's own camelCase item-type spelling -> the canonical CodexItemType
#: value. "plan" is app-server's name for what exec calls "todo_list".
_ITEM_TYPE_CAMEL_TO_SNAKE: Mapping[str, str] = {
    "agentMessage": CodexItemType.AGENT_MESSAGE.value,
    "commandExecution": CodexItemType.COMMAND_EXECUTION.value,
    "fileChange": CodexItemType.FILE_CHANGE.value,
    "mcpToolCall": CodexItemType.MCP_TOOL_CALL.value,
    "reasoning": CodexItemType.REASONING.value,
    "plan": CodexItemType.TODO_LIST.value,
    "webSearch": CodexItemType.WEB_SEARCH.value,
    "collabAgentToolCall": CodexItemType.COLLAB_TOOL_CALL.value,
}


def _normalize_item(item: Mapping[str, Any]) -> dict[str, Any]:
    normalized = dict(item)
    raw_type = item.get("type")
    if isinstance(raw_type, str) and raw_type in _ITEM_TYPE_CAMEL_TO_SNAKE:
        normalized["type"] = _ITEM_TYPE_CAMEL_TO_SNAKE[raw_type]
    return normalized


def _normalize_usage(usage: Mapping[str, Any]) -> dict[str, Any]:
    """camelCase app-server usage keys -> the canonical snake_case ones."""
    return {
        "input_tokens": usage.get("inputTokens"),
        "cached_input_tokens": usage.get("cachedInputTokens"),
        "output_tokens": usage.get("outputTokens"),
    }


def _handle_thread_started(params: Mapping[str, Any]) -> dict[str, Any]:
    thread = params.get("thread")
    thread_id = thread.get("id") if isinstance(thread, Mapping) else None
    return {"type": CodexEventType.THREAD_STARTED.value, "thread_id": thread_id or ""}


def _handle_item_event(canonical_type: str, params: Mapping[str, Any]) -> dict[str, Any]:
    item = params.get("item")
    return {
        "type": canonical_type,
        "item": _normalize_item(item) if isinstance(item, Mapping) else {},
    }


def _handle_turn_started(params: Mapping[str, Any]) -> dict[str, Any]:
    del params
    return {"type": CodexEventType.TURN_STARTED.value}


def _handle_turn_completed(params: Mapping[str, Any]) -> dict[str, Any]:
    turn = params.get("turn")
    status = turn.get("status") if isinstance(turn, Mapping) else None
    if status == "completed":
        return {"type": CodexEventType.TURN_COMPLETED.value}
    return {
        "type": CodexEventType.TURN_FAILED.value,
        "error": {"message": status or "", "code": ""},
    }


def _handle_error(params: Mapping[str, Any]) -> dict[str, Any]:
    if params.get("willRetry"):
        # An unavailable-but-retryable server error is not a session event at
        # all -- ignored exactly like item/started, never terminal.
        return {"type": CodexEventType.ITEM_STARTED.value}
    error_payload = params.get("error")
    message = ""
    code = ""
    if isinstance(error_payload, Mapping):
        message = error_payload.get("message") or ""
        code = error_payload.get("code") or ""
    return {"type": CodexEventType.ERROR.value, "message": message, "code": code}


def _handle_token_usage_updated(params: Mapping[str, Any]) -> dict[str, Any]:
    # todo_list is an inert item type for every existing consumer: this
    # carries usage as a nonterminal item.updated without inventing a new
    # canonical event.
    event: dict[str, Any] = {
        "type": CodexEventType.ITEM_UPDATED.value,
        "item": {"type": CodexItemType.TODO_LIST.value},
    }
    token_usage = params.get("tokenUsage")
    if not isinstance(token_usage, Mapping):
        return event
    last = token_usage.get("last")
    if isinstance(last, Mapping):
        event["last_usage"] = _normalize_usage(last)
    total = token_usage.get("total")
    if isinstance(total, Mapping):
        event["cumulative_usage"] = _normalize_usage(total)
    return event


_APP_SERVER_METHOD_HANDLERS: Mapping[str, Callable[[Mapping[str, Any]], dict[str, Any]]] = {
    "thread/started": _handle_thread_started,
    "item/started": partial(_handle_item_event, CodexEventType.ITEM_STARTED.value),
    "item/completed": partial(_handle_item_event, CodexEventType.ITEM_COMPLETED.value),
    "item/updated": partial(_handle_item_event, CodexEventType.ITEM_UPDATED.value),
    "turn/started": _handle_turn_started,
    "turn/completed": _handle_turn_completed,
    "error": _handle_error,
    "thread/tokenUsage/updated": _handle_token_usage_updated,
}


def _app_server_to_exec_event(obj: Mapping[str, Any]) -> dict[str, Any] | None:
    """Adapt one parsed line into the canonical exec event shape, or None.

    ``None`` covers both a JSON-RPC response (no ``method``, no ``type`` —
    the driver's own concern, never a session event) and an empty/malformed
    object; an object already in exec shape (no ``method``, has ``type``)
    passes through unchanged so exec- and app-server-transport launches
    share this one parsing path.
    """
    method = obj.get("method")
    if method is None:
        return dict(obj) if "type" in obj else None
    params = obj.get("params")
    params = params if isinstance(params, Mapping) else {}
    handler = _APP_SERVER_METHOD_HANDLERS.get(method)
    if handler is None:
        return {"type": method}
    return handler(params)


@dataclass(slots=True)
class CodexStreamParser:
    """Stateful NDJSON/JSON-RPC stream parser for Codex CLI output.

    One instance per session — accumulates marker detection and the latest
    observed token-usage state across ``parse_line()`` calls. Not reusable
    across sessions. Handles both ``codex exec`` NDJSON and ``codex
    app-server`` JSON-RPC lines transparently via ``_app_server_to_exec_event``.
    """

    completion_marker: str = ""
    _saw_marker: bool = field(default=False, init=False, repr=False)
    ndjson_unknown_event_count: int = field(default=0, init=False, repr=False)
    ndjson_unknown_item_count: int = field(default=0, init=False, repr=False)
    _last_usage: Mapping[str, Any] | None = field(default=None, init=False, repr=False)
    _cumulative_usage: Mapping[str, Any] | None = field(default=None, init=False, repr=False)

    def _check_marker_text(self, text: str) -> None:
        if self.completion_marker and _marker_is_standalone(text, self.completion_marker):
            self._saw_marker = True

    def parse_line(self, line: str) -> SessionEvent | None:
        line = line.strip()
        if not line:
            return None
        try:
            raw_obj = fast_loads(line)
        except (ValueError, TypeError):
            return None
        if not isinstance(raw_obj, dict):
            return None
        obj = _app_server_to_exec_event(raw_obj)
        if obj is None:
            return None

        event_type = CodexEventType.from_ndjson(obj.get("type", ""))

        if event_type == CodexEventType.THREAD_STARTED:
            return SessionEvent(
                kind=BackendEventKind.SESSION_META,
                is_terminal=False,
                has_marker=False,
                session_id=obj.get("thread_id", "") or None,
            )

        if event_type == CodexEventType.SESSION_META:
            return SessionEvent(
                kind=BackendEventKind.SESSION_META,
                is_terminal=False,
                has_marker=False,
                session_id=obj.get("payload", {}).get("id", "") or None,
            )

        if event_type in (CodexEventType.TURN_STARTED, CodexEventType.ITEM_STARTED):
            return SessionEvent(
                kind=BackendEventKind.IGNORED,
                is_terminal=False,
                has_marker=False,
            )

        if event_type == CodexEventType.ITEM_UPDATED:
            last_usage = obj.get("last_usage")
            if isinstance(last_usage, dict):
                self._last_usage = last_usage
            cumulative_usage = obj.get("cumulative_usage")
            if isinstance(cumulative_usage, dict):
                self._cumulative_usage = cumulative_usage
            return SessionEvent(
                kind=BackendEventKind.IGNORED,
                is_terminal=False,
                has_marker=False,
            )

        if event_type == CodexEventType.ITEM_COMPLETED:
            item = obj.get("item", {})
            if not isinstance(item, dict):
                return SessionEvent(
                    kind=BackendEventKind.IGNORED,
                    is_terminal=False,
                    has_marker=False,
                )
            item_type = CodexItemType.from_ndjson(item.get("type", ""))

            if item_type == CodexItemType.AGENT_MESSAGE:
                self._check_marker_text(item.get("text", ""))
                return SessionEvent(
                    kind=BackendEventKind.TOOL_OUTPUT,
                    is_terminal=False,
                    has_marker=False,
                    backend_data=CodexEventData(
                        record_type="item.completed",
                        thread_id="",
                        item_type="agent_message",
                        raw=obj,
                    ),
                )

            if item_type == CodexItemType.MESSAGE:
                for block in item.get("content", []):
                    if isinstance(block, dict) and block.get("type") == "text":
                        self._check_marker_text(block.get("text", ""))
                return SessionEvent(
                    kind=BackendEventKind.TOOL_OUTPUT,
                    is_terminal=False,
                    has_marker=False,
                    backend_data=CodexEventData(
                        record_type="item.completed",
                        thread_id="",
                        item_type="message",
                        raw=obj,
                    ),
                )

            if item_type in (
                CodexItemType.FILE_CHANGE,
                CodexItemType.COMMAND_EXECUTION,
                CodexItemType.FUNCTION_CALL,
                CodexItemType.MCP_TOOL_CALL,
                CodexItemType.COLLAB_TOOL_CALL,
                CodexItemType.WEB_SEARCH,
            ):
                return SessionEvent(
                    kind=BackendEventKind.TOOL_OUTPUT,
                    is_terminal=False,
                    has_marker=False,
                    backend_data=CodexEventData(
                        record_type="item.completed",
                        thread_id="",
                        item_type=item_type.value,
                        raw=obj,
                    ),
                )

            if item_type in (CodexItemType.REASONING, CodexItemType.TODO_LIST):
                return SessionEvent(
                    kind=BackendEventKind.IGNORED,
                    is_terminal=False,
                    has_marker=False,
                )

            self.ndjson_unknown_item_count += 1
            logger.warning("codex_ndjson_unknown_item_type", item_type=item.get("type", ""))
            return SessionEvent(
                kind=BackendEventKind.IGNORED,
                is_terminal=False,
                has_marker=False,
            )

        if event_type == CodexEventType.TURN_COMPLETED:
            usage = obj.get("usage") or self._last_usage
            return SessionEvent(
                kind=BackendEventKind.COMPLETION,
                is_terminal=True,
                has_marker=self._saw_marker,
                backend_data=CodexEventData(
                    record_type="turn.completed",
                    thread_id="",
                    item_type="",
                    raw=obj,
                    usage=usage,
                    cumulative_usage=self._cumulative_usage,
                ),
            )

        if event_type == CodexEventType.TURN_FAILED:
            return SessionEvent(
                kind=BackendEventKind.COMPLETION,
                is_terminal=True,
                has_marker=False,
                backend_data=CodexEventData(
                    record_type="turn.failed",
                    thread_id="",
                    item_type="",
                    raw=obj,
                ),
            )

        if event_type == CodexEventType.ERROR:
            return SessionEvent(
                kind=BackendEventKind.ERROR,
                is_terminal=True,
                has_marker=False,
                backend_data=CodexEventData(
                    record_type="error",
                    thread_id="",
                    item_type="",
                    raw=obj,
                ),
            )

        self.ndjson_unknown_event_count += 1
        logger.warning("codex_ndjson_unknown_event_type", type=obj.get("type", ""))
        return SessionEvent(
            kind=BackendEventKind.IGNORED,
            is_terminal=False,
            has_marker=False,
        )


__all__ = ["CodexStreamParser", "_app_server_to_exec_event"]
