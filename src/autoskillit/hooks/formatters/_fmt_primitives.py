"""Shared primitives for the standalone, stdlib-only pretty-output hook."""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Any

_HOOK_CONFIG_PATH_COMPONENTS = (".autoskillit", "temp", ".hook_config.json")
_RESPONSE_SPILL_EXPORTS = (
    "_RESPONSE_BACKSTOP_EXEMPTION_REGISTRY",
    "_RESPONSE_BACKSTOP_EXEMPTION_REGISTRY_DIGEST",
    "_RESPONSE_SPILL_METADATA_KEY",
    "_RESPONSE_SPILL_METADATA_KEYS",
    "_RESPONSE_SPILL_REASONS",
    "_RESPONSE_SPILL_SCHEMA_DIGEST",
    "_RESPONSE_SPILL_SCHEMA_VERSION",
    "_validate_response_spill_metadata",
)


def __getattr__(name: str) -> Any:
    """Lazily re-export spill contracts in package and standalone hook modes."""
    if name not in _RESPONSE_SPILL_EXPORTS:
        raise AttributeError(name)
    module_name = f"{__package__}._fmt_response_spill" if __package__ else "_fmt_response_spill"
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value


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


def _fmt_generic(
    short_name: str,
    data: dict,
    _pipeline: bool,
    *,
    artifact_backed: bool,
) -> str:
    """Format generic data, reducing it only when a complete artifact is trusted."""
    lines = [f"## {short_name}", ""]
    for key, val in data.items():
        if isinstance(val, list):
            val = list(val)
            visible = val[:20] if artifact_backed else val
            if not val:
                lines.append(f"{key}: []")
            elif all(isinstance(item, str) for item in val):
                lines.append(f"{key}:")
                lines.extend(f"  - {item}" for item in visible)
            else:
                lines.append(f"{key}:")
                for item in visible:
                    if isinstance(item, dict):
                        kvs = []
                        for nested_key, nested_value in item.items():
                            rendered = str(nested_value)
                            if artifact_backed and len(rendered) > 120:
                                rendered = rendered[:120] + "..."
                            kvs.append(f"{nested_key}: {rendered}")
                        lines.append(f"  - {', '.join(kvs)}")
                    else:
                        rendered = json.dumps(item, ensure_ascii=False, separators=(",", ":"))
                        if artifact_backed and len(rendered) > 2000:
                            rendered = rendered[:2000] + "..."
                        lines.append(f"  - {rendered}")
            if artifact_backed and len(val) > 20:
                lines.append(f"  ... and {len(val) - 20} more")
        elif isinstance(val, dict):
            if not val:
                lines.append(f"{key}: {{}}")
            else:
                lines.append(f"{key}:")
                for nested_key, nested_value in val.items():
                    if isinstance(nested_value, (dict, list)):
                        rendered = json.dumps(
                            nested_value, ensure_ascii=False, separators=(",", ":")
                        )
                        if artifact_backed and len(rendered) > 2000:
                            rendered = rendered[:2000] + "..."
                        lines.append(f"  {nested_key}: {rendered}")
                    else:
                        lines.append(f"  {nested_key}: {nested_value}")
        else:
            lines.append(f"{key}: {val}")
    return "\n".join(lines)
