"""Shared primitives for the pretty_output_hook PostToolUse formatter split.

Stdlib-only at runtime — runs under any Python interpreter without the
autoskillit package, so the four ``_fmt_*`` modules and ``pretty_output_hook.py``
all import directly from this module without going through any L1+ layer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_HOOK_CONFIG_PATH_COMPONENTS = (".autoskillit", "temp", ".hook_config.json")


def _read_hook_config() -> dict[str, Any]:
    """Read the quota_guard section from the session-bridge config file.

    Returns an empty dict on any error (file absent, malformed JSON, wrong type).
    Single shared implementation for all hook scripts that need quota_guard
    data. Fail-open: absence of the file means no kitchen is active, handled
    gracefully by all callers.
    """
    path = Path.cwd().joinpath(*_HOOK_CONFIG_PATH_COMPONENTS)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("quota_guard", {}) if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, AttributeError, TypeError):
        return {}


@dataclass(frozen=True)
class _DictPayload:
    data: dict[str, Any]


@dataclass(frozen=True)
class _PlainTextPayload:
    text: str


_Payload = _DictPayload | _PlainTextPayload
_CHECK_MARK = "\u2713"  # ✓
_CROSS_MARK = "\u2717"  # ✗
_WARN_MARK = "\u26a0"  # ⚠


def _is_pipeline_mode() -> bool:
    """Check if kitchen is open (pipeline mode) by hook config file presence."""
    config_path = Path.cwd().joinpath(*_HOOK_CONFIG_PATH_COMPONENTS)
    return config_path.is_file()


def _fmt_tokens(n: int | None) -> str:
    """Format a token count as compact string (45.2k, 1.2M, etc.)."""
    if n is None or n == 0:
        return "0"
    if not isinstance(n, (int, float)):
        return "0"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def _extract_tool_short_name(tool_name: str) -> str:
    """Extract short tool name from full MCP tool name.

    "mcp__plugin_autoskillit_autoskillit__run_skill" -> "run_skill"
    Falls back to the full tool_name if no __ separator found.
    """
    return tool_name.rsplit("__", 1)[-1] if "__" in tool_name else tool_name
