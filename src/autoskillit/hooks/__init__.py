"""Hook scripts (PreToolUse and PostToolUse) for AutoSkillit."""

from autoskillit.hook_registry import (
    HOOK_REGISTRY,
    HookDef,
    generate_hooks_json,
)
from autoskillit.hooks.formatters._fmt_primitives import _HOOK_CONFIG_PATH_COMPONENTS
from autoskillit.hooks.guards.branch_protection_guard import BRANCH_PROTECTION_DENY_TRIGGER
from autoskillit.hooks.guards.leaf_orchestration_guard import LEAF_ORCHESTRATION_DENY_TRIGGER
from autoskillit.hooks.guards.quota_guard import QUOTA_GUARD_DENY_TRIGGER
from autoskillit.hooks.quota_post_hook import QUOTA_POST_WARNING_TRIGGER
from autoskillit.hooks.guards.review_loop_gate import REVIEW_LOOP_DENY_TRIGGER

__all__ = [
    "HOOK_REGISTRY",
    "HookDef",
    "BRANCH_PROTECTION_DENY_TRIGGER",
    "LEAF_ORCHESTRATION_DENY_TRIGGER",
    "QUOTA_GUARD_DENY_TRIGGER",
    "QUOTA_POST_WARNING_TRIGGER",
    "REVIEW_LOOP_DENY_TRIGGER",
    "_HOOK_CONFIG_PATH_COMPONENTS",
    "generate_hooks_json",
]
