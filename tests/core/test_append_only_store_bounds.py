"""B11: append-only JSONL stores are bounded oldest-first, exercised via the shared
append_and_trim_jsonl / trim_jsonl_lines primitives every writer routes through."""

from __future__ import annotations

import json

import pytest

from autoskillit.core.runtime import append_and_trim_jsonl, trim_jsonl_lines

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def test_trim_jsonl_lines_keeps_the_newest_oldest_first_order() -> None:
    lines = [f"line-{i}" for i in range(10)]

    trimmed = trim_jsonl_lines(lines, max_lines=3)

    assert trimmed == ["line-7", "line-8", "line-9"]


def test_trim_jsonl_lines_is_a_noop_under_the_ceiling() -> None:
    lines = ["a", "b"]

    assert trim_jsonl_lines(lines, max_lines=10) == ["a", "b"]


def test_append_and_trim_jsonl_writes_past_the_ceiling_and_stays_under_it(tmp_path) -> None:
    path = tmp_path / "events.jsonl"

    for i in range(50):
        append_and_trim_jsonl(path, json.dumps({"i": i}), max_lines=10)

    lines = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    assert len(lines) == 10
    assert [line["i"] for line in lines] == list(range(40, 50))


@pytest.mark.parametrize(
    ("module_path", "attr_name"),
    [
        ("autoskillit.core.runtime.session_provenance", "_MAX_PROVENANCE_RECORDS"),
        ("autoskillit.fleet._dispatch_reaper", "_MAX_REAPER_EVENTS"),
    ],
)
def test_the_named_jsonl_stores_declare_a_bound(module_path: str, attr_name: str) -> None:
    import importlib

    module = importlib.import_module(module_path)
    bound = getattr(module, attr_name)
    assert isinstance(bound, int)
    assert bound > 0


def test_hook_settings_jsonl_writers_declare_a_bound() -> None:
    """quota_events.jsonl / join_diagnostics.jsonl -- hooks/_hook_settings.py is
    stdlib-only (no autoskillit.* imports), so it duplicates the trim helper rather than
    importing core.runtime.append_and_trim_jsonl; confirm the duplicate still bounds."""
    from autoskillit.hooks import _hook_settings

    assert _hook_settings._MAX_HOOK_LOG_LINES > 0

    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        log_path = Path(tmp) / "events.jsonl"
        for i in range(_hook_settings._MAX_HOOK_LOG_LINES + 20):
            _hook_settings._append_and_trim_jsonl_line(
                log_path, json.dumps({"i": i}), max_lines=_hook_settings._MAX_HOOK_LOG_LINES
            )
        lines = [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]
        assert len(lines) == _hook_settings._MAX_HOOK_LOG_LINES


def test_run_skill_dispatch_cleanup_failure_sink_declares_a_bound() -> None:
    from autoskillit.server.tools.tools_execution import _run_skill_dispatch

    assert _run_skill_dispatch._MAX_CLEANUP_FAILURE_RECORDS > 0
