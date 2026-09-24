"""Focused checks for the derived report index."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from autoskillit.core import ArtifactLease, ArtifactLeaseContention
from autoskillit.execution import (
    REPORT_INDEX_SCHEMA_VERSION,
    read_report_index,
    rebuild_report_index,
    report_index,
    update_report_index,
)
from autoskillit.execution.evidence.report_walk import WalkItem
from tests.execution._report_index_fixtures import (
    CLAUDE_SCOPE,
    basic_session_row,
)
from tests.execution._report_index_fixtures import (
    otlp_log_record as _log,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]

_CLAUDE_SCOPE = CLAUDE_SCOPE
_T0_NS = 1_577_836_800_000_000_000


def _json_line(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode("utf-8") + b"\n"


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"".join(_json_line(row) for row in rows))


def _otlp_line(record_id: str, *records: dict[str, Any]) -> bytes:
    return _json_line(
        {
            "record_id": record_id,
            "signal": "logs",
            "payload": {
                "resourceLogs": [
                    {
                        "scopeLogs": [
                            {
                                "scope": {"name": _CLAUDE_SCOPE},
                                "logRecords": list(records),
                            }
                        ]
                    }
                ]
            },
        }
    )


def _session(dir_name: str, session_id: str, **fields: Any) -> dict[str, Any]:
    return {"dir_name": dir_name, "session_id": session_id, **fields}


def _basic_session(dir_name: str, session_id: str, **fields: Any) -> dict[str, Any]:
    return basic_session_row(dir_name, session_id, **fields)


def test_update_then_read_joins_events_to_sessions(tmp_path: Path) -> None:
    root = tmp_path / "logs"
    session_dir = "session-a"
    transcript = tmp_path / "parent.jsonl"
    transcript.write_text(
        json.dumps(
            {
                "type": "assistant",
                "requestId": "turn-1",
                "message": {"content": [{"type": "tool_use", "name": "Read"}]},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    _write_jsonl(
        root / "sessions.jsonl",
        [_basic_session(session_dir, "sid-1", claude_code_log=str(transcript))],
    )
    records = [
        _log(
            "api_request",
            "sid-1",
            time_ns=_T0_NS + 1_000_000_000,
            request_id="parent-request",
            cache_read_tokens=0,
            input_tokens=3,
        ),
        _log(
            "api_request",
            "sid-1",
            time_ns=_T0_NS + 2_000_000_000,
            request_id="subagent-request",
            **{"agent.name": "agent-1"},
        ),
        _log(
            "tool_result",
            "sid-1",
            time_ns=_T0_NS + 3_000_000_000,
            tool_use_id="tool-1",
            tool_name="Read",
            success=True,
        ),
        _log(
            "subagent_completed",
            "sid-1",
            time_ns=_T0_NS + 4_000_000_000,
            **{"agent.name": "agent-1", "agent_type": "explore"},
        ),
    ]
    (root / "otlp.jsonl").write_bytes(
        _otlp_line("otlp-record-1", *records)
        + _otlp_line("otlp-record-2", _log("api_request", "sid-1", request_id="timeless"))
    )

    result = update_report_index(root, tmp_path / "index")

    assert result.rows_written == 6
    assert result.source_gaps == ()
    indexed = read_report_index(tmp_path / "index")
    assert all(row["session_key"] == session_dir for row in indexed.requests.values())
    assert all(row["session_key"] == session_dir for row in indexed.tools.values())
    assert all(row["session_key"] == session_dir for row in indexed.subagents.values())
    parent = next(
        row for row in indexed.requests.values() if row["request_id"] == "parent-request"
    )
    assert parent["cache_read_tokens"] == {"state": "measured_zero", "value": 0}
    assert parent["cache_write_tokens"] == {"state": "unknown", "value": None}
    assert update_report_index(root, tmp_path / "index").rows_written == 0


def test_absent_attribute_resolves_by_attributed_pair(tmp_path: Path) -> None:
    root = tmp_path / "logs"
    _write_jsonl(
        root / "sessions.jsonl",
        [
            _basic_session("anthropic-session", "sid-a"),
            _basic_session("minimax-session", "sid-m", provider_used="minimax"),
        ],
    )
    (root / "otlp.jsonl").write_bytes(
        _otlp_line(
            "events",
            _log("api_request", "sid-a", time_ns=_T0_NS, request_id="req-a"),
            _log("api_request", "sid-m", time_ns=_T0_NS, request_id="req-m"),
            _log("api_request", "sid-unknown", time_ns=_T0_NS, request_id="req-orphan"),
        )
    )

    update_report_index(root, tmp_path / "index")
    indexed = read_report_index(tmp_path / "index")
    by_id = {row["request_id"]: row for row in indexed.requests.values()}

    assert by_id["req-a"]["cache_write_tokens"]["state"] == "unknown"
    assert by_id["req-m"]["cache_write_tokens"]["state"] == "unavailable"
    assert by_id["req-orphan"]["session_key"] is None
    assert by_id["req-orphan"]["cache_write_tokens"]["state"] == "unknown"
    persisted = [
        json.loads(line) for line in (tmp_path / "index" / "rows.jsonl").read_text().splitlines()
    ]
    persisted_requests = {
        row["request_id"]: row for row in persisted if row.get("kind") == "request"
    }
    assert all(persisted_requests[name]["cache_write_tokens"] is None for name in by_id)


def test_resume_attempts_attribute_events_by_time(tmp_path: Path) -> None:
    root = tmp_path / "logs"
    _write_jsonl(
        root / "sessions.jsonl",
        [
            _session(
                "sid",
                "sid",
                backend="claude-code",
                provider_used="anthropic",
                timestamp="2020-01-01T00:00:00Z",
            ),
            _session(
                "sid_2020-01-01T00-00-01Z",
                "sid",
                backend="claude-code",
                provider_used="anthropic",
                timestamp="2020-01-01T00:00:01Z",
            ),
            _session(
                "equal-a",
                "equal-sid",
                backend="claude-code",
                provider_used="anthropic",
                timestamp="2020-01-01T00:00:00Z",
            ),
            _session(
                "equal-z",
                "equal-sid",
                backend="claude-code",
                provider_used="anthropic",
                timestamp="2020-01-01T00:00:00Z",
            ),
        ],
    )
    (root / "otlp.jsonl").write_bytes(
        _otlp_line(
            "attempt-events",
            _log("api_request", "sid", time_ns=_T0_NS - 1_000_000_000, request_id="before"),
            _log("api_request", "sid", time_ns=_T0_NS + 500_000_000, request_id="between"),
            _log("api_request", "sid", time_ns=_T0_NS + 2_000_000_000, request_id="after"),
            _log("api_request", "sid", request_id="ambiguous"),
            _log(
                "api_request",
                "equal-sid",
                time_ns=_T0_NS + 1_000_000_000,
                request_id="equal-start",
            ),
        )
    )

    update_report_index(root, tmp_path / "index")
    by_id = {
        row["request_id"]: row for row in read_report_index(tmp_path / "index").requests.values()
    }

    assert [by_id[name]["session_key"] for name in ("before", "between", "after")] == [
        "sid",
        "sid",
        "sid_2020-01-01T00-00-01Z",
    ]
    assert by_id["equal-start"]["session_key"] == "equal-z"
    assert by_id["ambiguous"]["session_key"] is None
    assert by_id["ambiguous"]["cache_write_tokens"]["state"] == "unknown"


def test_rebuild_from_scratch_equals_incremental_build(tmp_path: Path) -> None:
    root = tmp_path / "logs"
    inc = tmp_path / "incremental"
    full = tmp_path / "full"
    transcript_paths = {name: tmp_path / f"{name}.jsonl" for name in ("a", "b", "c")}
    for path in transcript_paths.values():
        path.write_text(
            json.dumps(
                {
                    "type": "assistant",
                    "requestId": "turn-1",
                    "message": {"content": [{"type": "tool_use", "name": "Read"}]},
                }
            )
            + "\n",
            encoding="utf-8",
        )
    sessions = [
        _basic_session(
            "a", "sid-a", claude_code_log=str(transcript_paths["a"]), subtype="initial"
        ),
        _basic_session(
            "b", "sid-b", claude_code_log=str(transcript_paths["b"]), subtype="initial"
        ),
    ]
    _write_jsonl(root / "sessions.jsonl", sessions)
    active = root / "otlp.jsonl"
    active.write_bytes(
        _otlp_line("a-1", _log("api_request", "sid-a", time_ns=_T0_NS, request_id="a-1"))
        + _otlp_line("b-1", _log("api_request", "sid-b", time_ns=_T0_NS, request_id="b-1"))
    )
    update_report_index(root, inc)

    sessions[0] = {**sessions[0], "subtype": "updated"}
    sessions.append(
        _basic_session("c", "sid-c", claude_code_log=str(transcript_paths["c"]), subtype="initial")
    )
    _write_jsonl(root / "sessions.jsonl", sessions)
    with active.open("ab") as handle:
        handle.write(
            _otlp_line("a-2", _log("api_request", "sid-a", time_ns=_T0_NS + 1, request_id="a-2"))
        )
        handle.write(
            _otlp_line("c-1", _log("api_request", "sid-c", time_ns=_T0_NS + 1, request_id="c-1"))
        )
    update_report_index(root, inc)

    archived = sessions.pop(0)
    _write_jsonl(root / "sessions.jsonl", sessions)
    _write_jsonl(root / "sessions-archive.jsonl", [archived])
    (root / "otlp.jsonl.1").write_bytes(active.read_bytes())
    active.write_bytes(
        _otlp_line("b-2", _log("api_request", "sid-b", time_ns=_T0_NS + 2, request_id="b-2"))
    )
    update_report_index(root, inc)

    rebuild_report_index(root, full)
    assert read_report_index(inc) == read_report_index(full)


def test_interrupted_update_resumes_to_the_same_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "logs"
    _write_jsonl(
        root / "sessions.jsonl", [_basic_session("a", "sid-a"), _basic_session("b", "sid-b")]
    )
    (root / "otlp.jsonl").write_bytes(
        _otlp_line("a", _log("api_request", "sid-a", time_ns=_T0_NS, request_id="a"))
        + _otlp_line("b", _log("api_request", "sid-b", time_ns=_T0_NS, request_id="b"))
    )
    index_dir = tmp_path / "interrupted"
    original_walk = report_index.iter_report_walk

    def interrupt_after_three(root_path: Path, watermark: dict[str, Any] | None = None) -> Any:
        # Inject a checkpoint WalkItem just before the failure so the row
        # appender commits the partial state through its normal
        # ``kind == "checkpoint"`` branch, avoiding any monkeypatch of the
        # private ``_COMMIT_BYTES`` knob. The checkpoint's watermark reflects
        # only the items already yielded, not the next item that the original
        # walker would have produced, so resume replays work the same way as
        # the eager ``_COMMIT_BYTES = 0`` path it replaces.
        committed_watermark: dict[str, Any] = dict(watermark or {})
        for count, item in enumerate(original_walk(root_path, watermark)):
            if count == 3:
                yield WalkItem("checkpoint", None, None, None, committed_watermark)
                raise RuntimeError("interrupted report walk")
            yield item
            committed_watermark = dict(item.watermark)

    monkeypatch.setattr(report_index, "iter_report_walk", interrupt_after_three)
    with pytest.raises(RuntimeError, match="interrupted report walk"):
        update_report_index(root, index_dir)
    assert (index_dir / "state.json").is_file()
    with (index_dir / "rows.jsonl").open("ab") as handle:
        handle.write(_json_line({"kind": "session", "key": "uncommitted", "session_id": "ghost"}))
        handle.write(b'{"kind":"session","key":"torn')

    monkeypatch.setattr(report_index, "iter_report_walk", original_walk)
    update_report_index(root, index_dir)
    rebuild_report_index(root, tmp_path / "rebuilt")

    assert read_report_index(index_dir) == read_report_index(tmp_path / "rebuilt")
    assert "uncommitted" not in read_report_index(index_dir).sessions
    assert all(json.loads(line) for line in (index_dir / "rows.jsonl").read_text().splitlines())


def test_malformed_state_recovers_and_truncates_torn_tail(tmp_path: Path) -> None:
    root = tmp_path / "logs"
    _write_jsonl(root / "sessions.jsonl", [_basic_session("a", "sid-a")])
    (root / "otlp.jsonl").write_bytes(
        _otlp_line("a", _log("api_request", "sid-a", time_ns=_T0_NS, request_id="a"))
    )
    index_dir = tmp_path / "index"
    update_report_index(root, index_dir)
    expected = read_report_index(index_dir)
    with (index_dir / "rows.jsonl").open("ab") as handle:
        handle.write(b'{"kind":"request","key":"torn')
    (index_dir / "state.json").write_text("{broken", encoding="utf-8")

    update_report_index(root, index_dir)

    assert read_report_index(index_dir) == expected
    assert all(json.loads(line) for line in (index_dir / "rows.jsonl").read_text().splitlines())


def test_older_version_rows_parse_without_error(tmp_path: Path) -> None:
    rows_path = tmp_path / "index" / "rows.jsonl"
    rows_path.parent.mkdir()
    rows_path.write_bytes(
        b"{malformed}\n"
        + _json_line(["array"])
        + _json_line({"key": "no-kind"})
        + _json_line({"kind": "future_kind", "key": "future"})
        + _json_line({"kind": "session", "key": "old-session", "session_id": "sid-old"})
        + _json_line(
            {
                "schema_version": 1,
                "kind": "request",
                "key": "sid-old:req-old",
                "session_id": "sid-old",
                "harness": "claude-code",
                "request_id": "req-old",
            }
        )
        + _json_line(
            {
                "schema_version": REPORT_INDEX_SCHEMA_VERSION + 1,
                "kind": "session",
                "key": "future-session",
                "session_id": "sid-future",
                "future_field": 1,
            }
        )
    )

    indexed = read_report_index(rows_path.parent)

    old_session = indexed.sessions["old-session"]
    assert old_session["harness"] == old_session["provider"] == "unknown"
    assert old_session["input_tokens"]["state"] == "unknown"
    old_request = indexed.requests["sid-old:req-old"]
    assert old_request["session_key"] == "old-session"
    assert old_request["input_tokens"]["state"] == "unknown"
    assert old_request["cost_usd"] is None
    assert "future_field" not in indexed.sessions["future-session"]
    assert len(indexed.sessions) == 2


def test_lost_state_keeps_rows_whose_sources_rotated_away(tmp_path: Path) -> None:
    root = tmp_path / "logs"
    _write_jsonl(root / "sessions.jsonl", [_basic_session("a", "sid-a")])
    (root / "otlp.jsonl").write_bytes(
        _otlp_line("a", _log("api_request", "sid-a", time_ns=_T0_NS, request_id="a"))
    )
    index_dir = tmp_path / "index"
    update_report_index(root, index_dir)
    sessions_before = read_report_index(index_dir).sessions
    (root / "otlp.jsonl").unlink()
    (index_dir / "state.json").unlink()

    update_report_index(root, index_dir)

    indexed = read_report_index(index_dir)
    assert indexed.sessions == sessions_before
    assert {row["request_id"] for row in indexed.requests.values()} == {"a"}


def test_archive_gap_reindexes_archive_then_projection(tmp_path: Path) -> None:
    root = tmp_path / "logs"
    index_dir = tmp_path / "index"
    archived_v1 = _basic_session("d", "sid-d", subtype="v1")
    projected_v2 = _basic_session("d", "sid-d", subtype="v2")
    _write_jsonl(root / "sessions-archive.jsonl", [archived_v1])
    _write_jsonl(root / "sessions.jsonl", [projected_v2])
    (root / "otlp.jsonl").write_bytes(
        _otlp_line("event", _log("api_request", "sid-d", time_ns=_T0_NS, request_id="req-d"))
    )
    update_report_index(root, index_dir)
    before = (index_dir / "rows.jsonl").read_bytes()
    assert read_report_index(index_dir).sessions["d"]["subtype"] == "v2"

    rewritten_archive = _basic_session("d", "sid-d", subtype="x1")
    assert len(_json_line(rewritten_archive)) == len(_json_line(archived_v1))
    _write_jsonl(root / "sessions-archive.jsonl", [rewritten_archive])
    result = update_report_index(root, index_dir)

    assert result.source_gaps == ("archive",)
    assert read_report_index(index_dir).sessions["d"]["subtype"] == "v2"
    appended = (index_dir / "rows.jsonl").read_bytes()[len(before) :]
    assert {json.loads(line)["kind"] for line in appended.splitlines()} == {"session"}


def test_update_fails_fast_when_another_writer_holds_the_lease(tmp_path: Path) -> None:
    root = tmp_path / "logs"
    index_dir = tmp_path / "index"
    index_dir.mkdir()

    with ArtifactLease.acquire_exclusive(index_dir / "index.lock", timeout=0.0):
        with pytest.raises(ArtifactLeaseContention):
            update_report_index(root, index_dir)
