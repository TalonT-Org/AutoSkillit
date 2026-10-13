"""Tests for sanitized, captured E2E session evidence."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from autoskillit.core import PLUGIN_PREFIX
from tests.infra._complexity_helpers import load_check_script
from tests.infra._e2e_evidence import load_provenance, materialize_run

pytestmark = [pytest.mark.layer("infra"), pytest.mark.medium]

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_NAME = "run_38170521660_attempt_1"
FIXTURE_DIR = Path(__file__).with_name("fixtures") / "e2e_sessions" / FIXTURE_NAME
_SCRIPT = REPO_ROOT / "scripts" / "e2e" / "e2e_sessions.py"
sessions = load_check_script("_autoskillit_e2e_sessions_captured", _SCRIPT)
ROOT_SESSION_ID = "2292658a-34cf-44a0-a0b1-f7a20218874b"
WORKER_SESSION_IDS = (
    "8ef8f993-8caa-47dd-83b0-26f9e5b51139",
    "54f18144-1188-45d7-a392-17b4529f721e",
)
UNMATCHED_AGENT_ID = "a610a4bda1f28ca35"
NATIVE_AGENT_IDS = {
    "a516cbae85151dc7c",
    "a9ed51b7c02b828dd",
    "aeb96cc0ddbdab294",
    "a7e613554b849d6b2",
}

_INPUTS_TO_PRESERVE = {
    PLUGIN_PREFIX + "claim_and_resolve_issue",
    PLUGIN_PREFIX + "create_and_publish_branch",
}
_TRUNCATION = re.compile(r"\.\.\.\[fixture-truncated (\d+) chars\]$")


def _rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _tool_reply(rows: list[dict[str, Any]], suffix: str) -> tuple[dict[str, Any], dict[str, Any]]:
    matches = [
        (row, block)
        for row in rows
        for block in row.get("message", {}).get("content", [])
        if isinstance(block, dict)
        and block.get("type") == "tool_use"
        and isinstance(block.get("name"), str)
        and block["name"].endswith(suffix)
    ]
    assert len(matches) == 1
    use_row, use_block = matches[0]
    reply = next(row for row in rows if row.get("sourceToolAssistantUUID") == use_row["uuid"])
    result = next(
        block
        for block in reply.get("message", {}).get("content", [])
        if isinstance(block, dict)
        and block.get("type") == "tool_result"
        and block.get("tool_use_id") == use_block.get("id")
    )
    return reply, result


def _assert_truncated_prose(value: Any, *, preserve_input: bool = False) -> None:
    if isinstance(value, str):
        stripped = value.lstrip()
        if not preserve_input and stripped.startswith(("{", "[")):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, (dict, list)):
                _assert_truncated_prose(parsed)
                return
        if not preserve_input and len(value) > 400:
            marker = _TRUNCATION.search(value)
            assert marker is not None
            assert marker.start() == 400
            assert int(marker.group(1)) > 0
        return
    if isinstance(value, list):
        for item in value:
            _assert_truncated_prose(item, preserve_input=preserve_input)
        return
    if isinstance(value, dict):
        keep_full_input = (
            value.get("type") == "tool_use" and value.get("name") in _INPUTS_TO_PRESERVE
        )
        for key, item in value.items():
            _assert_truncated_prose(
                item,
                preserve_input=preserve_input or (key == "input" and keep_full_input),
            )


def test_captured_fixture_is_sanitized_and_keeps_the_real_wire_shape() -> None:
    provenance = load_provenance(FIXTURE_NAME)
    for path in FIXTURE_DIR.rglob("*"):
        if path.is_file() and path.name != "PROVENANCE.json":
            assert b"/home/" not in path.read_bytes(), path.relative_to(FIXTURE_DIR)

    envelope = json.loads((FIXTURE_DIR / "out/envelope.json").read_text(encoding="utf-8"))
    root_transcript = (
        FIXTURE_DIR
        / "projects"
        / "-workspace-sandbox"
        / f"{envelope['dispatched_session_id']}.jsonl"
    )
    rows = _rows(root_transcript)
    version_rows = [row["version"] for row in rows if "version" in row]
    assert version_rows
    assert all(version == provenance["cli_version"] for version in version_rows)

    tool_uses = [
        block
        for row in rows
        for block in row.get("message", {}).get("content", [])
        if isinstance(block, dict) and block.get("type") == "tool_use"
    ]
    autoskillit_names = [
        block["name"]
        for block in tool_uses
        if isinstance(block.get("name"), str) and "autoskillit" in block["name"]
    ]
    assert autoskillit_names
    assert all(name.startswith(PLUGIN_PREFIX) for name in autoskillit_names)

    for suffix in ("claim_and_resolve_issue", "create_and_publish_branch"):
        reply, result_block = _tool_reply(rows, suffix)
        structured = reply["mcpMeta"]["structuredContent"]["result"]
        assert isinstance(json.loads(structured), dict)
        with pytest.raises(json.JSONDecodeError):
            json.loads(result_block["content"])

    bootstrap_reply, _ = _tool_reply(rows, "bootstrap_clone")
    bootstrap_result = json.loads(bootstrap_reply["mcpMeta"]["structuredContent"]["result"])
    assert bootstrap_result["merge_target"] == "main"
    _assert_truncated_prose(rows)


def test_captured_run_proves_claim_and_published_branch(tmp_path: Path) -> None:
    provenance = load_provenance(FIXTURE_NAME)
    trace = tmp_path / "trace" / "nested" / "session-events.jsonl"
    log_root = tmp_path / "log-root"
    projects = tmp_path / "projects"
    envelope = materialize_run(
        FIXTURE_NAME,
        trace=trace,
        log_root=log_root,
        projects=projects,
    )

    proof = sessions.collect_ownership(
        trace,
        envelope,
        log_root,
        provenance["issue_url"],
        provenance["repository"],
        claim_label=provenance["claim_label"],
    )
    assert proof["branches"] == ["e2e-implementation-fixture-normalize-whitespace/1-3"]
    assert proof["claimed"] is True
    assert proof["violations"] == []
    assert envelope["l3_payload"] is None


def test_captured_run_accounts_for_successful_and_failed_workers(
    tmp_path: Path,
) -> None:
    provenance = load_provenance(FIXTURE_NAME)
    trace = tmp_path / "trace" / "session-events.jsonl"
    log_root = tmp_path / "log-root"
    envelope = materialize_run(
        FIXTURE_NAME,
        trace=trace,
        log_root=log_root,
        projects=tmp_path / "projects",
    )

    summary = sessions.summarize(trace, envelope, log_root)
    coverage = summary["coverage"]
    assert coverage["managed_session_ids"] == sorted(WORKER_SESSION_IDS)
    assert coverage["owned_session_ids"] == sorted((ROOT_SESSION_ID, *WORKER_SESSION_IDS))
    assert summary["peak_sessions"] == 5
    assert {agent["agent_id"] for agent in coverage["native_agents"]} == NATIVE_AGENT_IDS
    assert coverage["complete"] is False
    assert summary["violations"] == sorted(
        [
            f"lifecycle:agent:{ROOT_SESSION_ID}:{UNMATCHED_AGENT_ID}:orphan_end",
            f"native:{ROOT_SESSION_ID}:{UNMATCHED_AGENT_ID}:lifecycle_incomplete",
            f"native:{ROOT_SESSION_ID}:{UNMATCHED_AGENT_ID}:transcript_unenumerated",
        ]
    )
    assert envelope["l3_payload"] is None

    ownership = sessions.collect_ownership(
        trace,
        envelope,
        log_root,
        provenance["issue_url"],
        provenance["repository"],
        claim_label=provenance["claim_label"],
    )
    assert ownership["branches"] == ["e2e-implementation-fixture-normalize-whitespace/1-3"]
    assert ownership["claimed"] is True
    assert ownership["violations"] == []
    assert [item["session_id"] for item in ownership["transcripts"]] == sorted(
        (ROOT_SESSION_ID, *WORKER_SESSION_IDS)
    )


def test_captured_run_is_complete_once_the_unmatched_stop_is_removed(
    tmp_path: Path,
) -> None:
    trace = tmp_path / "trace" / "session-events.jsonl"
    log_root = tmp_path / "log-root"
    envelope = materialize_run(
        FIXTURE_NAME,
        trace=trace,
        log_root=log_root,
        projects=tmp_path / "projects",
    )
    events = _rows(trace)
    unmatched_stops = [
        event
        for event in events
        if event.get("event") == "SubagentStop" and event.get("agent_id") == UNMATCHED_AGENT_ID
    ]
    assert len(unmatched_stops) == 1
    trace.write_text(
        "".join(
            json.dumps(event, separators=(",", ":"), ensure_ascii=False) + "\n"
            for event in events
            if event not in unmatched_stops
        ),
        encoding="utf-8",
    )

    summary = sessions.summarize(trace, envelope, log_root)
    assert summary["coverage"]["complete"] is True
    assert summary["violations"] == []
    assert summary["peak_sessions"] == 5
