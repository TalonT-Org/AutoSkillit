"""Shared loaders for Codex backend fixture payloads."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_APP_SERVER_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "codex_ndjson"


def app_server_fixture(name: str) -> dict[str, Any]:
    return json.loads((_APP_SERVER_FIXTURE_DIR / name).read_text())
