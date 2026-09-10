"""Backward-compat shim for feature_flags — see core.claude_env.feature_flags."""

from autoskillit.core.claude_env.feature_flags import (
    FEATURE_REGISTRY,
    _collect_disabled_feature_tags,
    is_feature_enabled,
)

__all__ = [
    "FEATURE_REGISTRY",
    "_collect_disabled_feature_tags",
    "is_feature_enabled",
]
