"""Hook scripts (PreToolUse and PostToolUse) for AutoSkillit."""

from autoskillit.hook_registry import (
    HOOK_REGISTRY,
    HookDef,
    generate_hooks_json,
)
from autoskillit.hooks._fmt_primitives import _HOOK_CONFIG_PATH_COMPONENTS

__all__ = ["HOOK_REGISTRY", "HookDef", "_HOOK_CONFIG_PATH_COMPONENTS", "generate_hooks_json"]
