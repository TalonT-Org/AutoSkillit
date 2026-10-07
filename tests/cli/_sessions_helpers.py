from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from autoskillit.core import TOKEN_USAGE_SCHEMA_VERSION, TURN_USAGE_SCHEMA_VERSION


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


def _seed_turn_ledger(log_root: Path) -> None:
    session_dir = log_root / "sessions" / "session-1"
    session_dir.mkdir(parents=True, exist_ok=True)
    descriptor = {
        "schema_version": TOKEN_USAGE_SCHEMA_VERSION,
        "turn_usage_file": "turn_usage.jsonl",
        "turn_usage_count": 1,
        "turn_usage_schema_version": TURN_USAGE_SCHEMA_VERSION,
    }
    turn = {
        "backend": "claude-code",
        "provider_used": "anthropic",
        "message_id": "message-1",
        "request_id": "request-1",
        "timestamp": "2020-01-01T00:00:00Z",
        "model": "claude-opus-4-1-20250805",
        "input_tokens": {"state": "measured", "value": 75},
        "output_tokens": {"state": "measured_zero", "value": 0},
        "cache_read_tokens": {"state": "measured", "value": 50},
        "cache_write_tokens": {"state": "unavailable", "value": None},
        "peak_context": {"state": "unknown", "value": None},
        "context_window_tokens": 100,
        "context_fraction": 0.5,
    }
    (session_dir / "token_usage.json").write_text(json.dumps(descriptor), encoding="utf-8")
    (session_dir / "turn_usage.jsonl").write_text(json.dumps(turn) + "\n", encoding="utf-8")
