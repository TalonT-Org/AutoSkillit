"""Shared PreToolUse hook payload extraction for Bash and run_cmd shapes.

stdlib-only; no autoskillit imports. Sibling import within hooks/, matching
the sharing mechanism already used for _command_classification.py.
"""

from __future__ import annotations

import os
from enum import StrEnum
from typing import Any, NamedTuple

_RUN_CMD_SUFFIX = "__run_cmd"


class PayloadAnomaly(StrEnum):
    """Structural defects in a hook payload, independent of command content."""

    FIELD_CONFUSION = "field_confusion"
    NON_STRING_COMMAND = "non_string_command"
    NON_STRING_CWD = "non_string_cwd"
    RELATIVE_CWD = "relative_cwd"


class ParsedHookCommand(NamedTuple):
    """Normalized facts extracted from a PreToolUse hook payload.

    ``execution_cwd`` and ``payload_cwd`` are independent facts, never
    compared against each other here: the payload's top-level ``cwd`` is
    the session cwd, while a run_cmd tool's own ``cwd`` argument is the
    directory the command actually runs in. Bash has no separate per-call
    cwd argument, so its execution_cwd is the same top-level field as
    payload_cwd.
    """

    tool_kind: str  # "bash" | "run_cmd" | "other"
    command: str | None
    execution_cwd: str
    payload_cwd: str
    anomalies: tuple[str, ...]


def _absolute_string_or_blank(value: Any) -> tuple[str, PayloadAnomaly | None]:
    """Return (cwd, anomaly) for a raw cwd-shaped value.

    Missing/None yields ("", no anomaly) — unknown, not malformed. A
    present non-string value is NON_STRING_CWD; a present string that is
    not an absolute path is RELATIVE_CWD.
    """
    if value is None:
        return ("", None)
    if not isinstance(value, str):
        return ("", PayloadAnomaly.NON_STRING_CWD)
    if os.path.isabs(value):
        return (value, None)
    return ("", PayloadAnomaly.RELATIVE_CWD)


def parse_hook_command(data: dict[str, Any]) -> ParsedHookCommand:
    """Extract command/cwd facts from a Bash- or run_cmd-shaped PreToolUse payload.

    Returns tool_kind="other" (command=None, no anomalies) for any tool
    shape this module does not recognize — callers treat that as a pass
    (this module never denies; it only describes the payload).
    """
    raw_payload_cwd = data.get("cwd")
    payload_cwd, _ = _absolute_string_or_blank(raw_payload_cwd)

    tool_name = data.get("tool_name")
    tool_input = data.get("tool_input")
    if not isinstance(tool_name, str) or not isinstance(tool_input, dict):
        return ParsedHookCommand(
            tool_kind="other",
            command=None,
            execution_cwd="",
            payload_cwd=payload_cwd,
            anomalies=(),
        )

    if tool_name == "Bash":
        tool_kind = "bash"
        has_field_confusion = "cmd" in tool_input or "cwd" in tool_input
        raw_command = tool_input.get("command")
        raw_execution_cwd = data.get("cwd")
    elif tool_name.endswith(_RUN_CMD_SUFFIX) and "autoskillit" in tool_name:
        tool_kind = "run_cmd"
        has_field_confusion = "command" in tool_input
        raw_command = tool_input.get("cmd")
        raw_execution_cwd = tool_input.get("cwd")
    else:
        return ParsedHookCommand(
            tool_kind="other",
            command=None,
            execution_cwd="",
            payload_cwd=payload_cwd,
            anomalies=(),
        )

    anomalies: list[PayloadAnomaly] = []
    if has_field_confusion:
        anomalies.append(PayloadAnomaly.FIELD_CONFUSION)

    if raw_command is None:
        command: str | None = None
    elif isinstance(raw_command, str):
        command = raw_command
    else:
        command = None
        anomalies.append(PayloadAnomaly.NON_STRING_COMMAND)

    execution_cwd, cwd_anomaly = _absolute_string_or_blank(raw_execution_cwd)
    if cwd_anomaly is not None:
        anomalies.append(cwd_anomaly)

    return ParsedHookCommand(
        tool_kind=tool_kind,
        command=command,
        execution_cwd=execution_cwd,
        payload_cwd=payload_cwd,
        anomalies=tuple(anomalies),
    )
