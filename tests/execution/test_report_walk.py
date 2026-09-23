"""Focused checks for the resumable evidence source walk."""

from __future__ import annotations

import json
import tracemalloc
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
import zstandard

from autoskillit.core import iter_merged_assistant_turns
from autoskillit.execution.evidence.report_walk import (
    SourceGapError,
    WalkItem,
    iter_report_walk,
)
from autoskillit.execution.session_log.session_index import (
    read_tolerant_session_index_rows,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]


def _json_line(value: dict[str, Any]) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode("utf-8") + b"\n"


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"".join(_json_line(row) for row in rows))


def _otlp(record_id: str, *, session_id: str = "sid-1", payload: object = None) -> dict[str, Any]:
    return {
        "record_id": record_id,
        "signal": "logs",
        "payload": {"session_id": session_id} if payload is None else payload,
    }


def _session(
    dir_name: str,
    session_id: str,
    *,
    claude_code_log: object = ...,
    codex_log: object = ...,
) -> dict[str, Any]:
    row: dict[str, Any] = {"dir_name": dir_name, "session_id": session_id}
    if claude_code_log is not ...:
        row["claude_code_log"] = claude_code_log
    if codex_log is not ...:
        row["codex_log"] = codex_log
    return row


def _consume(
    root: Path,
    store: dict[str, Any],
    *,
    pause_before_record: int | None = None,
    pause_after_record: int | None = None,
) -> list[WalkItem]:
    emitted: list[WalkItem] = []
    committed_records: dict[str, dict[str, Any]] = store["records"]
    writes: Counter[str] = store["writes"]
    committed = 0
    for item in iter_report_walk(root, store["watermark"]):
        if item.record is not None and pause_before_record == committed:
            break
        emitted.append(item)
        if item.record is not None:
            assert item.source_id is not None
            committed_records[item.source_id] = item.record
            writes[item.source_id] += 1
            committed += 1
        store["watermark"] = item.watermark
        if item.record is not None and pause_after_record == committed:
            break
    return emitted


def _store() -> dict[str, Any]:
    return {"records": {}, "writes": Counter(), "watermark": None}


