"""Hook scripts (PreToolUse and PostToolUse) for AutoSkillit."""

from autoskillit.hook_registry import (
    HOOK_REGISTRY,
    HookDef,
    _build_hook_entry,
    generate_hooks_json,
)

__all__ = ["HOOK_REGISTRY", "HookDef", "_build_hook_entry", "generate_hooks_json"]
