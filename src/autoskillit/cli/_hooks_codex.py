"""Codex config.toml hook generation and synchronization."""

from __future__ import annotations

from pathlib import Path

from autoskillit.core import atomic_write
from autoskillit.execution import (
    _read_codex_config,
    _serialize_toml,
    _write_codex_config,
)
from autoskillit.hook_registry import (
    HOOKS_DIR,
    _build_hook_command,
    _build_hook_entry,
)
from autoskillit.hooks import HOOK_REGISTRY


def generate_codex_hooks_config() -> list[dict]:
    """Generate Codex config.toml hooks entries from HOOK_REGISTRY.

    Skips interactive_only hooks. Consolidates entries sharing the same
    (event_type, matcher) key.
    """
    groups: dict[tuple[str, str], dict] = {}
    for hook_def in HOOK_REGISTRY:
        if hook_def.session_scope == "interactive_only":
            continue
        key = (hook_def.event_type, hook_def.matcher)
        hook_commands = [
            _build_hook_command(HOOKS_DIR, script, hook_def.timeout_seconds)
            for script in hook_def.scripts
        ]
        if key not in groups:
            entry = _build_hook_entry(hook_def, hook_commands)
            entry["event"] = hook_def.event_type
            groups[key] = entry
        else:
            groups[key]["hooks"].extend(hook_commands)
    return list(groups.values())


def _is_autoskillit_hook_entry(entry: dict) -> bool:
    """Check if a Codex hooks config entry belongs to autoskillit."""
    hooks_dir_str = str(HOOKS_DIR)
    for hook in entry.get("hooks", []):
        cmd = hook.get("command", "")
        if "/autoskillit/" in cmd or hooks_dir_str in cmd or "_dispatch.py" in cmd:
            return True
    return False


def _upsert_hooks_text(config_path: Path, raw_bytes: bytes, fresh_hooks: list[dict]) -> None:
    """Replace autoskillit-owned [[hooks]] blocks in raw config text and append fresh ones."""
    text = raw_bytes.decode("utf-8", errors="replace")
    lines = text.splitlines(keepends=True)

    owned_ranges: list[tuple[int, int]] = []
    i = 0
    while i < len(lines):
        if lines[i].strip() == "[[hooks]]":
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


def sync_hooks_to_codex_config(config_path: Path | None = None) -> bool:
    """Sync autoskillit hooks to Codex config.toml.

    Returns True if the config was changed, False if already up to date.
    """
    if config_path is None:
        config_path = Path.home() / ".codex" / "config.toml"
    result = _read_codex_config(config_path)
    if result.is_corrupt:
        assert result.raw_bytes is not None
        fresh = generate_codex_hooks_config()
        _upsert_hooks_text(config_path, result.raw_bytes, fresh)
        return True
    else:
        config = result.data
        existing_hooks = config.get("hooks", [])
        foreign_hooks = [e for e in existing_hooks if not _is_autoskillit_hook_entry(e)]
        fresh = generate_codex_hooks_config()
        new_hooks = foreign_hooks + fresh
        if new_hooks == existing_hooks:
            return False
        config["hooks"] = new_hooks
        _write_codex_config(config_path, config, source=result)
        return True
