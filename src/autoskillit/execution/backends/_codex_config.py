"""TOML serialization and MCP registration for the Codex backend."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from autoskillit.core import (
    HEADLESS_AUTO_GATE_ENV_VAR,
    HEADLESS_ENV_VAR,
    atomic_write,
    get_logger,
)

logger = get_logger()


def _format_toml_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        escaped = (
            v.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\n", "\\n")
            .replace("\r", "\\r")
            .replace("\t", "\\t")
        )
        return f'"{escaped}"'
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return str(v)
    if isinstance(v, list):
        items = ", ".join(_format_toml_value(item) for item in v)
        return f"[{items}]"
    msg = f"Unsupported TOML value type: {type(v).__name__}"
    raise TypeError(msg)


def _format_inline_table(d: dict[str, Any]) -> str:
    pairs = [f"{k} = {_format_toml_value(v)}" for k, v in d.items()]
    return "{" + ", ".join(pairs) + "}"


def _classify_list(key: str, lst: list) -> bool:
    """Return True if list is all-dicts (array-of-tables), False if all-scalars.

    Raises TypeError for mixed lists containing both dict and non-dict items.
    """
    if not lst:
        return False
    has_dicts = any(isinstance(item, dict) for item in lst)
    has_non_dicts = any(not isinstance(item, dict) for item in lst)
    if has_dicts and has_non_dicts:
        msg = f"TOML array {key!r} contains both dict and non-dict items"
        raise TypeError(msg)
    return has_dicts


def _emit_aot_entry(d: dict[str, Any], path: list[str], lines: list[str]) -> None:
    """Emit a single [[path]] array-of-tables entry."""
    lines.append(f"\n[[{'.'.join(path)}]]")
    nested_aot: list[tuple[str, list[dict]]] = []
    for k, v in d.items():
        if isinstance(v, dict):
            lines.append(f"{k} = {_format_inline_table(v)}")
        elif isinstance(v, list) and _classify_list(k, v):
            nested_aot.append((k, v))
        else:
            lines.append(f"{k} = {_format_toml_value(v)}")
    for k, entries in nested_aot:
        for entry in entries:
            _emit_aot_entry(entry, [*path, k], lines)


def _emit_toml_table(d: dict[str, Any], path: list[str], lines: list[str]) -> None:
    header = f"[{'.'.join(path)}]"

    scalars: list[tuple[str, Any]] = []
    tables: list[tuple[str, dict]] = []
    aot_keys: list[tuple[str, list[dict]]] = []
    for k, v in d.items():
        if isinstance(v, dict):
            tables.append((k, v))
        elif isinstance(v, list) and _classify_list(k, v):
            aot_keys.append((k, v))
        else:
            scalars.append((k, v))

    has_scalars = bool(scalars)

    inline_tables: list[tuple[str, dict[str, Any]]] = []
    recurse_tables: list[tuple[str, dict[str, Any]]] = []
    for k, v in tables:
        if not v:
            inline_tables.append((k, v))
            continue
        is_leaf = not any(isinstance(sv, dict) for sv in v.values())
        if is_leaf and has_scalars:
            inline_tables.append((k, v))
        else:
            recurse_tables.append((k, v))

    if scalars or inline_tables:
        lines.append(f"\n{header}")
        for k, v in scalars:
            lines.append(f"{k} = {_format_toml_value(v)}")
        for k, v in inline_tables:
            lines.append(f"{k} = {_format_inline_table(v)}")

    for k, v in recurse_tables:
        _emit_toml_table(v, [*path, k], lines)

    for k, entries in aot_keys:
        for entry in entries:
            _emit_aot_entry(entry, [*path, k], lines)


def _serialize_toml(data: dict[str, Any]) -> str:
    lines: list[str] = []
    for k, v in data.items():
        if isinstance(v, dict):
            continue
        if isinstance(v, list) and _classify_list(k, v):
            continue
        lines.append(f"{k} = {_format_toml_value(v)}")
    for k, v in data.items():
        if isinstance(v, dict):
            _emit_toml_table(v, [k], lines)
    for k, v in data.items():
        if isinstance(v, list) and _classify_list(k, v):
            for entry in v:
                _emit_aot_entry(entry, [k], lines)
    text = "\n".join(lines).lstrip("\n")
    return text + "\n" if text else ""


def _read_codex_config(path: Path) -> dict[str, Any]:
    import tomllib

    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except FileNotFoundError:
        return {}
    except tomllib.TOMLDecodeError:
        logger.warning("corrupt_codex_config", path=str(path))
        return {}
    return data


def _write_codex_config(path: Path, data: dict[str, Any]) -> None:
    atomic_write(path, _serialize_toml(data))


def _is_autoskillit_registered(config: dict[str, Any], *, headless_auto_gate: bool) -> bool:
    entry = config.get("mcp_servers", {}).get("autoskillit")
    if not isinstance(entry, dict):
        return False
    if entry.get("command") != "autoskillit":
        return False
    env = entry.get("env", {})
    if env.get(HEADLESS_ENV_VAR) != "1":
        return False
    if headless_auto_gate and env.get(HEADLESS_AUTO_GATE_ENV_VAR) != "1":
        return False
    return True


def ensure_codex_mcp_registered(
    *,
    config_path: Path | None = None,
    headless_auto_gate: bool = True,
) -> bool:
    """Return True if the entry was written, False if already registered."""
    if config_path is None:
        config_path = Path.home() / ".codex" / "config.toml"
    config = _read_codex_config(config_path)
    if _is_autoskillit_registered(config, headless_auto_gate=headless_auto_gate):
        return False
    env: dict[str, str] = {HEADLESS_ENV_VAR: "1"}
    if headless_auto_gate:
        env[HEADLESS_AUTO_GATE_ENV_VAR] = "1"
    entry: dict[str, Any] = {
        "command": "autoskillit",
        "env": env,
        "startup_timeout_sec": 30.0,
        "tool_timeout_sec": 120.0,
    }
    config.setdefault("mcp_servers", {})["autoskillit"] = entry
    _write_codex_config(config_path, config)
    return True
