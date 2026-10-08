"""Tests for the E2E session lifecycle and ownership evidence helper."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from tests.infra._complexity_helpers import load_check_script

pytestmark = [pytest.mark.layer("infra"), pytest.mark.medium]

REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = REPO_ROOT / "scripts" / "e2e" / "e2e_sessions.py"
sessions = load_check_script("_autoskillit_e2e_sessions", _SCRIPT)

DISPATCH_ID = "dispatch-5233"
ROOT_ID = "session-root"
ISSUE_URL = "https://github.com/owner/repo/issues/5233"
REPOSITORY = "owner/repo"


def _jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )


def _session(
    log_root: Path,
    session_id: str,
    *,
    parent_session_id: str = "",
    caller_session_id: str = "",
    records: list[dict[str, Any]] | None = None,
) -> Path:
    transcript = log_root / "transcripts" / f"{session_id}.jsonl"
    _jsonl(transcript, records or [])
    session_dir = log_root / "sessions" / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "summary.json").write_text(
        json.dumps(
            {
                "session_id": session_id,
                "dispatch_id": DISPATCH_ID,
                "execution_identity": {"parent_session_id": parent_session_id},
            }
        ),
        encoding="utf-8",
    )
    return transcript


def _index(log_root: Path, entries: list[dict[str, Any]]) -> None:
    _jsonl(log_root / "sessions.jsonl", entries)


def _entry(
    session_id: str,
    transcript: Path,
    *,
    parent_session_id: str = "",
    caller_session_id: str = "",
) -> dict[str, Any]:
    return {
        "session_id": session_id,
        "dir_name": session_id,
        "dispatch_id": DISPATCH_ID,
        "parent_session_id": parent_session_id,
        "caller_session_id": caller_session_id,
        "claude_code_log": str(transcript),
    }


def _event(
    name: str,
    session_id: str,
    timestamp: float,
    *,
    transcript: Path | None = None,
    agent_id: str | None = None,
    agent_transcript: Path | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "event": name,
        "timestamp": timestamp,
        "session_id": session_id,
    }
    if transcript is not None:
        row["transcript_path"] = str(transcript)
    if agent_id is not None:
        row["agent_id"] = agent_id
    if agent_transcript is not None:
        row["agent_transcript_path"] = str(agent_transcript)
    return row


def _trace(path: Path, events: list[dict[str, Any]]) -> None:
    _jsonl(path, events)


def _envelope(session_id: str = ROOT_ID) -> dict[str, str]:
    return {"dispatched_session_id": session_id, "dispatch_id": DISPATCH_ID}


def _base_sessions(
    tmp_path: Path,
    *,
    child_id: str | None = None,
    child_records: list[dict[str, Any]] | None = None,
) -> tuple[Path, Path, Path | None]:
    log_root = tmp_path / "logs"
    root_transcript = _session(log_root, ROOT_ID)
    entries = [_entry(ROOT_ID, root_transcript)]
    child_transcript = None
    if child_id is not None:
        child_transcript = _session(
            log_root,
            child_id,
            parent_session_id=ROOT_ID,
            records=child_records,
        )
        entries.append(
            _entry(
                child_id,
                child_transcript,
                parent_session_id=ROOT_ID,
                caller_session_id=ROOT_ID,
            )
        )
    _index(log_root, entries)
    trace = tmp_path / "run" / "session-lifecycle.jsonl"
    return log_root, trace, child_transcript


def _tool_use(tool_id: str, name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "id": tool_id,
                    "name": f"mcp__autoskillit__{name}",
                    "input": tool_input,
                }
            ]
        },
    }


def _tool_result(tool_id: str, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "user",
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_id,
                    "content": json.dumps(result),
                }
            ]
        },
    }


def test_hook_capture_appends_concurrent_allow_listed_records(tmp_path: Path) -> None:
    trace = tmp_path / "events.jsonl"
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(
                lambda index: sessions.capture_hook_event(
                    trace,
                    {
                        "hook_event_name": "SessionStart",
                        "session_id": f"session-{index}",
                        "source": "startup",
                        "transcript_path": f"/tmp/{index}.jsonl",
                        "prompt": "must not be persisted",
                        "environment": {"TOKEN": "must not be persisted"},
                    },
                ),
                range(32),
            )
        )

    records = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
    assert all(results)
    assert len(records) == 32
    assert {record["session_id"] for record in records} == {
        f"session-{index}" for index in range(32)
    }
    assert all(isinstance(record["timestamp"], (int, float)) for record in records)
    assert all(
        set(record) <= {"event", "timestamp", "session_id", "source", "transcript_path"}
        for record in records
    )
    assert (
        sessions.capture_hook_event(trace, {"event": "OtherHook", "session_id": "ignored"})
        is False
    )
    assert len(trace.read_text(encoding="utf-8").splitlines()) == 32


def test_summary_collapses_repeated_events_excludes_unrelated_and_sweeps_half_open(
    tmp_path: Path,
) -> None:
    log_root, trace, child_transcript = _base_sessions(tmp_path, child_id="session-child")
    assert child_transcript is not None
    unrelated = _session(log_root, "session-unrelated", parent_session_id="other-parent")
    rows = json.loads((log_root / "sessions.jsonl").read_text(encoding="utf-8").splitlines()[0])
    entries = [
        rows,
        _entry(
            "session-child", child_transcript, parent_session_id=ROOT_ID, caller_session_id=ROOT_ID
        ),
    ]
    entries.append(
        _entry(
            "session-unrelated",
            unrelated,
            parent_session_id="other-parent",
            caller_session_id="other-parent",
        )
    )
    _index(log_root, entries)
    _trace(
        trace,
        [
            _event(
                "SessionStart",
                ROOT_ID,
                1,
                transcript=log_root / "transcripts" / f"{ROOT_ID}.jsonl",
            ),
            _event(
                "SessionStart",
                ROOT_ID,
                1.5,
                transcript=log_root / "transcripts" / f"{ROOT_ID}.jsonl",
            ),
            _event("SessionStart", "session-child", 3, transcript=child_transcript),
            _event("SessionStart", "session-unrelated", 1, transcript=unrelated),
            _event(
                "SessionEnd", ROOT_ID, 2, transcript=log_root / "transcripts" / f"{ROOT_ID}.jsonl"
            ),
            _event(
                "SessionEnd", ROOT_ID, 3, transcript=log_root / "transcripts" / f"{ROOT_ID}.jsonl"
            ),
            _event("SessionEnd", "session-child", 4, transcript=child_transcript),
            _event("SessionEnd", "session-unrelated", 5, transcript=unrelated),
        ],
    )

    summary = sessions.summarize(trace, _envelope(), log_root)

    assert summary["peak_sessions"] == 1
    assert summary["coverage"]["complete"] is True
    assert summary["coverage"]["managed_session_ids"] == ["session-child"]
    assert [identity["session_id"] for identity in summary["identities"]] == [
        ROOT_ID,
        "session-child",
    ]
    root_interval = summary["identities"][0]
    assert (root_interval["start"], root_interval["end"]) == (1.0, 3.0)
    assert (trace.parent / "session-concurrency.json").is_file()


@pytest.mark.parametrize(
    ("child_event", "expected_violation"),
    [("SessionStart", "missing_end"), ("SessionEnd", "orphan_end")],
)
def test_summary_does_not_infer_child_end(
    tmp_path: Path, child_event: str, expected_violation: str
) -> None:
    log_root, trace, child_transcript = _base_sessions(tmp_path, child_id="session-child")
    assert child_transcript is not None
    _trace(
        trace,
        [
            _event(
                "SessionStart",
                ROOT_ID,
                1,
                transcript=log_root / "transcripts" / f"{ROOT_ID}.jsonl",
            ),
            _event(child_event, "session-child", 2, transcript=child_transcript),
            _event(
                "SessionEnd", ROOT_ID, 5, transcript=log_root / "transcripts" / f"{ROOT_ID}.jsonl"
            ),
        ],
    )

    summary = sessions.summarize(trace, _envelope(), log_root)

    assert summary["peak_sessions"] == 1
    assert summary["coverage"]["complete"] is False
    assert f"lifecycle:session:session-child:{expected_violation}" in summary["violations"]


def test_summary_enumerates_native_agent_transcripts_and_requires_endpoints(
    tmp_path: Path,
) -> None:
    log_root, trace, _child = _base_sessions(tmp_path)
    root_transcript = log_root / "transcripts" / f"{ROOT_ID}.jsonl"
    agent_transcript = log_root / "transcripts" / ROOT_ID / "subagents" / "agent-agent-a.jsonl"
    agent_transcript.parent.mkdir(parents=True)
    agent_transcript.write_text("{}\n", encoding="utf-8")
    _trace(
        trace,
        [
            _event("SessionStart", ROOT_ID, 1, transcript=root_transcript),
            _event("SubagentStart", ROOT_ID, 2, agent_id="agent-a"),
            _event(
                "SubagentStop", ROOT_ID, 4, agent_id="agent-a", agent_transcript=agent_transcript
            ),
            _event("SessionEnd", ROOT_ID, 5, transcript=root_transcript),
        ],
    )

    summary = sessions.summarize(trace, _envelope(), log_root)

    assert summary["coverage"]["complete"] is True
    assert summary["peak_sessions"] == 2
    assert summary["coverage"]["native_agents"] == [
        {
            "session_id": ROOT_ID,
            "agent_id": "agent-a",
            "transcript_path": str(agent_transcript.resolve()),
        }
    ]


def test_indexed_owned_child_without_hooks_is_incomplete(tmp_path: Path) -> None:
    log_root, trace, _ = _base_sessions(tmp_path, child_id="missing-hooks")
    transcript = log_root / "transcripts" / f"{ROOT_ID}.jsonl"
    _trace(
        trace,
        [
            _event("SessionStart", ROOT_ID, 1, transcript=transcript),
            _event("SessionEnd", ROOT_ID, 5, transcript=transcript),
        ],
    )
    summary = sessions.summarize(trace, _envelope(), log_root)
    assert not summary["coverage"]["complete"]
    assert "lifecycle:missing-hooks:missing_endpoints" in summary["violations"]


def test_resumed_identity_requires_a_final_end(tmp_path: Path) -> None:
    log_root, trace, _ = _base_sessions(tmp_path)
    transcript = log_root / "transcripts" / f"{ROOT_ID}.jsonl"
    _trace(
        trace,
        [
            _event("SessionStart", ROOT_ID, 1, transcript=transcript),
            _event("SessionEnd", ROOT_ID, 2, transcript=transcript),
            _event("SessionStart", ROOT_ID, 3, transcript=transcript),
        ],
    )
    summary = sessions.summarize(trace, _envelope(), log_root)
    assert not summary["coverage"]["complete"]
    assert f"lifecycle:session:{ROOT_ID}:missing_final_end" in summary["violations"]


def test_empty_half_open_interval_does_not_reduce_concurrent_peak() -> None:
    assert (
        sessions._peak_sessions(
            [{"start": 1, "end": 3}, {"start": 2, "end": 2}, {"start": 2, "end": 4}]
        )
        == 2
    )


def test_no_attempted_resource_calls_leave_cleanup_proof_empty(tmp_path: Path) -> None:
    log_root, trace, _ = _base_sessions(tmp_path)
    transcript = log_root / "transcripts" / f"{ROOT_ID}.jsonl"
    _trace(
        trace,
        [
            _event("SessionStart", ROOT_ID, 1, transcript=transcript),
            _event("SessionEnd", ROOT_ID, 2, transcript=transcript),
        ],
    )
    proof = sessions.collect_ownership(trace, _envelope(), log_root, ISSUE_URL, REPOSITORY)
    assert proof["branches"] == []
    assert proof["claimed"] is False
    assert proof["violations"] == []


def test_missing_root_hooks_do_not_discard_available_branch_proof(tmp_path: Path) -> None:
    log_root, trace, _ = _base_sessions(tmp_path)
    transcript = log_root / "transcripts" / f"{ROOT_ID}.jsonl"
    _jsonl(
        transcript,
        [
            _tool_use(
                "branch",
                "create_and_publish_branch",
                {"issue_number": "5233", "remote_url": "https://github.com/owner/repo.git"},
            ),
            _tool_result("branch", {"merge_target": "impl/5233-fix", "was_unique": True}),
        ],
    )
    _trace(trace, [])
    proof = sessions.collect_ownership(trace, _envelope(), log_root, ISSUE_URL, REPOSITORY)
    assert proof["branches"] == ["impl/5233-fix"]
    assert "trace:root_session_missing" in proof["violations"]


def test_missing_root_and_malformed_trace_are_incomplete(tmp_path: Path) -> None:
    log_root = tmp_path / "logs"
    trace = tmp_path / "run" / "session-lifecycle.jsonl"
    trace.parent.mkdir(parents=True)
    trace.write_text("{malformed\n", encoding="utf-8")

    summary = sessions.summarize(trace, None, log_root)

    assert summary["coverage"]["complete"] is False
    assert "envelope:missing" in summary["violations"]
    assert "sessions_index:unavailable" in summary["violations"]
    assert "trace:invalid_json:1" in summary["violations"]


@pytest.mark.parametrize("dir_name", [".", ".."])
def test_summary_rejects_dot_session_directories(tmp_path: Path, dir_name: str) -> None:
    log_root, trace, _ = _base_sessions(tmp_path)
    root_transcript = log_root / "transcripts" / f"{ROOT_ID}.jsonl"
    entry = _entry(ROOT_ID, root_transcript)
    entry["dir_name"] = dir_name
    _index(log_root, [entry])
    _trace(trace, [_event("SessionStart", ROOT_ID, 1, transcript=root_transcript)])

    summary = sessions.summarize(trace, _envelope(), log_root)

    assert summary["coverage"]["complete"] is False
    assert f"sessions_index:{ROOT_ID}:invalid_dir_name" in summary["violations"]


def test_summary_rejects_malformed_owned_index_summary_and_transcript(
    tmp_path: Path,
) -> None:
    log_root, trace, _child = _base_sessions(tmp_path)
    root_transcript = log_root / "transcripts" / f"{ROOT_ID}.jsonl"
    index_path = log_root / "sessions.jsonl"
    index_path.write_text(
        index_path.read_text(encoding="utf-8") + "{malformed\n", encoding="utf-8"
    )
    (log_root / "sessions" / ROOT_ID / "summary.json").write_text("{malformed\n", encoding="utf-8")
    root_transcript.write_text("{malformed\n", encoding="utf-8")
    _trace(
        trace,
        [
            _event("SessionStart", ROOT_ID, 1, transcript=root_transcript),
            _event("SessionEnd", ROOT_ID, 2, transcript=root_transcript),
        ],
    )

    summary = sessions.summarize(trace, _envelope(), log_root)

    assert summary["coverage"]["complete"] is False
    assert "sessions_index:invalid_json:2" in summary["violations"]
    assert f"summary:{ROOT_ID}:missing_or_malformed" in summary["violations"]
    assert f"transcript:{ROOT_ID}:invalid_json:1" in summary["violations"]


@pytest.mark.parametrize(
    ("claim_input_label", "expected_claimed"),
    [(None, True), ("in-progress", True), ("different-label", False)],
)
def test_ownership_pairs_root_branch_with_managed_claim_and_keeps_compact_proof(
    tmp_path: Path,
    claim_input_label: str | None,
    expected_claimed: bool,
) -> None:
    claim_input: dict[str, Any] = {"issue_url": ISSUE_URL, "allow_reentry": False}
    if claim_input_label is not None:
        claim_input["label"] = claim_input_label
    claim_records = [
        _tool_use("claim-1", "claim_and_resolve_issue", claim_input),
        _tool_result(
            "claim-1",
            {
                "success": True,
                "claimed": True,
                "issue_number": 5233,
                "issue_title": "private title",
            },
        ),
    ]
    log_root, trace, child_transcript = _base_sessions(
        tmp_path,
        child_id="session-child",
        child_records=claim_records,
    )
    assert child_transcript is not None
    root_transcript = log_root / "transcripts" / f"{ROOT_ID}.jsonl"
    _jsonl(
        root_transcript,
        [
            _tool_use(
                "branch-1",
                "create_and_publish_branch",
                {
                    "issue_number": "5233",
                    "remote_url": "https://github.com/owner/repo.git",
                },
            ),
            _tool_result("branch-1", {"merge_target": "impl/5233-fix", "was_unique": True}),
        ],
    )
    _trace(
        trace,
        [
            _event("SessionStart", ROOT_ID, 1, transcript=root_transcript),
            _event("SessionStart", "session-child", 2, transcript=child_transcript),
            _event("SessionEnd", "session-child", 4, transcript=child_transcript),
            _event("SessionEnd", ROOT_ID, 5, transcript=root_transcript),
        ],
    )

    proof = sessions.collect_ownership(
        trace, _envelope(), log_root, ISSUE_URL, REPOSITORY, claim_label="in-progress"
    )

    assert proof["branches"] == ["impl/5233-fix"]
    assert proof["claimed"] is expected_claimed
    if expected_claimed:
        assert proof["violations"] == []
    else:
        assert "ownership:claim_result_failed" in proof["violations"]
    assert {item["session_id"] for item in proof["transcripts"]} == {ROOT_ID, "session-child"}


def test_ownership_ignores_unhashable_tool_names(tmp_path: Path) -> None:
    log_root, trace, _ = _base_sessions(tmp_path)
    root_transcript = log_root / "transcripts" / f"{ROOT_ID}.jsonl"
    record = _tool_use("unrelated", "ignored", {})
    record["message"]["content"][0]["name"] = []
    _jsonl(root_transcript, [record])
    _trace(trace, [_event("SessionStart", ROOT_ID, 1, transcript=root_transcript)])

    proof = sessions.collect_ownership(trace, _envelope(), log_root, ISSUE_URL, REPOSITORY)

    assert proof["branches"] == []
    assert proof["claimed"] is False
    assert proof["violations"] == []


def test_ownership_ignores_child_branch_and_rejects_reentry_and_unpaired_results(
    tmp_path: Path,
) -> None:
    child_records = [
        _tool_use(
            "child-branch",
            "create_and_publish_branch",
            {"issue_number": "5233", "remote_url": "git@github.com:owner/repo.git"},
        ),
        _tool_result("child-branch", {"merge_target": "wrong-owner/branch"}),
    ]
    log_root, trace, child_transcript = _base_sessions(
        tmp_path,
        child_id="session-child",
        child_records=child_records,
    )
    assert child_transcript is not None
    root_transcript = log_root / "transcripts" / f"{ROOT_ID}.jsonl"
    _jsonl(
        root_transcript,
        [
            _tool_use(
                "root-branch",
                "create_and_publish_branch",
                {"issue_number": "5233", "remote_url": "git@github.com:owner/repo.git"},
            ),
            _tool_use(
                "claim-1",
                "claim_and_resolve_issue",
                {"issue_url": ISSUE_URL, "allow_reentry": False},
            ),
            _tool_result("claim-1", {"success": True, "claimed": True, "issue_number": 5233}),
            _tool_use(
                "claim-2",
                "claim_and_resolve_issue",
                {"issue_url": ISSUE_URL, "allow_reentry": False},
            ),
            _tool_result("claim-2", {"success": True, "claimed": True, "issue_number": 5233}),
            _tool_result("wrong-branch-id", {"merge_target": "must-not-be-paired"}),
        ],
    )
    _trace(
        trace,
        [
            _event("SessionStart", ROOT_ID, 1, transcript=root_transcript),
            _event("SessionStart", "session-child", 2, transcript=child_transcript),
            _event("SessionEnd", "session-child", 4, transcript=child_transcript),
            _event("SessionEnd", ROOT_ID, 5, transcript=root_transcript),
        ],
    )

    proof = sessions.collect_ownership(trace, _envelope(), log_root, ISSUE_URL, REPOSITORY)

    assert proof["branches"] == []
    assert proof["claimed"] is False
    assert "ownership:claim_reentry" in proof["violations"]
    assert any("unpaired_tool_use:root-branch" in item for item in proof["violations"])
