from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def _configure_log_root(monkeypatch: pytest.MonkeyPatch, log_root: Path) -> None:
    monkeypatch.setattr(
        "autoskillit.config.load_config",
        lambda: SimpleNamespace(linux_tracing=SimpleNamespace(log_dir=str(log_root))),
    )


def _seed_session(log_root: Path) -> None:
    log_root.mkdir(parents=True, exist_ok=True)
    session = {
        "dir_name": "session-1",
        "session_id": "sid-1",
        "backend": "claude-code",
        "provider_used": "anthropic",
        "timestamp": "2020-01-01T00:00:00Z",
    }
    (log_root / "sessions.jsonl").write_text(json.dumps(session) + "\n", encoding="utf-8")
