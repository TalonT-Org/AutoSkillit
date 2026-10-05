"""Shared fixtures for the standalone observability-deck tests."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from py_mini_racer import MiniRacer

from autoskillit.core import pkg_root
from autoskillit.report.deck import build_deck_payload
from tests.report._fixtures import DECK_GENERATED_AT, session_row


@pytest.fixture
def deck_asset() -> Any:
    def read(rel: str) -> str:
        return (pkg_root() / "assets" / "deck" / rel).read_text(encoding="utf-8")

    return read


def _time_ms(value: datetime) -> int:
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    return (value - epoch) // timedelta(milliseconds=1)


@pytest.fixture
def deck_rows() -> list[dict[str, Any]]:
    return [
        session_row(
            "s1",
            time_ms=_time_ms(DECK_GENERATED_AT - timedelta(days=1)),
            harness="claude-code",
            provider="anthropic",
            skill="a",
        ),
        session_row(
            "s2",
            time_ms=_time_ms(DECK_GENERATED_AT - timedelta(days=2)),
            harness="claude-code",
            provider="anthropic",
            skill="a",
        ),
        session_row(
            "s3",
            time_ms=_time_ms(DECK_GENERATED_AT - timedelta(days=9)),
            harness="claude-code",
            provider="anthropic",
            skill="b",
        ),
        session_row(
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
