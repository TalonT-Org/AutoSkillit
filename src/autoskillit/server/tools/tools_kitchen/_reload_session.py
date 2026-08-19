"""reload_session tool and session-id resolution helpers."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from autoskillit.core import (
    atomic_write,
    get_logger,
    get_state_dir,
    is_marker_fresh,
    read_marker,
)
from autoskillit.server import mcp
from autoskillit.server._notify import track_response_size

# Late-binding for monkeypatch reach: tests patch
# "autoskillit.server.tools.tools_kitchen.find_latest_session_id" (the
# package facade), so it must be resolved via attribute access on the
# package at call time rather than imported by name into this submodule.
from autoskillit.server.tools import tools_kitchen as _tk_pkg
from autoskillit.server.tools._cancellation_shield import _cancellation_shield

logger = get_logger(__name__)


def _find_session_id_for_reload(cwd: Path) -> str | None:
    """Return the session_id to use for reload; kitchen marker preferred, mtime fallback."""
    state_dir = get_state_dir()
    if state_dir.is_dir():

        def _safe_mtime(p: Path) -> float:
            try:
                return p.stat().st_mtime
            except OSError:
                return 0.0

        candidates = sorted(state_dir.glob("*.json"), key=_safe_mtime, reverse=True)
        for p in candidates:
            marker = read_marker(p.stem)
            if marker is not None and is_marker_fresh(marker):
                return marker.session_id
    return _tk_pkg.find_latest_session_id(str(cwd))


def _write_reload_sentinel(cwd: Path, session_id: str) -> None:
    """Atomically write a reload sentinel file for session_id."""
    sentinel_path = cwd / ".autoskillit" / "temp" / "reload_sentinel" / f"{session_id}.json"
    payload = json.dumps({"session_id": session_id, "requested_at": datetime.now(UTC).isoformat()})
    atomic_write(sentinel_path, payload)


def _reload_session_handler() -> dict[str, str]:
    """Core logic for the reload_session tool — testable without FastMCP."""
    cwd = Path.cwd()
    session_id = _find_session_id_for_reload(cwd)
    if not session_id:
        raise ValueError(
            "Cannot determine session ID. Ensure open_kitchen was called, "
            "or that a Claude Code session JSONL exists for this project."
        )
    _write_reload_sentinel(cwd, session_id)
    return {
        "status": "reload_requested",
        "session_id": session_id,
        "next_action": (
            "Run /exit now. The parent autoskillit process will re-launch "
            "with --resume and full wrapper environment."
        ),
    }


@mcp.tool(
    tags={"autoskillit"}, annotations={"readOnlyHint": True}, meta={"anthropic/alwaysLoad": True}
)
@_cancellation_shield()
@track_response_size("reload_session")
async def reload_session() -> str:
    """Signal the parent autoskillit process to reload this session with the full
    wrapper environment intact and resume the conversation.

    After calling this tool, run /exit to allow the parent process to detect the
    reload request and re-launch claude with --resume <session_id>.

    Never raises.
    """
    try:
        return json.dumps(_reload_session_handler())
    except Exception as exc:
        logger.error("reload_session unhandled exception", exc_info=True)
        return json.dumps({"status": "error", "error": f"{type(exc).__name__}: {exc}"})
