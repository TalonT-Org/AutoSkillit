"""Tests for autoskillit.pipeline.tokens — pipeline token usage tracking."""

from __future__ import annotations

import json
from dataclasses import fields

from autoskillit.pipeline.tokens import DefaultTokenLog, TokenEntry


def _make_usage(**overrides: int) -> dict[str, int]:
    defaults = {
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_creation_input_tokens": 10,
        "cache_read_input_tokens": 5,
    }
    return {**defaults, **overrides}


class TestTokenEntry:
    def test_fields_exist(self):
        entry = TokenEntry(step_name="plan")
        field_names = {f.name for f in fields(entry)}
        assert field_names == {
            "step_name",
            "input_tokens",
            "output_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
            "invocation_count",
        }

    def test_default_counts_are_zero(self):
        entry = TokenEntry(step_name="plan")
        assert entry.input_tokens == 0
        assert entry.output_tokens == 0
        assert entry.cache_creation_input_tokens == 0
        assert entry.cache_read_input_tokens == 0
        assert entry.invocation_count == 0

    def test_to_dict_is_json_serializable(self):
        entry = TokenEntry(step_name="plan")
        d = entry.to_dict()
        assert json.loads(json.dumps(d)) == d

    def test_to_dict_contains_all_fields(self):
        entry = TokenEntry(step_name="implement", input_tokens=42)
        d = entry.to_dict()
        assert set(d.keys()) == {
            "step_name",
            "input_tokens",
            "output_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
            "invocation_count",
        }
        assert d["step_name"] == "implement"
        assert d["input_tokens"] == 42


class TestDefaultTokenLog:
    def test_empty_on_init(self):
        log = DefaultTokenLog()
        assert log.get_report() == []

    def test_record_single_step(self):
        log = DefaultTokenLog()
        log.record("plan", _make_usage())
        report = log.get_report()
        assert len(report) == 1
        assert report[0]["step_name"] == "plan"
        assert report[0]["input_tokens"] == 100
        assert report[0]["output_tokens"] == 50
        assert report[0]["cache_creation_input_tokens"] == 10
        assert report[0]["cache_read_input_tokens"] == 5

    def test_record_same_step_twice_accumulates(self):
        log = DefaultTokenLog()
        log.record("implement", _make_usage(input_tokens=100, output_tokens=50))
        log.record("implement", _make_usage(input_tokens=200, output_tokens=80))
        report = log.get_report()
        assert len(report) == 1
        assert report[0]["input_tokens"] == 300
        assert report[0]["output_tokens"] == 130

    def test_invocation_count_increments_per_call(self):
        log = DefaultTokenLog()
        log.record("implement", _make_usage())
        log.record("implement", _make_usage())
        report = log.get_report()
        assert report[0]["invocation_count"] == 2

    def test_record_different_steps_produces_separate_entries(self):
        log = DefaultTokenLog()
        log.record("plan", _make_usage())
        log.record("implement", _make_usage())
        report = log.get_report()
        assert len(report) == 2
        assert report[0]["step_name"] == "plan"
        assert report[1]["step_name"] == "implement"

    def test_record_noop_on_empty_step_name(self):
        log = DefaultTokenLog()
        log.record("", _make_usage())
        assert log.get_report() == []

    def test_record_noop_on_none_token_usage(self):
        log = DefaultTokenLog()
        log.record("plan", None)
        assert log.get_report() == []

    def test_get_report_is_defensive_copy(self):
        log = DefaultTokenLog()
        log.record("plan", _make_usage())
        report = log.get_report()
        report.clear()
        assert len(log.get_report()) == 1

    def test_clear_resets_all_entries(self):
        log = DefaultTokenLog()
        log.record("plan", _make_usage())
        log.record("implement", _make_usage())
        log.record("verify", _make_usage())
        log.clear()
        assert log.get_report() == []

    def test_partial_token_fields_default_missing_to_zero(self):
        log = DefaultTokenLog()
        log.record("plan", {"input_tokens": 42})
        report = log.get_report()
        assert report[0]["input_tokens"] == 42
        assert report[0]["output_tokens"] == 0
        assert report[0]["cache_creation_input_tokens"] == 0
        assert report[0]["cache_read_input_tokens"] == 0
