"""Shared factory for server-authoritative ingredient override injection."""

from __future__ import annotations

from autoskillit.config import SERVER_AUTHORITATIVE_INGREDIENTS


def _build_auto_overrides(
    defaults: dict[str, str],
    kitchen_id: str,
    log_dir: str,
) -> dict[str, str]:
    overrides: dict[str, str] = {
        "kitchen_id": kitchen_id,
        "diagnostics_log_dir": log_dir,
    }
    for key in SERVER_AUTHORITATIVE_INGREDIENTS:
        if (v := defaults.get(key)) is not None:
            overrides[key] = v
    return overrides
