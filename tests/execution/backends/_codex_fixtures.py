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
                "slug": "gpt-6-sol",
                "tool_mode": "code_mode_only",
                "apply_patch_tool_type": "freeform",
                "supported_reasoning_levels": [
                    {"effort": "low", "description": "Low"},
                    {"effort": "medium", "description": "Medium"},
                    {"effort": "high", "description": "High"},
                    {"effort": "xhigh", "description": "Extra high"},
                    {"effort": "max", "description": "Maximum"},
                    {"effort": "ultra", "description": "Ultra"},
                ],
                "default_reasoning_level": "medium",
                "sentinel": {"preserved": True},
            },
            {
                "slug": "gpt-6-luna",
                "tool_mode": "code_mode_only",
                "apply_patch_tool_type": "freeform",
                "supported_reasoning_levels": [
                    {"effort": "low", "description": "Low"},
                    {"effort": "medium", "description": "Medium"},
                    {"effort": "high", "description": "High"},
                    {"effort": "xhigh", "description": "Extra high"},
                    {"effort": "max", "description": "Maximum"},
                ],
                "default_reasoning_level": "medium",
                "reader_metadata": {"preserved": True},
            },
        ],
        "metadata": {"catalog": "installed", "schema_version": 7},
    }
