from __future__ import annotations

import json
import os
import re
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import autoskillit.hooks._exploration_request_record as records
from autoskillit.hooks._exploration_request_record import (
    consume_exploration_request_record,
    write_exploration_request_record,
)

pytestmark = pytest.mark.medium


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / ".autoskillit" / "temp").mkdir(parents=True)
    return root


def _record_path(root: Path, token: str) -> Path:
    return (
        root
        / ".autoskillit"
        / "temp"
        / "exploration-requests"
        / f"exploration-request-{token}.json"
    )


def test_write_and_consume_are_explicit_rooted_and_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _project(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    token = write_exploration_request_record(root, "enable_exploration", "native-session")
    path = _record_path(root, token)
    payload = json.loads(path.read_text())

    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", token)
    assert set(payload) == {"session_id", "tool_name", "created_at"}
    assert payload["session_id"] == "native-session"
    assert payload["tool_name"] == "enable_exploration"
    assert consume_exploration_request_record(root, "enable_exploration", token) == (
        "native-session"
    )
    assert not path.exists()


@pytest.mark.parametrize("token", ["", "short", "../escape", "x" * 44, object()])
def test_unknown_or_malformed_token_returns_no_identity(tmp_path: Path, token: object) -> None:
    root = _project(tmp_path)
    assert consume_exploration_request_record(root, "enable_exploration", token) is None  # type: ignore[arg-type]


def test_wrong_tool_and_replay_are_rejected_and_removed(tmp_path: Path) -> None:
    root = _project(tmp_path)
    token = write_exploration_request_record(root, "enable_exploration", "native-session")

    assert consume_exploration_request_record(root, "get_exploration_page", token) is None
    assert consume_exploration_request_record(root, "enable_exploration", token) is None
    assert not _record_path(root, token).exists()


def test_expiry_uses_record_time_not_mtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _project(tmp_path)
    monkeypatch.setattr(records, "_clock", lambda: 100.0)
    token = write_exploration_request_record(root, "enable_exploration", "native-session")
    os.utime(_record_path(root, token), (10_000.0, 10_000.0))
    monkeypatch.setattr(records, "_clock", lambda: 131.0)

    assert consume_exploration_request_record(root, "enable_exploration", token) is None


def test_atomic_claim_has_exactly_one_winner(tmp_path: Path) -> None:
    root = _project(tmp_path)
    token = write_exploration_request_record(root, "get_exploration_page", "native-session")

    def consume() -> str | None:
        return consume_exploration_request_record(root, "get_exploration_page", token)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _index: consume(), range(2)))

    assert results.count("native-session") == 1
    assert results.count(None) == 1


def test_cleanup_is_limited_to_expired_request_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _project(tmp_path)
    monkeypatch.setattr(records, "_clock", lambda: 100.0)
    stale_token = write_exploration_request_record(root, "enable_exploration", "stale-session")
    stale = _record_path(root, stale_token)
    os.utime(stale, (0.0, 0.0))
    ordinary_marker = root / ".autoskillit" / "temp" / "ordinary.marker"
    ordinary_marker.write_text("keep")
    os.utime(ordinary_marker, (0.0, 0.0))

    write_exploration_request_record(root, "enable_exploration", "fresh-session")

    assert not stale.exists()
    assert ordinary_marker.read_text() == "keep"


def test_creation_forces_mode_0600_despite_umask(tmp_path: Path) -> None:
    root = _project(tmp_path)
    previous = os.umask(0o777)
    try:
        token = write_exploration_request_record(root, "enable_exploration", "native-session")
    finally:
        os.umask(previous)

    assert stat.S_IMODE(_record_path(root, token).stat().st_mode) == 0o600


def test_claimed_inode_mode_is_validated(tmp_path: Path) -> None:
    root = _project(tmp_path)
    token = write_exploration_request_record(root, "enable_exploration", "native-session")
    os.chmod(_record_path(root, token), 0o644)

    assert consume_exploration_request_record(root, "enable_exploration", token) is None
    assert not _record_path(root, token).exists()


def test_record_symlink_substitution_is_rejected(tmp_path: Path) -> None:
    root = _project(tmp_path)
    token = "A" * 43
    target = tmp_path / "target.json"
    target.write_text(
        json.dumps(
            {
                "session_id": "native-session",
                "tool_name": "enable_exploration",
                "created_at": records._clock(),
            }
        )
    )
    path = _record_path(root, token)
    path.parent.mkdir(parents=True)
    path.symlink_to(target)

    assert consume_exploration_request_record(root, "enable_exploration", token) is None
    assert target.exists()
