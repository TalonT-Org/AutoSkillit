"""Focused checks for the resumable evidence source walk."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
import zstandard

import autoskillit.execution.evidence._native_child_projection as native_child_projection
from autoskillit.core import (
    TOKEN_USAGE_SCHEMA_VERSION,
    TURN_USAGE_SCHEMA_VERSION,
    TokenMeasure,
    iter_merged_assistant_turns,
)
from autoskillit.execution.evidence.report_walk import (
    SourceGapError,
    WalkItem,
    iter_report_walk,
)
from autoskillit.execution.session_log.session_index import (
    read_tolerant_session_index_rows,
)
from tests.execution._report_index_fixtures import _turn_usage_descriptor

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
    backend: str | None = None,
    timestamp: str | None = None,
    duration_seconds: float | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {"dir_name": dir_name, "session_id": session_id}
    if claude_code_log is not ...:
        row["claude_code_log"] = claude_code_log
    if codex_log is not ...:
        row["codex_log"] = codex_log
    if backend is not None:
        row["backend"] = backend
    if timestamp is not None:
        row["timestamp"] = timestamp
    if duration_seconds is not None:
        row["duration_seconds"] = duration_seconds
    return row


def _turn_usage_row(
    *,
    backend: str,
    provider: str,
    model: str,
    timestamp: str | None,
) -> dict[str, Any]:
    return {
        "backend": backend,
        "provider_used": provider,
        "message_id": "message-1",
        "request_id": "request-1",
        "timestamp": timestamp,
        "model": model,
        "input_tokens": {"state": "measured_zero", "value": 0},
        "output_tokens": {"state": "unknown", "value": None},
        "cache_read_tokens": {"state": "measured", "value": 12},
        "cache_write_tokens": {"state": "unavailable", "value": None},
        "peak_context": {"state": "measured", "value": 12},
        "context_window_tokens": 100,
        "context_fraction": 0.12,
    }


def _write_turn_usage(
    root: Path,
    dir_name: str,
    *,
    descriptor: dict[str, Any] | None,
    ledger: bytes | None,
) -> None:
    session_dir = root / "sessions" / dir_name
    session_dir.mkdir(parents=True, exist_ok=True)
    if descriptor is not None:
        (session_dir / "token_usage.json").write_text(json.dumps(descriptor), encoding="utf-8")
    if ledger is not None:
        (session_dir / "turn_usage.jsonl").write_bytes(ledger)


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


def test_turn_ledgers_are_admitted_for_live_and_archived_sessions(
    tmp_path: Path,
) -> None:
    root = tmp_path / "logs"
    archived = _session("archived-attempt", "same-session-id", backend="claude-code")
    live = _session("live-attempt", "same-session-id", backend="codex")
    _write_jsonl(root / "sessions-archive.jsonl", [archived])
    _write_jsonl(root / "sessions.jsonl", [live])
    _write_turn_usage(
        root,
        "archived-attempt",
        descriptor=_turn_usage_descriptor(count=1),
        ledger=_json_line(
            _turn_usage_row(
                backend="claude-code",
                provider="anthropic",
                model="claude-resolved",
                timestamp="2026-10-01T00:00:00Z",
            )
        ),
    )
    _write_turn_usage(
        root,
        "live-attempt",
        descriptor=_turn_usage_descriptor(count=1),
        ledger=_json_line(
            {
                **_turn_usage_row(
                    backend="codex",
                    provider="openai",
                    model="gpt-resolved",
                    timestamp=None,
                ),
                "model": None,
                "context_window_tokens": None,
                "context_fraction": None,
            }
        ),
    )

    records = {
        item.source_id: item.record
        for item in iter_report_walk(root)
        if item.kind == "session" and item.record is not None
    }

    for source_id, provider, model in (
        ("archived-attempt", "anthropic", "claude-resolved"),
        ("live-attempt", "openai", None),
    ):
        assert records[source_id] is not None
        assert records[source_id]["turn_usage_state"] == "observed"
        assert records[source_id]["turn_usage_reason"] is None
        (turn,) = records[source_id]["turn_usage_rows"]
        assert turn["provider_used"] == provider
        assert turn["model"] == model
        assert turn["input_tokens"] == TokenMeasure.observed(0)
        assert turn["output_tokens"] == TokenMeasure.unknown()
    assert records["live-attempt"]["turn_usage_rows"][0]["timestamp"] is None
    assert records["live-attempt"]["turn_usage_rows"][0]["context_window_tokens"] is None
    assert records["live-attempt"]["turn_usage_rows"][0]["context_fraction"] is None


@pytest.mark.parametrize(
    ("descriptor", "ledger", "reason"),
    [
        (None, None, "descriptor-missing"),
        (
            _turn_usage_descriptor(count=0, filename=None),
            None,
            "ledger-not-published",
        ),
        (_turn_usage_descriptor(count=0), None, "ledger-missing"),
        (
            {
                **_turn_usage_descriptor(count=1),
                "schema_version": TOKEN_USAGE_SCHEMA_VERSION + 1,
            },
            None,
            "descriptor-unsupported-version",
        ),
        (
            _turn_usage_descriptor(count=1, version=TURN_USAGE_SCHEMA_VERSION + 1),
            None,
            "ledger-unsupported-version",
        ),
        (
            _turn_usage_descriptor(count=2),
            _json_line(
                _turn_usage_row(
                    backend="codex",
                    provider="openai",
                    model="gpt-resolved",
                    timestamp=None,
                )
            ),
            "ledger-count-mismatch",
        ),
        (
            _turn_usage_descriptor(count=2),
            b'{"input_tokens":{"state":"measured","value":1}}\nnot-json',
            "ledger-malformed",
        ),
        (
            _turn_usage_descriptor(count=1, filename="../turn_usage.jsonl"),
            None,
            "descriptor-invalid-ledger-file",
        ),
    ],
)
def test_invalid_or_absent_turn_ledgers_have_explicit_coverage(
    tmp_path: Path,
    descriptor: dict[str, Any] | None,
    ledger: bytes | None,
    reason: str,
) -> None:
    root = tmp_path / "logs"
    _write_jsonl(root / "sessions.jsonl", [_session("attempt", "sid")])
    _write_turn_usage(root, "attempt", descriptor=descriptor, ledger=ledger)

    (item,) = [candidate for candidate in iter_report_walk(root) if candidate.kind == "session"]

    assert item.record is not None
    assert item.record["turn_usage_state"] == "unavailable"
    assert item.record["turn_usage_reason"] == reason
    assert item.record["turn_usage_rows"] == []


def test_session_walk_reads_child_outcomes_by_native_parent_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "logs"
    child = {
        "child_id": "native-child",
        "backend": "claude_code",
        "parent_session_id": "native-parent-id",
        "role": "audit-bugs",
        "attribution_skill": "make-plan",
        "effective_provider": "",
        "effective_model": "",
        "evidence_source": "transcript_metadata",
    }
    calls: list[tuple[str, str, Path]] = []

    def collect(*, backend: str, parent_session_id: str, log_root: Path):
        calls.append((backend, parent_session_id, log_root))
        return (child,)

    monkeypatch.setattr(native_child_projection, "collect_child_outcomes", collect)
    _write_jsonl(
        root / "sessions.jsonl",
        [_session("report-index-key", "native-parent-id", backend="claude-code")],
    )

    (item,) = [candidate for candidate in iter_report_walk(root) if candidate.kind == "session"]

    assert calls == [("claude_code", "native-parent-id", root)]
    assert item.source_id == "report-index-key"
    assert item.session_id == "native-parent-id"
    assert item.record is not None
    (projected_child,) = item.record["child_outcomes"]
    assert projected_child["child_id"] == "native-child"
    assert projected_child["native_parent_session_id"] == "native-parent-id"
    assert projected_child["parent_session_key"] == "report-index-key"
    assert projected_child["transcript_state"] == "unknown"
    assert projected_child["usage_state"] == "unknown"
    assert item.watermark["projection"]["report-index-key"]


def test_resumed_native_parent_owns_only_timestamp_matched_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "logs"
    parent_log = tmp_path / "project" / "native-parent-id.jsonl"
    parent_log.parent.mkdir(parents=True)
    parent_log.write_text("{}\n", encoding="utf-8")
    child_dir = parent_log.parent / "native-parent-id" / "subagents"
    _write_jsonl(
        child_dir / "agent-child-in-second-resume.jsonl",
        [
            {
                "type": "assistant",
                "timestamp": "2026-10-05T10:00:25Z",
                "message": {"id": "m1", "content": []},
            }
        ],
    )
    _write_jsonl(
        child_dir / "agent-child-without-time.jsonl",
        [{"type": "assistant", "message": {"id": "m2", "content": []}}],
    )
    (child_dir / "agent-incomplete-child.jsonl").write_text(
        json.dumps(
            {
                "type": "assistant",
                "timestamp": "2026-10-05T10:00:26Z",
                "message": {"id": "m3", "content": []},
            }
        ),
        encoding="utf-8",
    )
    children = (
        {
            "child_id": "child-in-second-resume",
            "backend": "claude_code",
            "parent_session_id": "native-parent-id",
            "role": "audit-bugs",
            "attribution_skill": "make-plan",
            "effective_provider": "anthropic",
            "effective_model": "claude-child",
            "evidence_source": "transcript_metadata",
        },
        {
            "child_id": "child-without-time",
            "backend": "claude_code",
            "parent_session_id": "native-parent-id",
            "role": "audit-bugs",
            "attribution_skill": "make-plan",
            "effective_provider": "anthropic",
            "effective_model": "claude-child",
            "evidence_source": "transcript_metadata",
        },
        {
            "child_id": "incomplete-child",
            "backend": "claude_code",
            "parent_session_id": "native-parent-id",
            "role": "audit-bugs",
            "attribution_skill": "make-plan",
            "effective_provider": "anthropic",
            "effective_model": "claude-child",
            "evidence_source": "transcript_metadata",
        },
    )
    monkeypatch.setattr(
        native_child_projection,
        "collect_child_outcomes",
        lambda **_kwargs: children,
    )
    _write_jsonl(
        root / "sessions.jsonl",
        [
            _session(
                "resume-a",
                "native-parent-id",
                backend="claude-code",
                claude_code_log=str(parent_log),
                timestamp="2026-10-05T10:00:00Z",
                duration_seconds=10,
            ),
            _session(
                "resume-b",
                "native-parent-id",
                backend="claude-code",
                claude_code_log=str(parent_log),
                timestamp="2026-10-05T10:00:20Z",
                duration_seconds=10,
            ),
        ],
    )

    sessions = {
        item.source_id: item.record for item in iter_report_walk(root) if item.kind == "session"
    }

    assert sessions["resume-a"]["child_outcomes"] == ()
    owned_children = {child["child_id"]: child for child in sessions["resume-b"]["child_outcomes"]}
    tool_free = owned_children["child-in-second-resume"]
    assert tool_free["tool_counts"] == {}
    assert tool_free["transcript_state"] == "observed"
    assert tool_free["usage_state"] == "unknown"
    incomplete = owned_children["incomplete-child"]
    assert incomplete["tool_counts"] is None
    assert incomplete["transcript_state"] == "unknown"
    assert incomplete["usage_state"] == "unknown"


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
def test_native_codex_rollouts_yield_one_turn_per_response_item(
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


def test_unchanged_walk_skips_record_yield_and_resume_follows_changes(
    tmp_path: Path,
) -> None:
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

    # Resume from the just-committed watermark: nothing has changed on disk, so
    # the walker yields no records (no checkpoint from _walk_otlp or
    # _walk_archive; a projection checkpoint requires an identity change).
    assert list(iter_report_walk(root, watermark)) == []

    new_transcript = tmp_path / "new-parent.jsonl"
    new_transcript.write_text('{"type":"assistant","message":{"content":[]}}\n')
    with (root / "otlp.jsonl").open("ab") as handle:
        handle.write(_json_line(_otlp("otlp-new", session_id="sid-new")))
    with (root / "sessions.jsonl").open("ab") as handle:
        handle.write(
            _json_line(_session("session-new", "sid-new", claude_code_log=str(new_transcript)))
        )

    changed = list(iter_report_walk(root, watermark))
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

    with pytest.raises(SourceGapError) as excinfo:
        list(iter_report_walk(root, committed))
    assert excinfo.value.source == "otlp"


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

    with pytest.raises(SourceGapError) as excinfo:
        list(iter_report_walk(idless_root, idless_watermark))
    assert excinfo.value.source == "otlp"


def test_replaced_archive_reports_archive_gap(tmp_path: Path) -> None:
    root = tmp_path / "logs"
    archive = root / "sessions-archive.jsonl"
    _write_jsonl(
        archive,
        [_session("archived-1", "sid-1"), _session("archived-2", "sid-2")],
    )
    first_pass = list(iter_report_walk(root))
    watermark = first_pass[-1].watermark

    original = archive.read_bytes()
    replacement = original.replace(b'"sid-1"', b'"sid-x"')
    assert len(replacement) == len(original)
    archive.write_bytes(replacement)

    with pytest.raises(SourceGapError) as excinfo:
        list(iter_report_walk(root, watermark))
    assert excinfo.value.source == "archive"


def test_archive_streams_complete_lines_and_skips_incomplete_ones(
    tmp_path: Path,
) -> None:
    root = tmp_path / "logs"
    archive = root / "sessions-archive.jsonl"
    rows = [_session(f"archived-{index}", f"sid-{index}") for index in range(24)]
    archive.parent.mkdir(parents=True, exist_ok=True)
    complete = b"".join(_json_line(row) for row in rows)
    malformed = b"{malformed}\n"
    incomplete = b'{"dir_name":"pending","session_id":"sid-pending"}'
    archive.write_bytes(complete + malformed + incomplete)

    walker = iter(iter_report_walk(root))
    assert next(walker).source_id == "archived-0"
    walker.close()

    first_pass = list(iter_report_walk(root))
    watermark = first_pass[-1].watermark
    assert watermark["archive"]["offset"] == len(complete + malformed)
    assert len([item for item in first_pass if item.kind == "session"]) == len(rows)

    with archive.open("ab") as handle:
        handle.write(b"\n")
    resumed = list(iter_report_walk(root, watermark))
    assert [item.source_id for item in resumed if item.kind == "session"] == ["pending"]


def test_walk_emits_each_record_when_sources_are_larger_than_yielded_state(
    tmp_path: Path,
) -> None:
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

    # The retained projection has one row plus 96 archive rows; the multi-megabyte
    # streams must not cause the walk to skip records or omit source items.
    assert archive.stat().st_size + active.stat().st_size > 6_000_000
    kinds = Counter(item.kind for item in iter_report_walk(root))

    assert kinds["otlp"] == 96
    assert kinds["session"] == 97


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
