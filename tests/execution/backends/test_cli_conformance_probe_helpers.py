from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tests.execution.backends.test_cli_conformance_probes import (
    _collect_generated_child_rollout,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def test_collect_generated_child_rollout_associates_parent_and_children(tmp_path: Path) -> None:
    session_root = tmp_path / "sessions"
    session_root.mkdir()
    parent_events = [{"type": "session_meta", "payload": {"id": "parent-thread"}}]
    child_events = [
        {
            "type": "session_meta",
            "payload": {"id": "child-thread", "forked_from_id": "parent-thread"},
        },
        {"type": "response_item", "payload": {"type": "message"}},
    ]
    unrelated_events = [{"type": "session_meta", "payload": {"id": "unrelated-thread"}}]
    for name, events in (
        ("parent", parent_events),
        ("child", child_events),
        ("unrelated", unrelated_events),
    ):
        (session_root / f"rollout-{name}.jsonl").write_text(
            "".join(f"{json.dumps(event)}\n" for event in events),
            encoding="utf-8",
        )
    result = subprocess.CompletedProcess(
        args=("codex",),
        returncode=0,
        stdout='not-json\n{"type":"thread.started","thread_id":"parent-thread"}\n',
        stderr="",
    )

    rollout = _collect_generated_child_rollout(result, session_home=tmp_path)

    assert rollout.parent_events == parent_events
    assert rollout.child_events == child_events
    assert rollout.parent_id == "parent-thread"
    assert rollout.session_ids == {"parent-thread", "child-thread", "unrelated-thread"}
