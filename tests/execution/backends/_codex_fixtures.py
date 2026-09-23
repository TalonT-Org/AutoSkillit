"""Shared loaders and model catalogs for Codex backend tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_APP_SERVER_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "codex_ndjson"


def app_server_fixture(name: str) -> dict[str, Any]:
    return json.loads((_APP_SERVER_FIXTURE_DIR / name).read_text())


def installed_catalog() -> dict[str, object]:
    return {
        "models": [
            {
                "slug": "gpt-5.6-sol",
                "tool_mode": "code_mode",
                "apply_patch_tool_type": "freeform",
                "sentinel": {"preserved": True},
            },
            {
                "slug": "gpt-5.6-luna",
                "tool_mode": "code_mode_only",
                "apply_patch_tool_type": "freeform",
                "supported_reasoning_levels": [
                    {"effort": "high", "description": "High"},
                    {"effort": "xhigh", "description": "Extra high"},
                ],
                "default_reasoning_level": "xhigh",
                "reader_metadata": {"preserved": True},
            },
        ],
        "metadata": {"catalog": "installed", "schema_version": 7},
    }


def managed_selection_catalog() -> dict[str, object]:
    """Catalog with a valid Sol selection, separate from the unsuitable fixture."""
    catalog = installed_catalog()
    models = catalog["models"]
    assert isinstance(models, list)
    models[0] = {
        "slug": "gpt-5.6-sol",
        "tool_mode": "code_mode_only",
        "apply_patch_tool_type": "freeform",
        "supported_reasoning_levels": [
            {"effort": "medium", "description": "Medium"},
            {"effort": "high", "description": "High"},
            {"effort": "ultra", "description": "Ultra"},
        ],
        "default_reasoning_level": "ultra",
        "sentinel": {"valid_selection": True},
    }
    return catalog
