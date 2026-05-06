"""Tests for pipeline.tokens — model capture, compute_model_totals, and load_from_log_dir model."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoskillit.pipeline.tokens import DefaultTokenLog, TokenEntry

pytestmark = [pytest.mark.layer("pipeline"), pytest.mark.small]


def _make_usage_with_model(
    model: str, *, input_tokens: int = 100, output_tokens: int = 50
) -> dict:
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "model_breakdown": {model: {"input_tokens": input_tokens, "output_tokens": output_tokens}},
    }


def test_token_entry_has_model_field() -> None:
    entry = TokenEntry(step_name="plan", model="claude-sonnet-4-6")
    assert entry.model == "claude-sonnet-4-6"
    d = entry.to_dict()
    assert d["model"] == "claude-sonnet-4-6"


def test_token_entry_model_defaults_empty() -> None:
    entry = TokenEntry(step_name="plan")
    assert entry.model == ""


def test_record_captures_model_from_breakdown() -> None:
    log = DefaultTokenLog()
    log.record("plan", _make_usage_with_model("claude-sonnet-4-6"))
    report = log.get_report()
    assert report[0]["model"] == "claude-sonnet-4-6"


def test_record_no_breakdown_model_empty() -> None:
    log = DefaultTokenLog()
    log.record("plan", {"input_tokens": 100, "output_tokens": 50})
    report = log.get_report()
    assert report[0]["model"] == ""


def test_record_model_set_on_first_invocation_only() -> None:
    log = DefaultTokenLog()
    log.record("plan", _make_usage_with_model("claude-sonnet-4-6"))
    log.record("plan", _make_usage_with_model("MiniMax-M2.7"))
    report = log.get_report()
    assert report[0]["model"] == "claude-sonnet-4-6"


def test_compute_model_totals_single_model() -> None:
    log = DefaultTokenLog()
    log.record("plan", _make_usage_with_model("claude-sonnet-4-6", input_tokens=100))
    log.record("verify", _make_usage_with_model("claude-sonnet-4-6", input_tokens=200))
    totals = log.compute_model_totals()
    assert len(totals) == 1
    assert totals[0]["model"] == "claude-sonnet-4-6"
    assert totals[0]["step_count"] == 2
    assert totals[0]["input_tokens"] == 300


def test_compute_model_totals_mixed_models() -> None:
    log = DefaultTokenLog()
    log.record("plan", _make_usage_with_model("claude-sonnet-4-6", input_tokens=100))
    log.record(
        "implement",
        _make_usage_with_model("MiniMax-M2.7", input_tokens=500, output_tokens=200),
    )
    totals = log.compute_model_totals()
    assert len(totals) == 2
    by_model = {t["model"]: t for t in totals}
    assert by_model["claude-sonnet-4-6"]["step_count"] == 1
    assert by_model["MiniMax-M2.7"]["input_tokens"] == 500


def test_compute_model_totals_no_model_returns_unknown() -> None:
    log = DefaultTokenLog()
    log.record("plan", {"input_tokens": 100, "output_tokens": 50})
    totals = log.compute_model_totals()
    assert len(totals) == 1
    assert totals[0]["model"] == "unknown"


def test_compute_model_totals_empty_log() -> None:
    log = DefaultTokenLog()
    totals = log.compute_model_totals()
    assert totals == []


@pytest.mark.parametrize("use_session_label", [True, False])
def test_load_from_log_dir_reads_model_identifier(tmp_path: Path, use_session_label: bool) -> None:
    sessions_dir = tmp_path / "sessions" / "s1"
    sessions_dir.mkdir(parents=True)
    step_key = "session_label" if use_session_label else "step_name"
    (sessions_dir / "token_usage.json").write_text(
        json.dumps(
            {
                step_key: "plan",
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "timing_seconds": 10.0,
                "model_identifier": "claude-sonnet-4-6",
            }
        )
    )
    index_entry = {"session_id": "s1", "dir_name": "s1", "timestamp": "2026-01-01T00:00:00Z"}
    (tmp_path / "sessions.jsonl").write_text(json.dumps(index_entry) + "\n")

    log = DefaultTokenLog()
    log.load_from_log_dir(tmp_path)
    report = log.get_report()
    assert len(report) == 1
    assert report[0]["model"] == "claude-sonnet-4-6"


def test_load_from_log_dir_no_model_identifier_defaults_empty(tmp_path: Path) -> None:
    sessions_dir = tmp_path / "sessions" / "s1"
    sessions_dir.mkdir(parents=True)
    (sessions_dir / "token_usage.json").write_text(
        json.dumps(
            {
                "session_label": "plan",
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "timing_seconds": 10.0,
            }
        )
    )
    index_entry = {"session_id": "s1", "dir_name": "s1", "timestamp": "2026-01-01T00:00:00Z"}
    (tmp_path / "sessions.jsonl").write_text(json.dumps(index_entry) + "\n")

    log = DefaultTokenLog()
    log.load_from_log_dir(tmp_path)
    report = log.get_report()
    assert report[0]["model"] == ""


def test_model_totals_single_model_one_row() -> None:
    log = DefaultTokenLog()
    for step in ["plan", "implement", "verify"]:
        log.record(step, _make_usage_with_model("claude-sonnet-4-6"))
    totals = log.compute_model_totals()
    assert len(totals) == 1


def test_model_totals_mixed_providers() -> None:
    log = DefaultTokenLog()
    log.record("plan", _make_usage_with_model("claude-sonnet-4-6"))
    log.record("implement", _make_usage_with_model("MiniMax-M2.7-highspeed", input_tokens=500))
    totals = log.compute_model_totals()
    assert len(totals) == 2
