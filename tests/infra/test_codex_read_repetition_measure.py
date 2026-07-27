"""Tests for scripts/measure_codex_read_repetition.py (#4351).

Exercises the extractor, bounded-read classifier, and cohort aggregator against a
synthetic rollout corpus so the measurement tool is covered code, not an unwired
artifact.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.small

SCRIPT = Path(__file__).parents[2] / "scripts" / "measure_codex_read_repetition.py"


def _load_measurer() -> ModuleType:
    spec = importlib.util.spec_from_file_location("codex_read_repetition", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


measurer = _load_measurer()


def _exec_record(cmd: str) -> dict:
    return {
        "type": "response_item",
        "payload": {
            "type": "function_call",
            "name": "exec_command",
            "arguments": json.dumps({"cmd": cmd}),
        },
    }


def _custom_tool_call_record(raw_input: str) -> dict:
    return {
        "type": "response_item",
        "payload": {"type": "custom_tool_call", "name": "exec", "input": raw_input},
    }


def _cohort_marker_record(text: str) -> dict:
    return {"type": "response_item", "payload": {"type": "message", "content": text}}


def _write_rollout(tmp_path: Path, name: str, records: list[dict]) -> Path:
    path = tmp_path / name
    with path.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")
    return path


def test_classifier_counts_only_leading_bounded_reads() -> None:
    assert measurer.is_bounded_read("sed -n '1,10p' /some/path")
    assert not measurer.is_bounded_read("gh pr view 123 | head -c 18000")


def test_repeat_reads_are_counted_per_session_not_per_corpus(tmp_path: Path) -> None:
    same_session = _write_rollout(
        tmp_path,
        "rollout-a.jsonl",
        [
            _exec_record("sed -n '1,10p' /a/b.py"),
            _exec_record("sed -n '11,20p' /a/b.py"),
        ],
    )
    row = measurer.measure_rollout(same_session)
    assert row["bounded_reads"] == 2
    assert row["repeat_reads"] == 1

    rollout_1 = _write_rollout(
        tmp_path, "rollout-b.jsonl", [_exec_record("sed -n '1,10p' /a/b.py")]
    )
    rollout_2 = _write_rollout(
        tmp_path, "rollout-c.jsonl", [_exec_record("sed -n '1,10p' /a/b.py")]
    )
    report = measurer.aggregate_report([rollout_1, rollout_2])
    assert report["cohorts"]["none"]["bounded_read_count"] == 2
    assert report["cohorts"]["none"]["repeat_count"] == 0


def test_policy_version_cohort_split(tmp_path: Path) -> None:
    v1 = _write_rollout(
        tmp_path,
        "rollout-v1.jsonl",
        [
            _cohort_marker_record("Context Intake Discipline v1:\n- Never read end-to-end."),
            _exec_record("sed -n '1,10p' /a.py"),
            _exec_record("sed -n '11,20p' /a.py"),
        ],
    )
    v2 = _write_rollout(
        tmp_path,
        "rollout-v2.jsonl",
        [
            _cohort_marker_record("Context Intake Discipline v2:\n- Read completely."),
            _exec_record("sed -n '1,10p' /b.py"),
        ],
    )
    report = measurer.aggregate_report([v1, v2])
    assert report["cohorts"]["v1"]["bounded_read_count"] == 2
    assert report["cohorts"]["v1"]["repeat_count"] == 1
    assert report["cohorts"]["v1"]["repeat_read_rate"] == pytest.approx(0.5)
    assert report["cohorts"]["v2"]["bounded_read_count"] == 1
    assert report["cohorts"]["v2"]["repeat_count"] == 0
    assert report["cohorts"]["v2"]["repeat_read_rate"] == pytest.approx(0.0)


def test_unparseable_records_are_skipped_not_fatal(tmp_path: Path) -> None:
    path = tmp_path / "rollout-corrupt.jsonl"
    path.write_text(
        "{not valid json\n" + json.dumps(_exec_record("sed -n '1,10p' /a.py")) + "\n",
        encoding="utf-8",
    )
    commands, unclassified = measurer.classify_rollout_records(path)
    assert len(commands) == 1
    assert unclassified == 0


def test_unclassified_exec_shapes_are_counted_and_reported(tmp_path: Path) -> None:
    records = [_custom_tool_call_record("this does not match the exec_command JS template")]
    path = _write_rollout(tmp_path, "rollout-unclassified.jsonl", records)
    commands, unclassified = measurer.classify_rollout_records(path)
    assert commands == []
    assert unclassified == 1