def test_first_pass_emits_joinable_records_and_tolerates_old_index_rows(
    tmp_path: Path,
) -> None:
    root = tmp_path / "logs"
    root.mkdir()
    transcript = tmp_path / "parent.jsonl"
    transcript.write_text(
        json.dumps(
            {
                "type": "assistant",
                "requestId": "req-1",
                "message": {"content": [{"type": "tool_use", "name": "Read"}]},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    current = _session("session-current", "sid-1", claude_code_log=str(transcript))
    old_schema = {"dir_name": "session-old", "session_id": "sid-old"}
    (root / "sessions.jsonl").write_bytes(
        b"{malformed}\n"
        + json.dumps(["old non-object row"]).encode("utf-8")
        + b"\n"
        + _json_line(current)
        + _json_line(old_schema)
    )
    _write_jsonl(root / "otlp.jsonl", [_otlp("otlp-1")])

    items = list(iter_report_walk(root))
    sessions = {item.session_id: item.record for item in items if item.kind == "session"}
    otlp_items = [item for item in items if item.kind == "otlp"]

    assert [item.source_id for item in otlp_items] == ["otlp-1"]
    assert otlp_items[0].session_id == "sid-1"
    assert sessions["sid-1"] is not None
    assert sessions["sid-1"]["row"] == current
    assert sessions["sid-1"]["assistant_turn_count"] == 1
    assert sessions["sid-1"]["assistant_turns"][0]["tool_names"] == ["Read"]
    assert sessions["sid-old"]["row"] == old_schema
    assert sessions["sid-old"]["assistant_turn_count"] is None
    assert read_tolerant_session_index_rows(root / "sessions.jsonl") == [current, old_schema]


@pytest.mark.parametrize("pause", ("before", "after"))
def test_committed_watermark_resumes_without_missing_or_duplicate_records(
    tmp_path: Path, pause: str
) -> None:
    root = tmp_path / "logs"
    rows = [_session(f"session-{index}", f"sid-{index}") for index in range(3)]
    _write_jsonl(root / "sessions.jsonl", rows)
    _write_jsonl(root / "otlp.jsonl", [_otlp("otlp-1"), _otlp("otlp-2")])
    expected = {item.source_id for item in iter_report_walk(root) if item.record is not None}
    store = _store()

    _consume(
        root,
        store,
        pause_before_record=0 if pause == "before" else None,
        pause_after_record=1 if pause == "after" else None,
    )
    _consume(root, store)

    assert set(store["records"]) == expected
    assert all(count == 1 for count in store["writes"].values())


def test_resume_after_first_changed_session_processes_remaining_snapshot(
    tmp_path: Path,
) -> None:
    root = tmp_path / "logs"
    old = _session("session-old", "sid-old")
    _write_jsonl(root / "sessions.jsonl", [old])
    store = _store()
    _consume(root, store)

    first = _session("session-new-1", "sid-new-1")
    second = _session("session-new-2", "sid-new-2")
    _write_jsonl(root / "sessions.jsonl", [old, first, second])
    _consume(root, store, pause_after_record=1)
    _consume(root, store)

    assert {record["row"]["dir_name"] for record in store["records"].values()} == {
        "session-old",
        "session-new-1",
        "session-new-2",
    }
    assert all(count == 1 for count in store["writes"].values())


@pytest.mark.parametrize(
    "row",
    (
        {"dir_name": "null-path", "session_id": "sid-null", "claude_code_log": None},
        {"dir_name": "invalid-path", "session_id": "sid-invalid", "claude_code_log": 42},
        {"dir_name": "missing-path", "session_id": "sid-missing"},
    ),
)
def test_unavailable_transcript_paths_are_not_reported_as_zero_turns(
    tmp_path: Path, row: dict[str, Any]
) -> None:
    root = tmp_path / "logs"
    _write_jsonl(root / "sessions.jsonl", [row])

    session = next(item for item in iter_report_walk(root) if item.kind == "session")

    assert session.record is not None
    assert session.record["assistant_turn_count"] is None
    assert session.record["transcripts_available"] is False


def test_iter_merged_assistant_turns_rejects_unsupported_backend() -> None:
    with pytest.raises(ValueError, match="Unsupported transcript backend"):
        list(iter_merged_assistant_turns("", backend="gemini"))


@pytest.mark.parametrize("compressed", (False, True))
def test_native_codex_rollouts_count_shared_assistant_turns(
    tmp_path: Path, compressed: bool
) -> None:
    root = tmp_path / "logs"
    suffix = ".jsonl.zst" if compressed else ".jsonl"
    rollout = tmp_path / f"codex{suffix}"
    event = {
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "Codex response"}],
        },
    }
    contents = _json_line(event)
    rollout.write_bytes(zstandard.ZstdCompressor().compress(contents) if compressed else contents)
    _write_jsonl(
        root / "sessions.jsonl",
        [_session("codex-session", "codex-sid", codex_log=str(rollout))],
    )

    session = next(item for item in iter_report_walk(root) if item.kind == "session")

    assert session.record is not None
    assert session.record["assistant_turn_count"] == 1
    assert session.record["assistant_turns"][0]["turn_id"].endswith(":turn-0")


