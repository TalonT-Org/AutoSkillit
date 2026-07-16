"""Shared primitives for the pretty_output_hook PostToolUse formatter split.

Stdlib-only at runtime — runs under any Python interpreter without the
autoskillit package, so the four ``_fmt_*`` modules and ``pretty_output_hook.py``
all import directly from this module without going through any IL-1+ layer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Keep in sync with HOOK_DIR_COMPONENTS + HOOK_CONFIG_FILENAME in _hook_settings.py
# (stdlib-only boundary prevents a shared import).
_HOOK_CONFIG_PATH_COMPONENTS = (".autoskillit", "temp", ".hook_config.json")


@dataclass(frozen=True, slots=True)
class _DictPayload:
    data: dict[str, Any]


@dataclass(frozen=True, slots=True)
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


def _filter_pytest_output(raw: str) -> str:
    """Filter pytest boilerplate, keeping only failure-relevant lines."""
    prefixes = (
        "platform ",
        "rootdir:",
        "configfile:",
        "plugins:",
        "collecting ",
        "collected ",
        "cacheprovider",
    )
    return "\n".join(
        line
        for line in raw.splitlines()
        if line.strip() and not any(line.strip().startswith(prefix) for prefix in prefixes)
    )


def _fmt_generic(short_name: str, data: dict, _pipeline: bool) -> str:
    """Format tools without a dedicated response renderer."""
    lines = [f"## {short_name}", ""]
    for key, val in data.items():
        if isinstance(val, list):
            val = list(val)
            if not val:
                lines.append(f"{key}: []")
            elif all(isinstance(item, str) for item in val):
                lines.append(f"{key}:")
                lines.extend(f"  - {item}" for item in val[:20])
            else:
                lines.append(f"{key}:")
                for item in val[:20]:
                    if isinstance(item, dict):
                        kvs = [
                            f"{k}: {(s := str(v))[:120] + ('...' if len(s) > 120 else '')}"
                            for k, v in item.items()
                        ]
                        lines.append(f"  - {', '.join(kvs)}")
                    else:
                        compact = json.dumps(item, separators=(",", ":"))
                        rendered = compact[:2000] + "..." if len(compact) > 2000 else compact
                        lines.append(f"  - {rendered}")
            if len(val) > 20:
                lines.append(f"  ... and {len(val) - 20} more")
        elif isinstance(val, dict):
            if not val:
                lines.append(f"{key}: {{}}")
            else:
                lines.append(f"{key}:")
                for nested_key, nested_value in val.items():
                    if isinstance(nested_value, (dict, list)):
                        compact = json.dumps(nested_value, separators=(",", ":"))
                        rendered = compact[:2000] + "..." if len(compact) > 2000 else compact
                        lines.append(f"  {nested_key}: {rendered}")
                    else:
                        lines.append(f"  {nested_key}: {nested_value}")
        else:
            lines.append(f"{key}: {val}")
    return "\n".join(lines)
