"""Tests for the E2E session lifecycle and ownership evidence helper."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from autoskillit.core import DIRECT_PREFIX, PLUGIN_PREFIX
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


def _worker(log_root: Path, session_id: str) -> Path:
    transcript = _session(log_root, session_id)
    index_path = log_root / "sessions.jsonl"
    entries = [json.loads(line) for line in index_path.read_text(encoding="utf-8").splitlines()]
    entries.append(_entry(session_id, transcript))
    _index(log_root, entries)
    return transcript


def _trace_root_and_worker(
    trace: Path,
    log_root: Path,
    worker_id: str,
    worker_transcript: Path,
) -> None:
    root_transcript = log_root / "transcripts" / f"{ROOT_ID}.jsonl"
    _trace(
        trace,
        [
            _event("SessionStart", ROOT_ID, 1, transcript=root_transcript),
            _event("SessionStart", worker_id, 2, transcript=worker_transcript),
            _event("SessionEnd", worker_id, 3, transcript=worker_transcript),
            _event("SessionEnd", ROOT_ID, 4, transcript=root_transcript),
        ],
    )


def _tool_use(
    tool_id: str,
    name: str,
    tool_input: dict[str, Any],
    *,
    prefix: str = PLUGIN_PREFIX,
) -> dict[str, Any]:
    return {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "id": tool_id,
                    "name": f"{prefix}{name}",
                    "input": tool_input,
                }
            ]
        },
    }


def _tool_result(
    tool_id: str,
    result: dict[str, Any],
    *,
    carrier: str = "mcp_meta",
    is_error: bool = False,
) -> dict[str, Any]:
    display = "tool reply\n\nsuccess: True"
    if carrier == "mcp_meta" or carrier == "display_only":
        content = display
    elif carrier == "block_json":
        content = json.dumps(result)
    else:
        raise ValueError(f"unsupported tool result carrier: {carrier}")

    row: dict[str, Any] = {
        "type": "user",
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_id,
                    "content": content,
                    "is_error": is_error,
                }
            ]
        },
    }
    if carrier == "mcp_meta":
        row["mcpMeta"] = {
            "_meta": {"fastmcp": {"wrap_result": True}},
            "structuredContent": {"result": json.dumps(result)},
        }
    return row


def _launch(
    tool_id: str,
    reply: dict[str, Any],
    *,
    tool: str = "run_skill",
    is_error: bool = False,
) -> list[dict[str, Any]]:
    return [
        _tool_use(tool_id, tool, {}),
        _tool_result(tool_id, reply, is_error=is_error),
    ]


def _root_ownership_proof(
    tmp_path: Path,
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    log_root, trace, _ = _base_sessions(tmp_path)
    root_transcript = log_root / "transcripts" / f"{ROOT_ID}.jsonl"
    _jsonl(root_transcript, records)
    _trace(
        trace,
        [
            _event("SessionStart", ROOT_ID, 1, transcript=root_transcript),
            _event("SessionEnd", ROOT_ID, 2, transcript=root_transcript),
        ],
    )
    return sessions.collect_ownership(trace, _envelope(), log_root, ISSUE_URL, REPOSITORY)


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
    assert summary["violations"] == []
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


@pytest.mark.parametrize("scenario", ["child_branch", "unpaired_root_branch", "claim_reentry"])
def test_ownership_isolates_branch_and_claim_scenarios(
    tmp_path: Path,
    scenario: str,
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
        child_id="session-child" if scenario == "child_branch" else None,
        child_records=child_records if scenario == "child_branch" else None,
    )
    root_transcript = log_root / "transcripts" / f"{ROOT_ID}.jsonl"
    if scenario == "claim_reentry":
        root_records = [
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
        ]
    else:
        root_records = [
            _tool_use(
                "root-branch",
                "create_and_publish_branch",
                {"issue_number": "5233", "remote_url": "git@github.com:owner/repo.git"},
            ),
            _tool_result(
                "root-branch" if scenario == "child_branch" else "wrong-branch-id",
                {"merge_target": "impl/5233-root"},
            ),
        ]
    _jsonl(root_transcript, root_records)
    _trace(trace, [_event("SessionStart", ROOT_ID, 1, transcript=root_transcript)])

    proof = sessions.collect_ownership(trace, _envelope(), log_root, ISSUE_URL, REPOSITORY)

    assert proof["branches"] == (["impl/5233-root"] if scenario == "child_branch" else [])
    assert proof["claimed"] is False
    if scenario == "claim_reentry":
        assert proof["violations"] == ["ownership:claim_reentry"]
    elif scenario == "unpaired_root_branch":
        assert proof["violations"] == [
            "ownership:branch_result_failed",
            f"transcript:{ROOT_ID}:unpaired_tool_use:root-branch",
        ]
    else:
        assert proof["violations"] == []


@pytest.mark.parametrize("prefix", [DIRECT_PREFIX, PLUGIN_PREFIX])
def test_ownership_accepts_both_canonical_namespaces(tmp_path: Path, prefix: str) -> None:
    proof = _root_ownership_proof(
        tmp_path,
        [
            _tool_use(
                "claim",
                "claim_and_resolve_issue",
                {"issue_url": ISSUE_URL, "allow_reentry": False},
                prefix=prefix,
            ),
            _tool_result("claim", {"success": True, "claimed": True, "issue_number": 5233}),
            _tool_use(
                "branch",
                "create_and_publish_branch",
                {
                    "issue_number": "5233",
                    "remote_url": "https://github.com/owner/repo.git",
                },
                prefix=prefix,
            ),
            _tool_result("branch", {"merge_target": "impl/5233-fix", "was_unique": True}),
        ],
    )

    assert proof["branches"] == ["impl/5233-fix"]
    assert proof["claimed"] is True
    assert proof["violations"] == []


@pytest.mark.parametrize(
    "name",
    [
        "create_and_publish_branch",
        "mcp__other__create_and_publish_branch",
        f"{PLUGIN_PREFIX}extra__create_and_publish_branch",
    ],
)
def test_tracked_tool_under_another_namespace_is_reported(tmp_path: Path, name: str) -> None:
    proof = _root_ownership_proof(
        tmp_path,
        [
            _tool_use(
                "branch",
                name,
                {
                    "issue_number": "5233",
                    "remote_url": "https://github.com/owner/repo.git",
                },
                prefix="",
            ),
            _tool_result("branch", {"merge_target": "impl/5233-fix"}),
        ],
    )

    assert proof["branches"] == []
    assert f"transcript:{ROOT_ID}:unrecognised_tool_namespace:1" in proof["violations"]


@pytest.mark.parametrize("carrier", ["mcp_meta", "block_json"])
def test_reply_is_read_from_each_supported_carrier(tmp_path: Path, carrier: str) -> None:
    proof = _root_ownership_proof(
        tmp_path,
        [
            _tool_use(
                "branch",
                "create_and_publish_branch",
                {
                    "issue_number": "5233",
                    "remote_url": "https://github.com/owner/repo.git",
                },
            ),
            _tool_result("branch", {"merge_target": "impl/5233-fix"}, carrier=carrier),
        ],
    )

    assert proof["branches"] == ["impl/5233-fix"]
    assert proof["violations"] == []


def test_non_json_display_text_proves_no_reply(tmp_path: Path) -> None:
    result_row = _tool_result(
        "branch",
        {"merge_target": "impl/evil"},
        carrier="display_only",
    )
    result_row["message"]["content"][0]["content"] += "\nmerge_target: impl/evil"
    proof = _root_ownership_proof(
        tmp_path,
        [
            _tool_use(
                "branch",
                "create_and_publish_branch",
                {
                    "issue_number": "5233",
                    "remote_url": "https://github.com/owner/repo.git",
                },
            ),
            result_row,
        ],
    )

    assert proof["branches"] == []
    assert "ownership:branch_result_failed" in proof["violations"]


def test_invalid_row_metadata_does_not_fall_back_to_block_json(tmp_path: Path) -> None:
    result_row = _tool_result(
        "branch",
        {"merge_target": "impl/5233-fix"},
        carrier="block_json",
    )
    result_row["mcpMeta"] = {
        "_meta": {"fastmcp": {"wrap_result": True}},
        "structuredContent": {"result": "not json"},
    }
    proof = _root_ownership_proof(
        tmp_path,
        [
            _tool_use(
                "branch",
                "create_and_publish_branch",
                {
                    "issue_number": "5233",
                    "remote_url": "https://github.com/owner/repo.git",
                },
            ),
            result_row,
        ],
    )

    assert proof["branches"] == []
    assert "ownership:branch_result_failed" in proof["violations"]


def test_row_level_reply_is_ignored_when_a_row_holds_several_results(tmp_path: Path) -> None:
    result = {"merge_target": "impl/5233-fix"}
    proof = _root_ownership_proof(
        tmp_path,
        [
            _tool_use(
                "branch",
                "create_and_publish_branch",
                {
                    "issue_number": "5233",
                    "remote_url": "https://github.com/owner/repo.git",
                },
            ),
            _tool_use("claim", "claim_and_resolve_issue", {"issue_url": "https://example.com"}),
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "branch",
                            "content": "tool reply\n\nsuccess: True",
                        },
                        {
                            "type": "tool_result",
                            "tool_use_id": "claim",
                            "content": "tool reply\n\nsuccess: True",
                        },
                    ]
                },
                "mcpMeta": {
                    "_meta": {"fastmcp": {"wrap_result": True}},
                    "structuredContent": {"result": json.dumps(result)},
                },
            },
        ],
    )

    assert proof["branches"] == []
    assert "ownership:branch_result_failed" in proof["violations"]


def test_untracked_merge_target_reply_is_not_a_branch(tmp_path: Path) -> None:
    proof = _root_ownership_proof(
        tmp_path,
        [
            _tool_use("clone", "bootstrap_clone", {}),
            _tool_result("clone", {"merge_target": "main"}),
        ],
    )

    assert proof["branches"] == []
    assert proof["violations"] == []


@pytest.mark.parametrize(
    ("tool", "success"),
    [
        ("run_skill", True),
        ("complete_run_skill_result", True),
        ("recover_run_skill_result", True),
        ("run_skill", False),
    ],
)
def test_launch_receipt_adopts_a_worker_with_empty_parent_fields(
    tmp_path: Path,
    tool: str,
    success: bool,
) -> None:
    log_root, trace, _ = _base_sessions(tmp_path)
    worker_id = "session-worker"
    worker_transcript = _worker(log_root, worker_id)
    root_transcript = log_root / "transcripts" / f"{ROOT_ID}.jsonl"
    _jsonl(
        root_transcript,
        _launch(
            "launch",
            {"success": success, "receipt_id": "r1", "session_id": worker_id},
            tool=tool,
        ),
    )
    _trace_root_and_worker(trace, log_root, worker_id, worker_transcript)

    summary = sessions.summarize(trace, _envelope(), log_root)

    assert summary["coverage"]["managed_session_ids"] == [worker_id]
    assert summary["coverage"]["complete"] is True


def test_acknowledgement_alone_adopts_a_worker(tmp_path: Path) -> None:
    log_root, trace, _ = _base_sessions(tmp_path)
    worker_id = "session-worker"
    worker_transcript = _worker(log_root, worker_id)
    root_transcript = log_root / "transcripts" / f"{ROOT_ID}.jsonl"
    _jsonl(
        root_transcript,
        [
            *_launch(
                "launch",
                {"success": True, "receipt_id": "r1", "audit_status": "published"},
            ),
            *_launch(
                "ack",
                {"success": True, "receipt_id": "r1", "session_id": worker_id},
                tool="complete_run_skill_result",
            ),
        ],
    )
    _trace_root_and_worker(trace, log_root, worker_id, worker_transcript)

    summary = sessions.summarize(trace, _envelope(), log_root)

    assert summary["coverage"]["managed_session_ids"] == [worker_id]
    assert summary["violations"] == []


@pytest.mark.parametrize("worker_dispatch", [DISPATCH_ID, "another-dispatch"])
def test_overlapping_session_without_receipt_or_parent_is_reported_not_adopted(
    tmp_path: Path,
    worker_dispatch: str,
) -> None:
    log_root, trace, _ = _base_sessions(tmp_path)
    worker_id = "session-worker"
    worker_transcript = _worker(log_root, worker_id)
    index_path = log_root / "sessions.jsonl"
    entries = [json.loads(line) for line in index_path.read_text(encoding="utf-8").splitlines()]
    next(row for row in entries if row["session_id"] == worker_id)["dispatch_id"] = worker_dispatch
    _index(log_root, entries)
    _trace_root_and_worker(trace, log_root, worker_id, worker_transcript)

    summary = sessions.summarize(trace, _envelope(), log_root)
    proof = sessions.collect_ownership(trace, _envelope(), log_root, ISSUE_URL, REPOSITORY)

    violation = f"lineage:{worker_id}:unattributed"
    assert worker_id not in summary["coverage"]["managed_session_ids"]
    assert violation in summary["violations"]
    assert summary["coverage"]["complete"] is False
    assert violation in proof["violations"]


def test_receipt_in_an_unowned_transcript_does_not_adopt(tmp_path: Path) -> None:
    log_root, trace, _ = _base_sessions(tmp_path)
    worker_id = "session-worker"
    worker_transcript = _worker(log_root, worker_id)
    foreign_id = "session-foreign"
    foreign_transcript = _session(
        log_root,
        foreign_id,
        parent_session_id="other-parent",
        records=_launch(
            "launch",
            {"success": True, "receipt_id": "r1", "session_id": worker_id},
        ),
    )
    index_path = log_root / "sessions.jsonl"
    entries = [json.loads(line) for line in index_path.read_text(encoding="utf-8").splitlines()]
    entries.append(
        _entry(
            foreign_id,
            foreign_transcript,
            parent_session_id="other-parent",
            caller_session_id="other-parent",
        )
    )
    _index(log_root, entries)
    _trace_root_and_worker(trace, log_root, worker_id, worker_transcript)

    summary = sessions.summarize(trace, _envelope(), log_root)

    assert worker_id not in summary["coverage"]["managed_session_ids"]
    assert f"lineage:{worker_id}:unattributed" in summary["violations"]


@pytest.mark.parametrize(
    "reply",
    [
        {
            "success": False,
            "is_error": True,
            "error": "permission denied",
            "stage": "preflight",
            "retriable": False,
        },
        {"success": False, "session_id": ""},
        {"success": False, "session_id": "headless-0123456789ab"},
    ],
)
def test_reply_without_receipt_id_proves_no_launch(
    tmp_path: Path,
    reply: dict[str, Any],
) -> None:
    log_root, trace, _ = _base_sessions(tmp_path)
    root_transcript = log_root / "transcripts" / f"{ROOT_ID}.jsonl"
    _jsonl(root_transcript, _launch("launch", reply))
    _trace(
        trace,
        [
            _event("SessionStart", ROOT_ID, 1, transcript=root_transcript),
            _event("SessionEnd", ROOT_ID, 2, transcript=root_transcript),
        ],
    )

    summary = sessions.summarize(trace, _envelope(), log_root)

    assert summary["coverage"]["managed_session_ids"] == []
    assert summary["violations"] == []


def test_launch_reply_marked_is_error_is_not_evidence(tmp_path: Path) -> None:
    log_root, trace, _ = _base_sessions(tmp_path)
    worker_id = "session-worker"
    worker_transcript = _worker(log_root, worker_id)
    root_transcript = log_root / "transcripts" / f"{ROOT_ID}.jsonl"
    _jsonl(
        root_transcript,
        _launch(
            "launch",
            {"success": True, "receipt_id": "r1", "session_id": worker_id},
            is_error=True,
        ),
    )
    _trace_root_and_worker(trace, log_root, worker_id, worker_transcript)

    summary = sessions.summarize(trace, _envelope(), log_root)

    assert worker_id not in summary["coverage"]["managed_session_ids"]
    assert f"lineage:{worker_id}:unattributed" in summary["violations"]


def test_launched_session_absent_from_trace_and_index_is_reported(tmp_path: Path) -> None:
    log_root, trace, _ = _base_sessions(tmp_path)
    root_transcript = log_root / "transcripts" / f"{ROOT_ID}.jsonl"
    _jsonl(
        root_transcript,
        _launch(
            "launch",
            {"success": True, "receipt_id": "r1", "session_id": "session-ghost"},
        ),
    )
    _trace(
        trace,
        [
            _event("SessionStart", ROOT_ID, 1, transcript=root_transcript),
            _event("SessionEnd", ROOT_ID, 2, transcript=root_transcript),
        ],
    )

    summary = sessions.summarize(trace, _envelope(), log_root)

    assert "lineage:session-ghost:launched_session_unobserved" in summary["violations"]


@pytest.mark.parametrize(
    ("index_count", "receipt_names_worker"),
    [(0, False), (0, True), (2, False), (2, True)],
)
def test_candidate_without_exactly_one_index_row_is_reported(
    tmp_path: Path,
    index_count: int,
    receipt_names_worker: bool,
) -> None:
    log_root, trace, _ = _base_sessions(tmp_path)
    worker_id = "session-worker"
    worker_transcript = _worker(log_root, worker_id)
    index_path = log_root / "sessions.jsonl"
    entries = [json.loads(line) for line in index_path.read_text(encoding="utf-8").splitlines()]
    worker_entry = next(row for row in entries if row["session_id"] == worker_id)
    entries = [row for row in entries if row["session_id"] != worker_id]
    if index_count == 2:
        entries.extend([worker_entry, dict(worker_entry)])
    _index(log_root, entries)
    root_transcript = log_root / "transcripts" / f"{ROOT_ID}.jsonl"
    _jsonl(
        root_transcript,
        _launch(
            "launch",
            {"success": True, "receipt_id": "r1", "session_id": worker_id},
        )
        if receipt_names_worker
        else [],
    )
    _trace_root_and_worker(trace, log_root, worker_id, worker_transcript)

    summary = sessions.summarize(trace, _envelope(), log_root)

    assert worker_id not in summary["coverage"]["managed_session_ids"]
    assert f"sessions_index:{worker_id}:missing_or_ambiguous" in summary["violations"]


def test_receipt_named_worker_from_another_dispatch_is_not_adopted(tmp_path: Path) -> None:
    log_root, trace, _ = _base_sessions(tmp_path)
    worker_id = "session-worker"
    worker_transcript = _worker(log_root, worker_id)
    index_path = log_root / "sessions.jsonl"
    entries = [json.loads(line) for line in index_path.read_text(encoding="utf-8").splitlines()]
    next(row for row in entries if row["session_id"] == worker_id)["dispatch_id"] = (
        "another-dispatch"
    )
    _index(log_root, entries)
    root_transcript = log_root / "transcripts" / f"{ROOT_ID}.jsonl"
    _jsonl(
        root_transcript,
        _launch(
            "launch",
            {"success": True, "receipt_id": "r1", "session_id": worker_id},
        ),
    )
    _trace_root_and_worker(trace, log_root, worker_id, worker_transcript)

    summary = sessions.summarize(trace, _envelope(), log_root)

    assert worker_id not in summary["coverage"]["managed_session_ids"]
    assert f"sessions_index:{worker_id}:dispatch_mismatch" in summary["violations"]


def test_receipt_adopted_worker_with_a_contradicting_declared_parent_is_reported(
    tmp_path: Path,
) -> None:
    log_root, trace, _ = _base_sessions(tmp_path)
    worker_id = "session-worker"
    worker_transcript = _worker(log_root, worker_id)
    summary_path = log_root / "sessions" / worker_id / "summary.json"
    worker_summary = json.loads(summary_path.read_text(encoding="utf-8"))
    worker_summary["execution_identity"]["parent_session_id"] = "other-parent"
    summary_path.write_text(json.dumps(worker_summary), encoding="utf-8")
    root_transcript = log_root / "transcripts" / f"{ROOT_ID}.jsonl"
    _jsonl(
        root_transcript,
        _launch(
            "launch",
            {"success": True, "receipt_id": "r1", "session_id": worker_id},
        ),
    )
    _trace_root_and_worker(trace, log_root, worker_id, worker_transcript)

    summary = sessions.summarize(trace, _envelope(), log_root)

    assert summary["coverage"]["managed_session_ids"] == [worker_id]
    assert f"summary:{worker_id}:parent_mismatch" in summary["violations"]


def test_receipt_adopted_worker_without_lifecycle_endpoints_is_incomplete(tmp_path: Path) -> None:
    log_root, trace, _ = _base_sessions(tmp_path)
    worker_id = "session-worker"
    _worker(log_root, worker_id)
    root_transcript = log_root / "transcripts" / f"{ROOT_ID}.jsonl"
    _jsonl(
        root_transcript,
        _launch(
            "launch",
            {"success": True, "receipt_id": "r1", "session_id": worker_id},
        ),
    )
    _trace(
        trace,
        [
            _event("SessionStart", ROOT_ID, 1, transcript=root_transcript),
            _event("SessionEnd", ROOT_ID, 2, transcript=root_transcript),
        ],
    )

    summary = sessions.summarize(trace, _envelope(), log_root)

    assert summary["coverage"]["managed_session_ids"] == [worker_id]
    assert summary["coverage"]["complete"] is False
    assert f"lifecycle:{worker_id}:missing_endpoints" in summary["violations"]
