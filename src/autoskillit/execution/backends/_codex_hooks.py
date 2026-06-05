"""Codex config.toml hook generation and synchronization.

Canonical implementation at IL-1 — importable by both CLI (IL-3) and
execution/backends (IL-1) without layer violations.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from autoskillit.core import atomic_write
from autoskillit.execution.backends._codex_config import (
    _read_codex_config,
    _serialize_toml,
    _write_codex_config,
)
from autoskillit.hook_registry import (
    HOOKS_DIR,
    _build_hook_entry,
)
from autoskillit.hooks import HOOK_REGISTRY

_SKIP_CODEX_STATUSES = frozenset({"fix-required", "not-applicable"})


def _build_codex_hook_command(hooks_dir: Path, script: str, timeout_seconds: int | None) -> dict:
    """Build a single Codex hook command dict with trusted_hash."""
    logical_name = script.removesuffix(".py")
    dispatch_path = hooks_dir / "_dispatch.py"
    script_hash = hashlib.sha256(dispatch_path.read_bytes()).hexdigest()
    cmd: dict = {
        "type": "command",
        "command": f"python3 {dispatch_path} {logical_name}",
        "trusted_hash": script_hash,
    }
    if timeout_seconds is not None:
        cmd["timeout"] = timeout_seconds
    return cmd


def generate_codex_hooks_config(hook_config_format: str = "") -> dict[str, list[dict]]:
    """Generate Codex config.toml hooks entries from HOOK_REGISTRY.

    Skips interactive_only and codex fix-required/not-applicable hooks.
    Returns dict keyed by event type for [[hooks.<EventType>]] TOML format.
    """
    groups: dict[str, dict[tuple[str, str], dict]] = {}
    for hook_def in HOOK_REGISTRY:
        if hook_def.session_scope == "interactive_only":
            continue
        if hook_def.codex_status in _SKIP_CODEX_STATUSES:
            continue
        event = hook_def.event_type
        key = (event, hook_def.matcher)
        hook_commands = [
            _build_codex_hook_command(HOOKS_DIR, script, hook_def.timeout_seconds)
            for script in hook_def.scripts
        ]
        if event not in groups:
            groups[event] = {}
        if key not in groups[event]:
            entry = _build_hook_entry(hook_def, hook_commands)
            groups[event][key] = entry
        else:
            groups[event][key]["hooks"].extend(hook_commands)
    return {event: list(entries.values()) for event, entries in groups.items()}


def _is_autoskillit_hook_entry(entry: dict) -> bool:
    """Check if a Codex hooks config entry belongs to autoskillit."""
    hooks_dir_str = str(HOOKS_DIR)
    for hook in entry.get("hooks", []):
        cmd = hook.get("command", "")
        if "/autoskillit/" in cmd or hooks_dir_str in cmd or "_dispatch.py" in cmd:
            return True
    return False


def _upsert_hooks_text(
    config_path: Path, raw_bytes: bytes, fresh_hooks: dict[str, list[dict]]
) -> None:
    """Replace autoskillit-owned hook blocks in raw config text and append fresh ones."""
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeError(f"config file contains non-UTF-8 bytes: {exc}") from exc
    lines = text.splitlines(keepends=True)

    owned_ranges: list[tuple[int, int]] = []
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped == "[[hooks]]" or (
            stripped.startswith("[[hooks.") and stripped.endswith("]]")
        ):
            block_start = i
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("["):
                i += 1
            block_end = i
            block_text = "".join(lines[block_start:block_end])
            if "/autoskillit/" in block_text or "_dispatch.py" in block_text:
                owned_ranges.append((block_start, block_end))
        else:
            i += 1

    for start, end in reversed(owned_ranges):
        del lines[start:end]

    fresh_text = _serialize_toml({"hooks": fresh_hooks})
    result_text = "".join(lines).rstrip("\n") + "\n\n" + fresh_text
    atomic_write(config_path, result_text)


def sync_hooks_to_codex_config(
    config_path: Path | None = None, *, hook_config_format: str = ""
) -> bool:
    """Sync autoskillit hooks to Codex config.toml.

    Returns True if the config was changed, False if already up to date.
    """
    if config_path is None:
        config_path = Path.home() / ".codex" / "config.toml"
    result = _read_codex_config(config_path)
    if result.is_corrupt:
        if result.raw_bytes is None:
            raise RuntimeError("corrupt ReadResult has no raw_bytes")
        fresh = generate_codex_hooks_config(hook_config_format=hook_config_format)
        _upsert_hooks_text(config_path, result.raw_bytes, fresh)
        return True

    config = result.data
    existing_hooks = config.get("hooks", {})
    if isinstance(existing_hooks, list):
        existing_hooks = {}
    foreign_hooks: dict[str, list[dict]] = {}
    for event_type, entries in existing_hooks.items():
        if not isinstance(entries, list):
            continue
        foreign = [e for e in entries if not _is_autoskillit_hook_entry(e)]
        if foreign:
            foreign_hooks[event_type] = foreign
    fresh = generate_codex_hooks_config(hook_config_format=hook_config_format)
    merged: dict[str, list[dict]] = {}
    for event_type in set(list(foreign_hooks.keys()) + list(fresh.keys())):
        merged[event_type] = foreign_hooks.get(event_type, []) + fresh.get(event_type, [])
    if merged == existing_hooks:
        return False
    config["hooks"] = merged
    _write_codex_config(config_path, config, source=result)
    return True
