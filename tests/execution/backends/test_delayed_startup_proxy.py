"""Deterministic measurement tests for the installed-Claude startup proxy."""

from __future__ import annotations

import json

import pytest

from tests.execution.backends._delayed_startup_proxy import response_measurements

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def test_response_measurements_keep_units_and_cost_separate() -> None:
    measurements = response_measurements(
        [json.dumps({"success": True, "result": "界", "cost_usd": 0.25}, ensure_ascii=False)]
    )

    assert measurements["raw_chars"] != measurements["utf8_bytes"]
    assert measurements["client_serialized_chars"] > measurements["raw_chars"]
    assert measurements["estimated_tokens"] == measurements["utf8_bytes"] // 4
    assert measurements["cost_usd"] == 0.25
