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


def test_find_rollouts_matches_the_real_two_level_yyyy_mm_layout(tmp_path: Path) -> None:
    # _codex_session_storage.py lays out rollouts as <root>/YYYY/MM/rollout-*.jsonl --
    # never a third YYYY/MM/DD level.
    rollout = tmp_path / "2026" / "07" / "rollout-a.jsonl"
    rollout.parent.mkdir(parents=True)
    rollout.write_text("{}")
    found = measurer._find_rollouts(tmp_path, None, None)
    assert found == [rollout]


def _write_dated_rollout(tmp_path: Path, year: str, month: str, day: str, thread: str) -> Path:
    directory = tmp_path / year / month
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"rollout-{year}-{month}-{day}T10-00-00-{thread}.jsonl"
    path.write_text("{}")
    return path


def test_find_rollouts_date_filter_actually_filters(tmp_path: Path) -> None:
    june = _write_dated_rollout(tmp_path, "2026", "06", "15", "thread-june")
    july_01 = _write_dated_rollout(tmp_path, "2026", "07", "01", "thread-july-01")
    july_15 = _write_dated_rollout(tmp_path, "2026", "07", "15", "thread-july-15")
    july_31 = _write_dated_rollout(tmp_path, "2026", "07", "31", "thread-july-31")
    august = _write_dated_rollout(tmp_path, "2026", "08", "05", "thread-august")

    # Month-precision bounds still work at day-precision resolution: every day
    # inside the bounded month is included.
    found = measurer._find_rollouts(tmp_path, "2026-07", "2026-07")
    assert found == [july_01, july_15, july_31]

    # Day-precision --since must not silently drop the whole target month. Before
    # the fix, the extracted date key was month precision ("2026-07"), which
    # string-sorts before any day-precision --since ("2026-07" < "2026-07-18"),
    # so every rollout in the target month was incorrectly dropped.
    found = measurer._find_rollouts(tmp_path, "2026-07-18", None)
    assert found == [july_31, august]

    # Day-precision --until must not symmetrically over-include the whole target
    # month ("2026-07" > "2026-07-18" was False, so all of July was kept).
    found = measurer._find_rollouts(tmp_path, None, "2026-07-18")
    assert found == [june, july_01, july_15]

    # --since/--until are documented as inclusive: the exact boundary date itself
    # must be kept, not excluded.
    found = measurer._find_rollouts(tmp_path, "2026-07-15", "2026-07-15")
    assert found == [july_15]


def test_extract_target_path_survives_pipe_alternation_in_rg_pattern() -> None:
    # AGENTS.md documents `|` alternation as this repo's ripgrep idiom -- the
    # extractor must not truncate at a `|` that is inside the quoted pattern.
    assert (
        measurer.extract_target_path("rg -n 'foo|bar' src/autoskillit/file.py")
        == "src/autoskillit/file.py"
    )
    assert (
        measurer.extract_target_path('rg -n "foo|bar|baz" src/autoskillit/file.py')
        == "src/autoskillit/file.py"
    )
    assert (
        measurer.extract_target_path("rg -n 'foo|bar' src/autoskillit/file.py | head -c 18000")
        == "src/autoskillit/file.py"
    )


def test_classify_policy_cohort_survives_an_unreadable_file(tmp_path: Path) -> None:
    # A directory named like a rollout file can never be read_text'd -- this must
    # not abort a batch run over many rollouts.
    unreadable = tmp_path / "rollout-unreadable.jsonl"
    unreadable.mkdir()
    assert measurer.classify_policy_cohort(unreadable) == "none"
