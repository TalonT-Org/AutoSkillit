"""Durable Codex rollout view, promotion, and recovery contracts."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from autoskillit.execution.backends._codex_session_storage import (
    CodexInteractiveSessionLease,
    CodexSessionStore,
)

from autoskillit.core import NamedResume, NoResume

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]


def _generated_home(tmp_path: Path) -> tuple[Path, dict[str, Path]]:
    home = tmp_path / "generated-home"
    home.mkdir()
    inert_targets: dict[str, Path] = {}
    for name in ("sessions", "archived_sessions"):
        target = home / f".inert-{name}"
        target.mkdir()
        (home / name).symlink_to(target)
        inert_targets[name] = target.resolve()
    return home, inert_targets


def _rollout(path: Path, thread_id: str) -> bytes:
    content = (
        f'{{"type":"thread.started","thread_id":"{thread_id}"}}\n{{"type":"turn.completed"}}\n'
    ).encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return content


def test_fresh_attempt_exposes_empty_view_and_no_child_abort_restores_inert_links(
    tmp_path: Path,
) -> None:
    log_dir = tmp_path / "log-root"
    index_path = log_dir / "codex-session-index.json"
    store = CodexSessionStore(log_dir=log_dir, index_path=index_path)
    home, inert_targets = _generated_home(tmp_path)
    lease = store.prepare_attempt(
        session_home=home,
        launch_id="0123456789abcdef",
        attempt=1,
        current_resume_spec=NoResume(),
    )

    assert isinstance(lease, CodexInteractiveSessionLease)
    with lease as handle:
        assert handle.view_id
        assert (home / "sessions").resolve().parent.name == handle.view_id
        assert (home / "archived_sessions").resolve().parent.name == handle.view_id
        assert list((home / "sessions").resolve().iterdir()) == []
        assert list((home / "archived_sessions").resolve().iterdir()) == []

    assert (home / "sessions").resolve() == inert_targets["sessions"]
    assert (home / "archived_sessions").resolve() == inert_targets["archived_sessions"]
    assert list((log_dir / "codex-active-sessions").glob("*")) == []
    assert list((log_dir / "codex-sessions").rglob("*.jsonl")) == []
    assert not index_path.exists()


def test_fresh_rollout_is_promoted_durably_and_indexed_once(tmp_path: Path) -> None:
    log_dir = tmp_path / "log-root"
    index_path = log_dir / "codex-session-index.json"
    store = CodexSessionStore(log_dir=log_dir, index_path=index_path)
    home, inert_targets = _generated_home(tmp_path)
    expected: bytes

    with store.prepare_attempt(
        session_home=home,
        launch_id="0123456789abcdef",
        attempt=1,
        current_resume_spec=NoResume(),
    ) as handle:
        expected = _rollout(
            (home / "sessions").resolve() / "2026" / "07" / "rollout-new.jsonl",
            "thread-new",
        )
        handle.record_spawn(os.getpid(), os.getpgrp())
        handle.record_reaped(os.getpid(), os.getpgrp())

    promoted = log_dir / "codex-sessions" / "2026" / "07" / "rollout-new.jsonl"
    assert promoted.read_bytes() == expected
    assert (home / "sessions").resolve() == inert_targets["sessions"]
    assert (home / "archived_sessions").resolve() == inert_targets["archived_sessions"]
    rows = json.loads(index_path.read_text(encoding="utf-8"))
    matching = [row for row in rows if row["session_id"] == "thread-new"]
    assert len(matching) == 1
    assert matching[0]["backend_name"] == "codex"
    assert matching[0]["canonical_store"] == "active"
    assert matching[0]["relative_path"] == "2026/07/rollout-new.jsonl"


@pytest.mark.parametrize(
    ("store_name", "public_name"),
    [
        pytest.param("codex-sessions", "sessions", id="active"),
        pytest.param("codex-archived-sessions", "archived_sessions", id="archived"),
    ],
)
def test_named_resume_hard_links_only_selected_rollout_into_matching_view(
    tmp_path: Path, store_name: str, public_name: str
) -> None:
    log_dir = tmp_path / "log-root"
    store = CodexSessionStore(log_dir=log_dir)
    canonical = log_dir / store_name / "2026" / "07" / "rollout-resume.jsonl"
    _rollout(canonical, "thread-resume")
    unrelated = log_dir / store_name / "2026" / "07" / "rollout-unrelated.jsonl"
    _rollout(unrelated, "thread-unrelated")
    home, inert_targets = _generated_home(tmp_path)
    canonical_identity = (canonical.stat().st_dev, canonical.stat().st_ino)

    with store.prepare_attempt(
        session_home=home,
        launch_id="0123456789abcdef",
        attempt=1,
        current_resume_spec=NamedResume("thread-resume"),
    ) as handle:
        resumed = (home / public_name).resolve() / "2026" / "07" / canonical.name
        assert resumed.is_file()
        assert (resumed.stat().st_dev, resumed.stat().st_ino) == canonical_identity
        assert not ((home / public_name).resolve() / "2026" / "07" / unrelated.name).exists()
        other_public = "archived_sessions" if public_name == "sessions" else "sessions"
        assert list((home / other_public).resolve().rglob("*.jsonl")) == []
        handle.record_spawn(os.getpid(), os.getpgrp())
        handle.record_reaped(os.getpid(), os.getpgrp())

    assert (canonical.stat().st_dev, canonical.stat().st_ino) == canonical_identity
    assert unrelated.is_file()
    assert (home / "sessions").resolve() == inert_targets["sessions"]
    assert (home / "archived_sessions").resolve() == inert_targets["archived_sessions"]