def test_unchanged_walk_skips_transcript_open_and_otlp_payload_parse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autoskillit.execution.evidence import report_walk

    root = tmp_path / "logs"
    old_transcript = tmp_path / "old-parent.jsonl"
    old_transcript.write_text('{"type":"assistant","message":{"content":[]}}\n')
    _write_jsonl(
        root / "sessions.jsonl",
        [_session("session-old", "sid-old", claude_code_log=str(old_transcript))],
    )
    _write_jsonl(root / "otlp.jsonl", [_otlp("otlp-old")])
    first = list(iter_report_walk(root))
    watermark = first[-1].watermark

    opened_transcripts: list[Path] = []
    parsed_otlp_ids: list[str] = []
    original_open = Path.open
    original_loads = json.loads

    def tracked_open(path: Path, *args: Any, **kwargs: Any) -> Any:
        if path.name.endswith("parent.jsonl"):
            opened_transcripts.append(path)
        return original_open(path, *args, **kwargs)

    def tracked_loads(data: Any, *args: Any, **kwargs: Any) -> Any:
        raw = data if isinstance(data, bytes) else data.encode() if isinstance(data, str) else b""
        if raw.startswith(b'{"record_id":'):
            parsed_otlp_ids.append(original_loads(raw)["record_id"])
        return original_loads(data, *args, **kwargs)

    monkeypatch.setattr(Path, "open", tracked_open)
    monkeypatch.setattr(report_walk.json, "loads", tracked_loads)
    assert list(iter_report_walk(root, watermark)) == []
    assert opened_transcripts == []
    assert parsed_otlp_ids == []

    new_transcript = tmp_path / "new-parent.jsonl"
    new_transcript.write_text('{"type":"assistant","message":{"content":[]}}\n')
    with (root / "otlp.jsonl").open("ab") as handle:
        handle.write(_json_line(_otlp("otlp-new", session_id="sid-new")))
    with (root / "sessions.jsonl").open("ab") as handle:
        handle.write(
            _json_line(_session("session-new", "sid-new", claude_code_log=str(new_transcript)))
        )

    opened_transcripts.clear()
    changed = list(iter_report_walk(root, watermark))
    assert parsed_otlp_ids == ["otlp-new"]
    assert opened_transcripts == [new_transcript]
    assert [item.source_id for item in changed if item.kind == "otlp"] == ["otlp-new"]
    assert [item.source_id for item in changed if item.kind == "session"] == ["session-new"]


def test_rotation_resumes_from_record_id_and_keeps_equal_payloads_distinct(
    tmp_path: Path,
) -> None:
    root = tmp_path / "logs"
    first = _otlp("record-1", payload={"same": True})
    second = _otlp("record-2", payload={"same": True})
    third = _otlp("record-3", payload={"new": True})
    original = _json_line(first) + _json_line(second)
    (root / "otlp.jsonl").parent.mkdir(parents=True, exist_ok=True)
    (root / "otlp.jsonl").write_bytes(original)
    walker = iter(iter_report_walk(root))
    first_item = next(walker)
    assert first_item.source_id == "record-1"
    walker.close()

    (root / "otlp.jsonl.1").write_bytes(original)
    (root / "otlp.jsonl").write_bytes(_json_line(third))
    resumed = list(iter_report_walk(root, first_item.watermark))

    assert [item.source_id for item in resumed if item.kind == "otlp"] == [
        "record-2",
        "record-3",
    ]


def test_lost_otlp_generation_reports_gap_on_resume(tmp_path: Path) -> None:
    root = tmp_path / "logs"
    first = _otlp("old-id", payload={"same": True})
    active = root / "otlp.jsonl"
    active.parent.mkdir(parents=True)
    active.write_bytes(_json_line(first))
    walker = iter(iter_report_walk(root))
    committed = next(walker).watermark
    walker.close()
    (root / "otlp.jsonl.1").write_bytes(
        _json_line(_otlp("replacement-id", payload={"same": True}))
    )
    active.write_bytes(_json_line(_otlp("latest-id")))

    with pytest.raises(SourceGapError):
        list(iter_report_walk(root, committed))


def test_changed_idless_resume_reports_gap_when_fingerprint_diverges(
    tmp_path: Path,
) -> None:
    idless_root = tmp_path / "idless-logs"
    idless_active = idless_root / "otlp.jsonl"
    idless_active.parent.mkdir(parents=True)
    idless_active.write_bytes(b'{"signal":"logs","payload":{}}\n')
    idless_walker = iter(iter_report_walk(idless_root))
    idless_watermark = next(idless_walker).watermark
    idless_walker.close()
    idless_active.write_bytes(b'{"signal":"logs","payload":{"changed":true}}\n')

    with pytest.raises(SourceGapError):
        list(iter_report_walk(idless_root, idless_watermark))


