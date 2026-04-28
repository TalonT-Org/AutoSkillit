"""Tests for the reload_session MCP tool and supporting helpers."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


# ---------------------------------------------------------------------------
# RS-1 — reload_session is in FREE_RANGE_TOOLS
# ---------------------------------------------------------------------------


def test_reload_session_in_free_range_tools() -> None:
    from autoskillit.core._type_constants import FREE_RANGE_TOOLS

    assert "reload_session" in FREE_RANGE_TOOLS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_kitchen_marker(project_dir: Path, session_id: str) -> None:
    """Write a kitchen marker under AUTOSKILLIT_STATE_DIR = project_dir."""
    state_dir = project_dir / "kitchen_state"
    state_dir.mkdir(parents=True, exist_ok=True)
    marker_path = state_dir / f"{session_id}.json"
    from datetime import UTC, datetime

    payload = {
        "session_id": session_id,
        "opened_at": datetime.now(UTC).isoformat(),
        "recipe_name": None,
        "marker_version": 1,
        "content_hash": "",
        "composite_hash": "",
    }
    marker_path.write_text(json.dumps(payload), encoding="utf-8")


# ---------------------------------------------------------------------------
# RS-2 — Tool writes sentinel with session_id from kitchen marker
# ---------------------------------------------------------------------------


def test_reload_session_writes_sentinel_from_kitchen_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AUTOSKILLIT_STATE_DIR", str(tmp_path))
    _write_kitchen_marker(tmp_path, "abc123")

    from autoskillit.server.tools_kitchen import _reload_session_handler

    result = _reload_session_handler()

    sentinel_path = tmp_path / ".autoskillit" / "temp" / "reload_sentinel" / "abc123.json"
    assert sentinel_path.exists(), f"Sentinel not found at {sentinel_path}"
    data = json.loads(sentinel_path.read_text(encoding="utf-8"))
    assert data["session_id"] == "abc123"
    assert result["session_id"] == "abc123"


# ---------------------------------------------------------------------------
# RS-3 — Tool falls back to find_latest_session_id when no kitchen marker
# ---------------------------------------------------------------------------


def test_reload_session_falls_back_to_find_latest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    # No kitchen marker — state dir is empty
    monkeypatch.setenv("AUTOSKILLIT_STATE_DIR", str(tmp_path / "empty"))

    with patch(
        "autoskillit.server.tools_kitchen.find_latest_session_id", return_value="fallback-id"
    ):
        from autoskillit.server.tools_kitchen import _reload_session_handler as reload_session

        result = reload_session()

    sentinel_path = tmp_path / ".autoskillit" / "temp" / "reload_sentinel" / "fallback-id.json"
    assert sentinel_path.exists()
    data = json.loads(sentinel_path.read_text(encoding="utf-8"))
    assert data["session_id"] == "fallback-id"
    assert result["session_id"] == "fallback-id"


# ---------------------------------------------------------------------------
# RS-4 — Tool returns action instruction telling Claude to run /exit
# ---------------------------------------------------------------------------


def test_reload_session_returns_exit_instruction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AUTOSKILLIT_STATE_DIR", str(tmp_path / "empty"))

    with patch(
        "autoskillit.server.tools_kitchen.find_latest_session_id", return_value="sess-exit"
    ):
        from autoskillit.server.tools_kitchen import _reload_session_handler as reload_session

        result = reload_session()

    assert "/exit" in result["next_action"]
    assert result["status"] == "reload_requested"


# ---------------------------------------------------------------------------
# RS-5 — Tool raises informative error when session_id cannot be determined
# ---------------------------------------------------------------------------


def test_reload_session_raises_when_no_session_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AUTOSKILLIT_STATE_DIR", str(tmp_path / "empty"))

    with patch("autoskillit.server.tools_kitchen.find_latest_session_id", return_value=None):
        from autoskillit.server.tools_kitchen import _reload_session_handler as reload_session

        with pytest.raises(ValueError, match="session ID"):
            reload_session()


# ---------------------------------------------------------------------------
# RS-6 — The async wrapper serializes to str (not raw dict)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_reload_session_tool_wrapper_returns_str(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AUTOSKILLIT_STATE_DIR", str(tmp_path / "empty"))

    with patch(
        "autoskillit.server.tools_kitchen.find_latest_session_id", return_value="sess-wrap"
    ):
        from autoskillit.server.tools_kitchen import reload_session

        result = await reload_session()

    assert isinstance(result, str), f"Expected str, got {type(result).__name__}"
    parsed = json.loads(result)
    assert parsed["status"] == "reload_requested"
    assert parsed["session_id"] == "sess-wrap"
