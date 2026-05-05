"""JSONL parsing utilities for subprocess output monitoring."""

from __future__ import annotations

from autoskillit.core import ClaudeContentBlockType, get_logger

logger = get_logger(__name__)


def _marker_is_standalone(text: str, marker: str) -> bool:
    """Check if the marker appears as a standalone line, not embedded in prose."""
    for text_line in text.splitlines():
        if text_line.strip() == marker:
            return True
    return False


def _jsonl_contains_marker(
    content: str,
    marker: str,
    record_types: frozenset[str],
) -> bool:
    """Check if any JSONL record of an allowed type contains the marker.

    Parses each line as JSON and extracts the content field based on the
    record type — ``message.content`` for assistant records, ``result`` for
    result records. The marker must appear as a standalone line within the
    extracted text, not embedded in surrounding prose.

    This prevents false-fires when the model quotes the marker directive
    in discussion (e.g. ``"I will emit %%AUTOSKILLIT_COMPLETE%% when done"``).
    Thinking blocks are excluded — a marker inside a thinking block is internal
    reasoning, not a structural completion signal.
    """
    import json as _json

    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = _json.loads(line)
        except (ValueError, _json.JSONDecodeError):
            continue
        if not isinstance(obj, dict):
            continue
        record_type = obj.get("type")
        if record_type not in record_types:
            continue

        if record_type == "assistant":
            raw = (obj.get("message") or {}).get("content", "")
        elif record_type == "result":
            raw = obj.get("result", "")
        else:
            raw = " ".join(v for v in obj.values() if isinstance(v, str))

        if isinstance(raw, list):
            text = "\n".join(
                b.get("text", "")
                for b in raw
                if isinstance(b, dict)
                and ClaudeContentBlockType.from_api(b.get("type", ""))
                == ClaudeContentBlockType.TEXT
            )
        elif not isinstance(raw, str):
            text = "" if raw is None else str(raw)
        else:
            text = raw
        if _marker_is_standalone(text, marker):
            return True
    return False


def _jsonl_has_record_type(
    content: str,
    record_types: frozenset[str],
    completion_marker: str = "",
) -> bool:
    """Check if any JSONL record of an allowed type exists in content.

    Used by the heartbeat to detect when Claude CLI emits a result record
    to stdout. For ``type=result`` records, additionally requires the ``result``
    field to be a non-empty string — confirming on an empty-result envelope
    is the source of the drain-race false negative.

    When *completion_marker* is non-empty, ``type=result`` records additionally
    require the marker to appear as a standalone line in the ``result`` field.
    This prevents Channel A from confirming on premature exits where the model
    produced output but did not complete its task.
    """
    import json as _json

    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = _json.loads(line)
        except (ValueError, _json.JSONDecodeError):
            continue
        if not isinstance(obj, dict):
            continue
        record_type = obj.get("type")
        if record_type not in record_types:
            continue
        if record_type == "result":
            result_field = obj.get("result", "")
            if not (isinstance(result_field, str) and result_field.strip()):
                continue  # result absent, null, or empty — do not confirm
            if completion_marker and not _marker_is_standalone(result_field, completion_marker):
                continue  # marker configured but absent — do not confirm
        return True
    return False


def _jsonl_last_record_type(content: str) -> str | None:
    """Return the type field of the last parseable JSONL record in content, or None."""
    import json as _json

    last_type: str | None = None
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = _json.loads(line)
        except (ValueError, _json.JSONDecodeError):
            continue
        if isinstance(obj, dict):
            t = obj.get("type")
            if isinstance(t, str):
                last_type = t
    return last_type
