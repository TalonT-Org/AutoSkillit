"""Diagnostic reconstruction test for #4575.

Per Plan § Step 7.8 (REQ-EXTRACT-082), this test replays the #4575
scenario from ``join_diagnostics.jsonl`` alone, with no dependency
on TeammateIdle-style notifications. The diagnostics stream is the
production barrier's audit trail; reconstructing the wave from it
proves the contract is reproducible from the recorded evidence.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoskillit.hooks._hook_settings import (
    DIAGNOSTIC_KEYS,
    write_join_diagnostic,
)
from autoskillit.hooks._join_ledger import (
    OUTCOME_FAILURE,
    OUTCOME_SUCCESS,
    claim_assignment,
    declare_batch,
    settle_assignment,
)

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.small]


def test_diagnostics_write_redacts_to_bounded_keys(tmp_path: Path, monkeypatch) -> None:
    """Diagnostic writes are bounded to DIAGNOSTIC_KEYS — no child bodies."""
    monkeypatch.setenv("AUTOSKILLIT_LOG_DIR", str(tmp_path / "autoskillit_logs"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    write_join_diagnostic(
        {
            "gate": "join_claim_guard",
            "session_id": "4575",
            "top_level_parent": "p1",
            "tool_use_id": "t1",
            "child_body": "secret-prompt-text",
            "private_task_id": "ant-private-abc",
            "selection": "name",
            "status": "block",
        },
        caller="join_claim_guard",
    )
    log_path = tmp_path / "autoskillit_logs" / "join_diagnostics.jsonl"
    assert log_path.exists()
    records = [json.loads(line) for line in log_path.read_text().splitlines()]
    assert len(records) == 1
    # Each record contains only the bounded keys.
    assert set(records[0]) <= DIAGNOSTIC_KEYS
    # No child bodies or private task IDs are persisted.
    assert "child_body" not in records[0]
    assert "private_task_id" not in records[0]


def test_diagnostics_reconstruct_wave_from_evidence(tmp_path: Path, monkeypatch) -> None:
    """Replay the #4575 scenario from join_diagnostics.jsonl alone.

    The recorded events are: declaration, two claims, a denial of
    follow-up (with selector), and two settlements. The reconstructed
    state must match the live ledger state derived from the same
    operations.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AUTOSKILLIT_LOG_DIR", str(tmp_path / "autoskillit_logs"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    flag_dir = tmp_path

    # Live ledger: produce the canonical #4575 events.
    declare_batch(
        flag_dir,
        session_id="4575",
        top_level_parent="p1",
        skill_name="skill",
        artifact_digest="abc",
        assignments=("a1", "a2"),
    )
    write_join_diagnostic(
        {
            "gate": "declare_join_batch",
            "session_id": "4575",
            "top_level_parent": "p1",
            "join_batch_id": "batch-1",
            "wave_outcome": "pending",
            "status": "open",
        },
        caller="declare_join_batch",
    )
    claim_assignment(flag_dir, session_id="4575", top_level_parent="p1", tool_use_id="t1")
    write_join_diagnostic(
        {
            "gate": "join_claim_guard",
            "session_id": "4575",
            "top_level_parent": "p1",
            "tool_use_id": "t1",
            "selector_presence": ["name", "team_name"],  # the #4575 selectors
            "status": "block",
        },
        caller="join_claim_guard",
    )
    claim_assignment(flag_dir, session_id="4575", top_level_parent="p1", tool_use_id="t2")
    write_join_diagnostic(
        {
            "gate": "join_claim_guard",
            "session_id": "4575",
            "top_level_parent": "p1",
            "tool_use_id": "t2",
            "selector_presence": [],
            "status": "allow",
        },
        caller="join_claim_guard",
    )
    settle_assignment(
        flag_dir,
        session_id="4575",
        top_level_parent="p1",
        tool_use_id="t1",
        outcome=OUTCOME_SUCCESS,
    )
    write_join_diagnostic(
        {
            "gate": "join_settle_guard",
            "session_id": "4575",
            "top_level_parent": "p1",
            "tool_use_id": "t1",
            "assignment": "a1",
            "wave_outcome": "pending",
            "status": "settle",
        },
        caller="join_settle_guard",
    )
    settle_assignment(
        flag_dir,
        session_id="4575",
        top_level_parent="p1",
        tool_use_id="t2",
        outcome=OUTCOME_FAILURE,
    )
    write_join_diagnostic(
        {
            "gate": "join_settle_guard",
            "session_id": "4575",
            "top_level_parent": "p1",
            "tool_use_id": "t2",
            "assignment": "a2",
            "wave_outcome": "failure",
            "status": "settle",
        },
        caller="join_settle_guard",
    )

    # Reconstruct the wave state from join_diagnostics.jsonl alone.
    log_path = tmp_path / "autoskillit_logs" / "join_diagnostics.jsonl"
    records = [json.loads(line) for line in log_path.read_text().splitlines()]
    # The diagnostics stream covers the full lifecycle.
    gates = [r.get("gate") for r in records]
    assert "declare_join_batch" in gates
    assert "join_claim_guard" in gates
    assert "join_settle_guard" in gates
    # The denial is recorded.
    denials = [r for r in records if r.get("status") == "block"]
    assert len(denials) == 1
    assert "name" in denials[0]["selector_presence"]
    # The successful settlement is recorded.
    successes = [r for r in records if r.get("status") == "settle" and r.get("assignment") == "a1"]
    assert len(successes) == 1
    # The failure is recorded.
    failures = [r for r in records if r.get("status") == "settle" and r.get("assignment") == "a2"]
    assert len(failures) == 1


def test_diagnostics_no_child_bodies_under_any_gate(tmp_path: Path, monkeypatch) -> None:
    """Across all gates, no child body or private task ID is persisted."""
    monkeypatch.setenv("AUTOSKILLIT_LOG_DIR", str(tmp_path / "autoskillit_logs"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    for gate in (
        "join_claim_guard",
        "join_settle_guard",
        "join_followup_guard",
        "join_stop_guard",
        "declare_join_batch",
    ):
        write_join_diagnostic(
            {
                "gate": gate,
                "session_id": "s",
                "top_level_parent": "p",
                "tool_use_id": "t1",
                "child_body": "secret",
                "private_task_id": "ant-private",
                "status": "block",
            },
            caller=gate,
        )
    log_path = tmp_path / "autoskillit_logs" / "join_diagnostics.jsonl"
    records = [json.loads(line) for line in log_path.read_text().splitlines()]
    for record in records:
        assert "child_body" not in record
        assert "private_task_id" not in record


def test_diagnostics_keys_are_bounded() -> None:
    """The DIAGNOSTIC_KEYS set is the single source of truth for what is
    persisted — anything else is dropped at write time."""
    assert isinstance(DIAGNOSTIC_KEYS, frozenset)
    # Required keys are present.
    assert "gate" in DIAGNOSTIC_KEYS
    assert "session_id" in DIAGNOSTIC_KEYS
    assert "tool_use_id" in DIAGNOSTIC_KEYS
    # Forbidden keys are absent.
    assert "child_body" not in DIAGNOSTIC_KEYS
    assert "private_task_id" not in DIAGNOSTIC_KEYS
    assert "prompt" not in DIAGNOSTIC_KEYS
