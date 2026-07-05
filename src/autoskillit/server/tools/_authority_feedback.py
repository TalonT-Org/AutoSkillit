"""Shared authority-violation feedback helpers for open_kitchen, load_recipe, and lock_ingredients.

Single source of truth for authority feedback text — all three tool surfaces
route through this module to guarantee consistency across warning and rejection paths.
"""

from __future__ import annotations

from typing import Any

from autoskillit.config import (
    SERVER_AUTHORITATIVE_CONFIG_PATHS,
    SERVER_AUTHORITATIVE_INGREDIENTS,
    SERVER_AUTHORITATIVE_KEY_HINTS,
)


def build_authority_clobber_warnings(
    overrides: dict[str, str],
    config_layer: dict[str, str],
    *,
    caller_tool: str = "open_kitchen",
) -> list[str]:
    """Return warnings for overrides clobbered by server-authoritative layer."""
    warnings: list[str] = []
    for key in sorted(set(overrides.keys()) & SERVER_AUTHORITATIVE_INGREDIENTS):
        config_path = SERVER_AUTHORITATIVE_CONFIG_PATHS.get(key)
        server_value = config_layer.get(key, "")
        if config_path:
            warnings.append(
                f"Override for server-authoritative ingredient '{key}' ignored — "
                f"server value '{server_value}' (from config {config_path}) wins; "
                f"set the config key and re-call {caller_tool} to change it"
            )
        else:
            warnings.append(
                f"Override for server-authoritative ingredient '{key}' ignored — "
                f"set by the dispatch runtime at session launch, not user-configurable"
            )
        hint = SERVER_AUTHORITATIVE_KEY_HINTS.get(key)
        if hint:
            warnings.append(hint)
    return warnings


def build_authority_rejection_envelope(rejected_keys: set[str]) -> dict[str, Any]:
    """Return a structured failure envelope for lock_ingredients server-authoritative rejection."""
    config_backed: list[str] = []
    runtime_derived: list[str] = []
    for key in sorted(rejected_keys):
        if key in SERVER_AUTHORITATIVE_CONFIG_PATHS:
            config_backed.append(f"{key} ({SERVER_AUTHORITATIVE_CONFIG_PATHS[key]})")
        else:
            runtime_derived.append(key)

    parts: list[str] = []
    if config_backed:
        parts.append(
            "Config-backed server-authoritative ingredients (set via config): "
            + ", ".join(config_backed)
        )
    if runtime_derived:
        parts.append(
            "Runtime-derived server-authoritative ingredients "
            "(set by dispatch runtime, not user-configurable): " + ", ".join(runtime_derived)
        )
    for key in sorted(rejected_keys):
        hint = SERVER_AUTHORITATIVE_KEY_HINTS.get(key)
        if hint:
            parts.append(hint)
    parts.append("This rejection does NOT affect the running pipeline.")

    return {
        "success": False,
        "error": (
            f"Cannot lock server-authoritative ingredients: {sorted(rejected_keys)}. "
            "These are set by the server and cannot be overridden."
        ),
        "stage": "ingredient_authority_validation",
        "retriable": False,
        "user_visible_message": ". ".join(parts),
    }
