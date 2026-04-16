"""Hook scripts (PreToolUse and PostToolUse) for AutoSkillit."""

from autoskillit.hook_registry import (
    HOOK_REGISTRY,
    HookDef,
    generate_hooks_json,
)
from autoskillit.hooks._fmt_primitives import _HOOK_CONFIG_PATH_COMPONENTS
from autoskillit.hooks.quota_guard import QUOTA_GUARD_DENY_TRIGGER
from autoskillit.hooks.quota_post_hook import QUOTA_POST_WARNING_TRIGGER

__all__ = [
    "HOOK_REGISTRY",
    "HookDef",
    "QUOTA_GUARD_DENY_TRIGGER",
    "QUOTA_POST_WARNING_TRIGGER",
    "_HOOK_CONFIG_PATH_COMPONENTS",
    "generate_hooks_json",
]
