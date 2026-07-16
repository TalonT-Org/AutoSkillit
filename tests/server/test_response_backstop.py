"""Tests for the universal MCP response output-budget backstop."""

from __future__ import annotations

import json

import pytest

from autoskillit.config import OutputBudgetConfig
from autoskillit.server._response_budget import (
    RESPONSE_BACKSTOP_EXEMPT_TOOLS,
    RESPONSE_SPILL_METADATA_KEY,
    enforce_response_budget,
)

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def _config() -> OutputBudgetConfig:
    return OutputBudgetConfig(
        inline_max_chars=180,
        head_chars=90,
        tail_chars=90,
        response_max_bytes=1400,
    )


def test_small_response_is_byte_identical(tmp_path):
    original = json.dumps({"success": True, "result": "small"})
    assert (
        enforce_response_budget(
            original,
            tool_name="run_skill",
            artifact_dir=tmp_path,
            config=_config(),
        )
        == original
    )
    assert list(tmp_path.iterdir()) == []


def test_oversized_json_preserves_routing_shape_and_full_artifact(tmp_path):
    original = json.dumps(
        {
            "success": False,
            "needs_retry": True,
            "session_id": "session-1",
            "result": "middle-sentinel" + ("x" * 10_000),
        }
    )
    shaped = enforce_response_budget(
        original,
        tool_name="run_skill",
        artifact_dir=tmp_path,
        config=_config(),
    )

    assert isinstance(shaped, str)
    assert len(shaped.encode()) <= _config().response_max_bytes
    data = json.loads(shaped)
    assert data["success"] is False
    assert data["needs_retry"] is True
    assert data["session_id"] == "session-1"
    metadata = data[RESPONSE_SPILL_METADATA_KEY]
    assert open(metadata["artifact_path"], encoding="utf-8").read() == original
    assert metadata["original_utf8_bytes"] == len(original.encode())


def test_plain_text_response_uses_same_type_envelope(tmp_path):
    original = "head" + ("x" * 10_000) + "tail"
    shaped = enforce_response_budget(
        original,
        tool_name="plain_tool",
        artifact_dir=tmp_path,
        config=_config(),
    )

    assert isinstance(shaped, str)
    data = json.loads(shaped)
    artifact_path = data[RESPONSE_SPILL_METADATA_KEY]["artifact_path"]
    assert open(artifact_path, encoding="utf-8").read() == original
    assert len(shaped.encode()) <= _config().response_max_bytes


def test_artifact_failure_is_fail_closed(tmp_path, monkeypatch):
    from autoskillit.server import _response_budget

    monkeypatch.setattr(
        _response_budget,
        "atomic_write",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("ENOSPC")),
    )
    secret = "never-inline-when-spill-fails" * 1000
    shaped = enforce_response_budget(
        secret,
        tool_name="run_skill",
        artifact_dir=tmp_path,
        config=_config(),
    )
    assert secret not in shaped
    assert "artifact_write_failed" in shaped


def test_exemption_registry_is_closed():
    assert RESPONSE_BACKSTOP_EXEMPT_TOOLS == frozenset({"open_kitchen", "load_recipe"})
