"""Tests for bounded retained session-log inspection."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from autoskillit.server.tools import tools_session_logs as session_logs

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium]


@pytest.mark.asyncio
async def test_inspection_requires_an_open_kitchen(tool_ctx) -> None:
    result = json.loads(await session_logs.inspect_session_logs(operation="index"))

    assert result["success"] is False
    assert result["subtype"] == "gate_error"
    assert "open_kitchen" in result["result"]


@pytest.fixture
def retained_logs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    log_root = tmp_path / "logs"
    session_dir = log_root / "sessions" / "session-one"
    session_dir.mkdir(parents=True)
    summary = session_dir / "summary.json"
    anomalies = session_dir / "anomalies.jsonl"
    audit = session_dir / "audit_log.json"
    transcript = tmp_path / "transcripts" / "session-one.jsonl"
    transcript.parent.mkdir()
    summary.write_text('{"needs_retry":true,"retry_reason":"early_stop"}\n')
    anomalies.write_text('{"kind":"retry"}\n{"kind":"error"}\n')
    audit.write_text('{"subtype":"missing_completion_marker"}\n')
    transcript.write_text('{"event":"turn.failed"}\n')
    row = {
        "session_id": "session-one",
        "dir_name": "session-one",
        "cwd": str(tmp_path / "project"),
        "backend": "claude-code",
        "claude_code_log": str(transcript),
        "codex_log": None,
        "kitchen_id": "kitchen-one",
        "step_name": "implement",
        "success": False,
        "subtype": "missing_completion_marker",
        "needs_retry": True,
        "retry_reason": "early_stop",
    }
    index = log_root / "sessions.jsonl"
    index.write_text(json.dumps(row) + "\n")
    ctx = SimpleNamespace(
        config=SimpleNamespace(linux_tracing=SimpleNamespace(log_dir=str(log_root))),
        backend=None,
    )
    monkeypatch.setattr(session_logs, "_get_ctx", lambda: ctx)
    monkeypatch.setattr(session_logs, "_require_enabled", lambda: None)
    monkeypatch.setattr(session_logs, "claude_code_log_path", lambda cwd, session_id: transcript)
    monkeypatch.setattr(session_logs, "_CONTINUATION_KEY", b"k" * 32)
    monkeypatch.setattr(session_logs, "_CONTINUATION_CLOCK", lambda: 1_000.0)
    return {
        "log_root": log_root,
        "index": index,
        "summary": summary,
        "anomalies": anomalies,
        "audit": audit,
        "transcript": transcript,
    }


@pytest.mark.asyncio
async def test_index_returns_only_requested_metadata_and_exact_handles(retained_logs) -> None:
    result = json.loads(
        await session_logs.inspect_session_logs(
            operation="index",
            session_ids=["session-one"],
        )
    )

    assert result["status"] == "answered"
    assert result["sessions"] == [
        {
            "session_id": "session-one",
            "dir_name": "session-one",
            "backend": "claude-code",
            "kitchen_id": "kitchen-one",
            "step_name": "implement",
            "handles": ["summary", "anomalies", "audit", "transcript"],
            "retry": {
                "success": False,
                "subtype": "missing_completion_marker",
                "needs_retry": True,
                "retry_reason": "early_stop",
            },
        }
    ]


@pytest.mark.asyncio
async def test_read_pages_have_byte_counts_citations_and_authenticated_continuations(
    retained_logs,
) -> None:
    first = json.loads(
        await session_logs.inspect_session_logs(
            operation="read",
            session_id="session-one",
            artifact="anomalies",
            byte_limit=len(b'{"kind":"retry"}\n'),
        )
    )
    assert first["content"] == '{"kind":"retry"}\n'
    assert first["exact_bytes"] == len(first["content"].encode())
    assert first["line_range"] == {"start": 1, "end": 1}
    assert first["citation"] == "session-one/anomalies:1-1"
    assert first["truncated"] is True
    assert first["next_continuation"]

    second = json.loads(
        await session_logs.inspect_session_logs(
            operation="read",
            session_id="session-one",
            artifact="anomalies",
            continuation=first["next_continuation"],
        )
    )
    assert second["content"] == '{"kind":"error"}\n'
    assert second["line_range"] == {"start": 2, "end": 2}
    assert second["truncated"] is False

    continuation = first["next_continuation"]
    tampered = ("A" if continuation[0] != "A" else "B") + continuation[1:]
    invalid = json.loads(
        await session_logs.inspect_session_logs(
            operation="read",
            session_id="session-one",
            artifact="anomalies",
            continuation=tampered,
        )
    )
    assert invalid["reason"] == "continuation_invalid"


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["issued", "expires", "offset", "line"])
async def test_continuation_rejects_boolean_numeric_fields(retained_logs, field) -> None:
    first = json.loads(
        await session_logs.inspect_session_logs(
            operation="read",
            session_id="session-one",
            artifact="anomalies",
            byte_limit=len(b'{"kind":"retry"}\n'),
        )
    )
    payload = session_logs._decode_continuation(first["next_continuation"])
    payload[field] = True

    result = json.loads(
        await session_logs.inspect_session_logs(
            operation="read",
            session_id="session-one",
            artifact="anomalies",
            continuation=session_logs._encode_continuation(payload),
        )
    )

    assert result["reason"] == "continuation_invalid"


@pytest.mark.asyncio
async def test_search_is_literal_bounded_and_cited(retained_logs) -> None:
    result = json.loads(
        await session_logs.inspect_session_logs(
            operation="search",
            session_id="session-one",
            artifact="anomalies",
            query='"kind":"error"',
        )
    )

    assert result["status"] == "answered"
    assert result["matches"] == [
        {
            "line": 2,
            "citation": "session-one/anomalies:2",
            "excerpt": '{"kind":"error"}',
        }
    ]
    assert result["bytes_scanned"] == retained_logs["anomalies"].stat().st_size


@pytest.mark.asyncio
async def test_append_and_index_rewrite_expire_continuations(retained_logs) -> None:
    first = json.loads(
        await session_logs.inspect_session_logs(
            operation="read",
            session_id="session-one",
            artifact="anomalies",
            byte_limit=len(b'{"kind":"retry"}\n'),
        )
    )
    with retained_logs["anomalies"].open("ab") as handle:
        handle.write(b'{"kind":"later"}\n')
    stale = json.loads(
        await session_logs.inspect_session_logs(
            operation="read",
            session_id="session-one",
            artifact="anomalies",
            continuation=first["next_continuation"],
        )
    )
    assert stale["reason"] == "snapshot_stale"

    retained_logs["index"].write_text("")
    revalidated = json.loads(
        await session_logs.inspect_session_logs(
            operation="read",
            session_id="session-one",
            artifact="anomalies",
            continuation=first["next_continuation"],
        )
    )
    assert revalidated["reason"] == "session_unknown"


@pytest.mark.asyncio
async def test_incomplete_jsonl_suffix_is_disclosed_without_citation(retained_logs) -> None:
    retained_logs["anomalies"].write_bytes(b'{"complete":true}\n{"split":"\xe2\x82')

    result = json.loads(
        await session_logs.inspect_session_logs(
            operation="read",
            session_id="session-one",
            artifact="anomalies",
        )
    )

    assert result["status"] == "partial"
    assert result["reason"] == "incomplete_final_line"
    assert result["content"] == '{"complete":true}\n'
    assert result["line_range"] == {"start": 1, "end": 1}
    assert result["next_continuation"] == ""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"operation": "unknown"}, "operation_invalid"),
        ({"operation": "index", "session_ids": []}, "session_batch_invalid"),
        (
            {
                "operation": "read",
                "session_id": "session-one",
                "artifact": "anomalies",
                "query": "forbidden",
            },
            "arguments_invalid",
        ),
        (
            {"operation": "search", "session_id": "session-one", "artifact": "anomalies"},
            "arguments_invalid",
        ),
    ],
)
async def test_invalid_operation_argument_combinations_are_bounded(
    retained_logs, kwargs, reason
) -> None:
    result = json.loads(await session_logs.inspect_session_logs(**kwargs))
    assert result["status"] == "blocked"
    assert result["reason"] == reason
    assert len(json.dumps(result)) < 500


@pytest.mark.asyncio
async def test_complete_handler_exception_boundary_and_docstring(
    retained_logs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(session_logs, "_load_rows", lambda log_root: 1 / 0)
    result = json.loads(
        await session_logs.inspect_session_logs(operation="index", session_ids=["session-one"])
    )

    assert result["reason"] == "internal_error"
    assert "Never raises." in session_logs.inspect_session_logs.__doc__