def test_archive_streams_complete_lines_and_checkpoints_skipped_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autoskillit.execution.evidence import report_walk

    root = tmp_path / "logs"
    archive = root / "sessions-archive.jsonl"
    rows = [_session(f"archived-{index}", f"sid-{index}") for index in range(24)]
    archive.parent.mkdir(parents=True, exist_ok=True)
    complete = b"".join(_json_line(row) for row in rows)
    malformed = b"{malformed}\n"
    incomplete = b'{"dir_name":"pending","session_id":"sid-pending"}'
    archive.write_bytes(complete + malformed + incomplete)

    seen_rows: list[dict[str, Any] | None] = []
    original_iterator = report_walk.iter_tolerant_session_index_lines

    def tracked_iterator(path: Path, **kwargs: Any):
        for end, row in original_iterator(path, **kwargs):
            if path == archive:
                seen_rows.append(row)
            yield end, row

    monkeypatch.setattr(report_walk, "iter_tolerant_session_index_lines", tracked_iterator)
    walker = iter(iter_report_walk(root))
    first = next(walker)
    assert first.source_id == "archived-0"
    assert seen_rows == [rows[0]]
    walker.close()

    first_pass = list(iter_report_walk(root))
    watermark = first_pass[-1].watermark
    assert watermark["archive"]["offset"] == len(complete + malformed)
    assert len([item for item in first_pass if item.kind == "session"]) == len(rows)

    with archive.open("ab") as handle:
        handle.write(b"\n")
    resumed = list(iter_report_walk(root, watermark))
    assert [item.source_id for item in resumed if item.kind == "session"] == ["pending"]


def test_peak_memory_stays_with_one_record_and_transcript(tmp_path: Path) -> None:
    root = tmp_path / "logs"
    root.mkdir()
    transcript = tmp_path / "parent.jsonl"
    transcript.write_bytes(
        _json_line(
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "t" * 32_768}]},
            }
        )
    )
    parent_subagents = tmp_path / "parent" / "subagents"
    parent_subagents.mkdir(parents=True)
    (parent_subagents / "agent-child.jsonl").write_bytes(transcript.read_bytes())
    _write_jsonl(
        root / "sessions.jsonl",
        [_session("retained", "retained-id", claude_code_log=str(transcript))],
    )

    archive = root / "sessions-archive.jsonl"
    active = root / "otlp.jsonl"
    with archive.open("wb") as archive_file, active.open("wb") as otlp_file:
        for index in range(96):
            archive_file.write(
                _json_line(
                    {
                        **_session(
                            f"archived-{index}", f"sid-{index}", claude_code_log=str(transcript)
                        ),
                        "padding": "a" * 32_768,
                    }
                )
            )
            otlp_file.write(_json_line(_otlp(f"otlp-{index}", payload={"blob": "o" * 32_768})))

    # The retained projection has one row. At a yield the walk may also hold one
    # session summary, its subagent paths, one transcript and merged turns, and
    # the current source record; the multi-megabyte streams must not accumulate.
    assert archive.stat().st_size + active.stat().st_size > 6_000_000
    tracemalloc.start()
    try:
        kinds = Counter(item.kind for item in iter_report_walk(root))
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert kinds["otlp"] == 96
    assert kinds["session"] == 97
    assert peak < 1_000_000


def test_removal_only_snapshot_emits_checkpoint_with_empty_projection(
    tmp_path: Path,
) -> None:
    root = tmp_path / "logs"
    index = root / "sessions.jsonl"
    row = _session("removed", "sid-removed")
    _write_jsonl(index, [row])
    first = list(iter_report_walk(root))
    watermark = first[-1].watermark
    index.write_bytes(b"")

    removed = list(iter_report_walk(root, watermark))
    assert removed
    assert all(item.record is None for item in removed)
    assert removed[-1].watermark["projection"] == {}


def test_session_index_reader_accepts_final_line_without_newline(
    tmp_path: Path,
) -> None:
    row = _session("removed", "sid-removed")
    no_newline = tmp_path / "valid-final-line.jsonl"
    no_newline.write_bytes(json.dumps(row).encode("utf-8"))
    assert read_tolerant_session_index_rows(no_newline) == [row]
