"""Shared fixtures for the standalone observability-deck tests."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from py_mini_racer import MiniRacer

from autoskillit.core.io.paths import pkg_root
from autoskillit.report.deck._payload import build_deck_payload
from autoskillit.report.deck._registry import SESSION_COLUMNS

DECK_GENERATED_AT = datetime(2026, 10, 4, tzinfo=UTC)


@pytest.fixture
def deck_asset() -> Any:
    def read(rel: str) -> str:
        return (pkg_root() / "assets" / "deck" / rel).read_text(encoding="utf-8")

    return read


def _time_ms(value: datetime) -> int:
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    return (value - epoch) // timedelta(milliseconds=1)


def _session_row(
    key: str,
    *,
    time_ms: int | None,
    harness: str,
    provider: str,
    skill: str | None,
) -> dict[str, Any]:
    row: dict[str, Any] = dict.fromkeys(SESSION_COLUMNS)
    row.update(
        {
            "schema_version": 1,
            "kind": "session",
            "key": key,
            "session_id": key,
            "time_ms": time_ms,
            "harness": harness,
            "provider": provider,
            "skill": skill,
            "input_tokens": {"state": "unknown", "value": None},
            "output_tokens": {"state": "unknown", "value": None},
            "cache_write_tokens": {"state": "unknown", "value": None},
            "cache_read_tokens": {"state": "unknown", "value": None},
        }
    )
    if key == "s4":
        row["cache_write_tokens"] = {"state": "unavailable", "value": None}
    return row


@pytest.fixture
def deck_rows() -> list[dict[str, Any]]:
    return [
        _session_row(
            "s1",
            time_ms=_time_ms(DECK_GENERATED_AT - timedelta(days=1)),
            harness="claude-code",
            provider="anthropic",
            skill="a",
        ),
        _session_row(
            "s2",
            time_ms=_time_ms(DECK_GENERATED_AT - timedelta(days=2)),
            harness="claude-code",
            provider="anthropic",
            skill="a",
        ),
        _session_row(
            "s3",
            time_ms=_time_ms(DECK_GENERATED_AT - timedelta(days=9)),
            harness="claude-code",
            provider="anthropic",
            skill="b",
        ),
        _session_row(
            "s4",
            time_ms=None,
            harness="codex",
            provider="codex",
            skill=None,
        ),
    ]


@pytest.fixture
def deck_payload(deck_rows: list[dict[str, Any]]) -> dict[str, Any]:
    return build_deck_payload(deck_rows, generated_at=DECK_GENERATED_AT, index_schema_version=1)


@pytest.fixture
def deck_js(deck_asset: Any) -> Any:
    with MiniRacer() as ctx:
        ctx.eval(deck_asset("core.js"))
        yield ctx


@pytest.fixture
def deck_model(deck_js: Any, deck_payload: dict[str, Any]) -> dict[str, Any]:
    model = {**deck_payload, "tables": {**deck_payload["tables"]}}
    model["tables"]["sessions"] = deck_js.call(
        "DeckCore.decodeTable", deck_payload["tables"]["sessions"]
    )
    return model
