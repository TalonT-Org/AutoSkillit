"""Codex session identity propagation across hook, storage, and index boundaries."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from autoskillit.core import (
    NamedResume,
    claim_launch_for_session,
    read_registry,
    release_session_claim,
    write_registry_entry,
)
from autoskillit.execution.backends._codex_session_storage import CodexSessionStore
from autoskillit.hooks._runtime._session_registry_bridge import bridge_session_registry

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]


_LAUNCH_ID = "0123456789abcdef"
# Fixed non-live UUID. The lifecycle contract is that this one identity reaches
# the hook payload, resumable rollout, and derived index unchanged.
_SANITIZED_THREAD_ID = "7c1b6dc2-02c2-47b8-a3af-77b399278c3b"


def _generated_home(tmp_path: Path) -> Path:
    home = tmp_path / "generated-home"
    home.mkdir()
    for public_name in ("sessions", "archived_sessions"):
        inert_target = home / f".inert-{public_name}"
        inert_target.mkdir()
        (home / public_name).symlink_to(inert_target)
    return home


def _write_rollout(path: Path, thread_id: str, project_dir: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            (
                json.dumps({"type": "thread.started", "thread_id": thread_id}),
                json.dumps(
                    {
                        "type": "session_meta",
                        "payload": {"id": thread_id, "cwd": str(project_dir)},
                    }
                ),
                json.dumps({"type": "turn.completed"}),
            )
        )
        + "\n",
        encoding="utf-8",
    )


def test_resumed_codex_identity_stays_aligned_across_hook_storage_and_index(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    write_registry_entry(project_dir, _LAUNCH_ID, "cook", None)

    monkeypatch.setenv("AUTOSKILLIT_LAUNCH_ID", _LAUNCH_ID)
    bridge_session_registry(
        _SANITIZED_THREAD_ID,
        str(project_dir),
        launch_id=_LAUNCH_ID,
    )
    launch_id = claim_launch_for_session(
        project_dir,
        claude_session_id=_SANITIZED_THREAD_ID,
        session_type="cook",
        recipe_name=None,
    )
    assert launch_id == _LAUNCH_ID

    store = CodexSessionStore(log_dir=tmp_path / "codex-log")
    rollout = store.active_root / "2026" / "09" / f"rollout-{_SANITIZED_THREAD_ID}.jsonl"
    _write_rollout(rollout, _SANITIZED_THREAD_ID, project_dir)
    home = _generated_home(tmp_path)

    try:
        with store.prepare_attempt(
            session_home=home,
            project_dir=project_dir,
            launch_id=launch_id,
            attempt=1,
            current_resume_spec=NamedResume(_SANITIZED_THREAD_ID),
        ) as attempt:
            staged = home / "sessions" / "2026" / "09" / rollout.name
            assert (staged.stat().st_dev, staged.stat().st_ino) == (
                rollout.stat().st_dev,
                rollout.stat().st_ino,
            )
            attempt.record_spawn(os.getpid(), os.getpgrp())
            attempt.record_reaped(os.getpid(), os.getpgrp())
    finally:
        release_session_claim(project_dir, launch_id)

    assert read_registry(project_dir)[_LAUNCH_ID]["claude_session_id"] == _SANITIZED_THREAD_ID
    assert store.locate_session(_SANITIZED_THREAD_ID) == rollout
    summaries = store.read_index(str(project_dir))
    assert [(summary.session_id, summary.launch_id) for summary in summaries] == [
        (_SANITIZED_THREAD_ID, _LAUNCH_ID)
    ]
    index = json.loads(store.index_path.read_text(encoding="utf-8"))
    assert index[0]["session_id"] == _SANITIZED_THREAD_ID
    assert index[0]["launch_id"] == _LAUNCH_ID
    assert index[0]["relative_path"] == f"2026/09/{rollout.name}"
