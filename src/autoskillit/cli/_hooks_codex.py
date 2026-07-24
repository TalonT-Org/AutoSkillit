"""Codex config.toml hook generation and synchronization (CLI re-export).

Canonical implementation lives in execution/backends/_codex_hooks.py (IL-1).
This shim preserves the import path for CLI callers and tests.
"""

from autoskillit.execution import (
    _is_autoskillit_hook_entry,
    generate_codex_hooks_config,
    sync_hooks_to_codex_config,
)

__all__ = [
    "_is_autoskillit_hook_entry",
    "generate_codex_hooks_config",
    "sync_hooks_to_codex_config",
]
