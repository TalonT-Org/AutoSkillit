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


def _update_index(retained_logs: dict[str, Path], **updates: object) -> None:
    row = json.loads(retained_logs["index"].read_text())
    row.update(updates)
    retained_logs["index"].write_text(json.dumps(row) + "\n")


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
@pytest.mark.parametrize(
    ("payload", "max_index_bytes"),
    [
        (b"not-json\n", 100),
        (b"\xff\n", 100),
        (b'{"session_id":"one"}', 100),
        (b"{}\n", 2),
    ],
)
async def test_invalid_session_indexes_are_blocked_at_handler_boundary(
    retained_logs,
    monkeypatch: pytest.MonkeyPatch,
    payload: bytes,
    max_index_bytes: int,
) -> None:
    retained_logs["index"].write_bytes(payload)
    monkeypatch.setattr(session_logs, "_MAX_INDEX_BYTES", max_index_bytes)

    result = json.loads(
        await session_logs.inspect_session_logs(
            operation="index",
            session_ids=["session-one"],
        )
    )

    assert result["status"] == "blocked"
    assert result["reason"] == "index_invalid"


@pytest.mark.asyncio
async def test_codex_transcript_resolves_through_backend_locator(
    retained_logs, monkeypatch
) -> None:
    _update_index(
        retained_logs,
        backend="codex",
        claude_code_log=None,
        codex_log=str(retained_logs["transcript"]),
    )
    locator = SimpleNamespace(locate_session=lambda _session_id: retained_logs["transcript"])
    backend = SimpleNamespace(session_locator=lambda: locator)
    ctx = SimpleNamespace(
        config=SimpleNamespace(
            linux_tracing=SimpleNamespace(log_dir=str(retained_logs["log_root"]))
        ),
        backend=backend,
    )
    monkeypatch.setattr(session_logs, "_get_ctx", lambda: ctx)

    result = json.loads(
        await session_logs.inspect_session_logs(
            operation="read",
            session_id="session-one",
            artifact="transcript",
        )
    )

    assert result["status"] == "answered"
    assert result["content"] == '{"event":"turn.failed"}\n'


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["claude-code", "codex"])
async def test_transcript_locator_mismatch_is_rejected(
    retained_logs, monkeypatch, backend
) -> None:
    wrong_path = retained_logs["transcript"].with_name("different.jsonl")
    if backend == "claude-code":
        monkeypatch.setattr(
            session_logs,
            "claude_code_log_path",
            lambda _cwd, _session_id: wrong_path,
        )
    else:
        _update_index(
            retained_logs,
            backend="codex",
            claude_code_log=None,
            codex_log=str(retained_logs["transcript"]),
        )
        locator = SimpleNamespace(locate_session=lambda _session_id: wrong_path)
        ctx = SimpleNamespace(
            config=SimpleNamespace(
                linux_tracing=SimpleNamespace(log_dir=str(retained_logs["log_root"]))
            ),
            backend=SimpleNamespace(session_locator=lambda: locator),
        )
        monkeypatch.setattr(session_logs, "_get_ctx", lambda: ctx)

    result = json.loads(
        await session_logs.inspect_session_logs(
            operation="read",
            session_id="session-one",
            artifact="transcript",
        )
    )

    assert result["reason"] == "transcript_identity_mismatch"


@pytest.mark.asyncio
@pytest.mark.parametrize("dir_name", ["../outside", "/outside"])
async def test_invalid_index_directory_name_is_rejected(retained_logs, dir_name) -> None:
    _update_index(retained_logs, dir_name=dir_name)

    result = json.loads(
        await session_logs.inspect_session_logs(
            operation="read",
            session_id="session-one",
            artifact="summary",
        )
    )

    assert result["reason"] == "index_invalid"


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
async def test_continuation_is_bound_to_session_and_artifact(retained_logs) -> None:
    first = json.loads(
        await session_logs.inspect_session_logs(
            operation="read",
            session_id="session-one",
            artifact="anomalies",
            byte_limit=len(b'{"kind":"retry"}\n'),
        )
    )
    continuation = first["next_continuation"]

    artifact_substitution = json.loads(
        await session_logs.inspect_session_logs(
            operation="read",
            session_id="session-one",
            artifact="audit",
            continuation=continuation,
        )
    )
    assert artifact_substitution["reason"] == "continuation_invalid"

    row = json.loads(retained_logs["index"].read_text())
    row["session_id"] = "session-two"
    with retained_logs["index"].open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")
    session_substitution = json.loads(
        await session_logs.inspect_session_logs(
            operation="read",
            session_id="session-two",
            artifact="anomalies",
            continuation=continuation,
        )
    )
    assert session_substitution["reason"] == "continuation_invalid"


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
async def test_expired_continuation_is_rejected(retained_logs, monkeypatch) -> None:
    first = json.loads(
        await session_logs.inspect_session_logs(
            operation="read",
            session_id="session-one",
            artifact="anomalies",
            byte_limit=len(b'{"kind":"retry"}\n'),
        )
    )
    monkeypatch.setattr(session_logs, "_CONTINUATION_CLOCK", lambda: 1_301.0)

    result = json.loads(
        await session_logs.inspect_session_logs(
            operation="read",
            session_id="session-one",
            artifact="anomalies",
            continuation=first["next_continuation"],
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
@pytest.mark.parametrize(
    ("byte_limit_delta", "expected_status"),
    [(0, "answered"), (-1, "blocked")],
)
async def test_search_first_match_respects_exact_byte_budget(
    retained_logs,
    byte_limit_delta: int,
    expected_status: str,
) -> None:
    excerpt = '{"kind":"retry"}'
    result = json.loads(
        await session_logs.inspect_session_logs(
            operation="search",
            session_id="session-one",
            artifact="anomalies",
            query="retry",
            byte_limit=len(excerpt.encode()) + byte_limit_delta,
        )
    )

    assert result["status"] == expected_status
    if expected_status == "answered":
        assert result["matches"][0]["excerpt"] == excerpt
        assert result["exact_bytes"] == len(excerpt.encode())
    else:
        assert result["reason"] == "record_too_large"


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
