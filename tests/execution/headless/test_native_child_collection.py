"""Tests for collect_native_children_for_backend (issue #4623).

Covers the backend-dispatch wrapper _headless_execute.py calls after
_drain_model_evidence() establishes evidence_session_id — resolving the
parent's own transcript/rollout path via the backend's SessionLocator and
routing to the matching Claude/Codex collector.
"""

from __future__ import annotations

import json

import pytest

from autoskillit.execution import collect_child_outcomes, collect_native_children_for_backend

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


class _FakeLocator:
    def __init__(self, path):
        self._path = path

    def session_log_path(self, cwd, session_id):
        return self._path


class _FakeBackend:
    def __init__(self, *, name: str, transcript_path):
        self.name = name
        self._locator = _FakeLocator(transcript_path)

    def session_locator(self):
        return self._locator


def test_empty_evidence_session_id_is_a_no_op(tmp_path) -> None:
    backend = _FakeBackend(name="claude-code", transcript_path=None)
    log_dir = tmp_path / "logs"
    collect_native_children_for_backend(
        step_backend=backend,
        cwd=str(tmp_path),
        evidence_session_id="",
        diagnostic_log_dir=str(log_dir),
    )
    assert (
        collect_child_outcomes(backend="claude_code", parent_session_id="", log_root=log_dir) == ()
    )


def test_unresolvable_transcript_path_is_a_no_op(tmp_path) -> None:
    backend = _FakeBackend(name="claude-code", transcript_path=None)
    log_dir = tmp_path / "logs"
    collect_native_children_for_backend(
        step_backend=backend,
        cwd=str(tmp_path),
        evidence_session_id="parent-1",
        diagnostic_log_dir=str(log_dir),
    )
    assert (
        collect_child_outcomes(
            backend="claude_code", parent_session_id="parent-1", log_root=log_dir
        )
        == ()
    )


def test_claude_backend_dispatches_to_native_child_collection_with_normalized_backend(
    tmp_path,
) -> None:
    project_dir = tmp_path / "project"
    parent_transcript = project_dir / "parent-1.jsonl"
    parent_transcript.parent.mkdir(parents=True, exist_ok=True)
    parent_transcript.write_text("")
    subagents_dir = project_dir / "parent-1" / "subagents"
    subagents_dir.mkdir(parents=True, exist_ok=True)
    (subagents_dir / "agent-agent-a.jsonl").write_text(
        json.dumps({"type": "assistant", "attributionAgent": "Explore", "message": {"id": "m1"}})
        + "\n"
    )
    log_dir = tmp_path / "logs"
    backend = _FakeBackend(name="claude-code", transcript_path=parent_transcript)

    collect_native_children_for_backend(
        step_backend=backend,
        cwd=str(project_dir),
        evidence_session_id="parent-1",
        diagnostic_log_dir=str(log_dir),
    )

    # Written under "claude_code" (underscore) despite backend.name being
    # "claude-code" (hyphen) — the normalization this test exists to pin.
    outcomes = collect_child_outcomes(
        backend="claude_code", parent_session_id="parent-1", log_root=log_dir
    )
    assert len(outcomes) == 1
    assert outcomes[0]["child_id"] == "agent-a"


def test_codex_backend_dispatches_to_codex_rollout_collection(tmp_path) -> None:
    rollout_path = tmp_path / "rollout.jsonl"
    rollout_path.write_text(
        "\n".join(
            json.dumps(line)
            for line in [
                {"type": "session_meta", "payload": {"id": "parent-1"}},
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "sub_agent_activity",
                        "kind": "started",
                        "agent_thread_id": "child-1",
                    },
                },
            ]
        )
        + "\n"
    )
    log_dir = tmp_path / "logs"
    backend = _FakeBackend(name="codex", transcript_path=rollout_path)

    collect_native_children_for_backend(
        step_backend=backend,
        cwd=str(tmp_path),
        evidence_session_id="parent-1",
        diagnostic_log_dir=str(log_dir),
    )

    outcomes = collect_child_outcomes(
        backend="codex", parent_session_id="parent-1", log_root=log_dir
    )
    assert len(outcomes) == 1
    assert outcomes[0]["child_id"] == "child-1"


def test_collection_failure_never_raises(tmp_path, monkeypatch) -> None:
    """Purely observational: a collector exception must never propagate to the caller."""
    import autoskillit.execution.child_outcomes as module

    def _boom(**kwargs):
        raise RuntimeError("simulated collector failure")

    monkeypatch.setattr(module, "collect_claude_native_children", _boom)
    backend = _FakeBackend(name="claude-code", transcript_path=tmp_path / "parent-1.jsonl")
    (tmp_path / "parent-1.jsonl").write_text("")
    collect_native_children_for_backend(
        step_backend=backend,
        cwd=str(tmp_path),
        evidence_session_id="parent-1",
        diagnostic_log_dir=str(tmp_path / "logs"),
    )
    # No exception raised — the try/except in collect_native_children_for_backend absorbed it.
