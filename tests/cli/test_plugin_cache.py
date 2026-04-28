"""Tests for _plugin_cache: retiring cache, install locking, kitchen registry."""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("cli"), pytest.mark.medium]


# ---------------------------------------------------------------------------
# Category A — Grace period on version change
# ---------------------------------------------------------------------------


def test_retire_old_versions_registers_different_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    from autoskillit.core._plugin_cache import _retire_old_versions

    cache_dir = tmp_path / "cache"
    old_dir = cache_dir / "0.8.0"
    old_dir.mkdir(parents=True)

    _retire_old_versions(cache_dir, "0.9.0")

    assert old_dir.exists(), "0.8.0/ must survive (grace period)"
    retiring_json = tmp_path / ".autoskillit" / "retiring_cache.json"
    data = json.loads(retiring_json.read_text())
    entries = data["retiring"]
    assert len(entries) == 1
    assert entries[0]["version"] == "0.8.0"
    assert isinstance(datetime.fromisoformat(entries[0]["retired_at"]), datetime)


def test_retire_old_versions_multiple_old_dirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    from autoskillit.core._plugin_cache import _retire_old_versions

    cache_dir = tmp_path / "cache"
    (cache_dir / "0.7.0").mkdir(parents=True)
    (cache_dir / "0.8.0").mkdir(parents=True)

    _retire_old_versions(cache_dir, "0.9.0")

    assert (cache_dir / "0.7.0").exists()
    assert (cache_dir / "0.8.0").exists()
    retiring_json = tmp_path / ".autoskillit" / "retiring_cache.json"
    data = json.loads(retiring_json.read_text())
    versions = {e["version"] for e in data["retiring"]}
    assert versions == {"0.7.0", "0.8.0"}


# ---------------------------------------------------------------------------
# Category B — Same-version reinstall
# ---------------------------------------------------------------------------


def test_retire_old_versions_deletes_same_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    from autoskillit.core._plugin_cache import _retire_old_versions

    cache_dir = tmp_path / "cache"
    same_dir = cache_dir / "0.9.0"
    same_dir.mkdir(parents=True)

    _retire_old_versions(cache_dir, "0.9.0")

    assert not same_dir.exists(), "Same-version dir must be deleted immediately"
    retiring_json = tmp_path / ".autoskillit" / "retiring_cache.json"
    assert not retiring_json.exists(), (
        "No retiring entries should be created for a same-version reinstall"
    )


def test_retire_old_versions_noop_empty_cache_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    from autoskillit.core._plugin_cache import _retire_old_versions

    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True)

    _retire_old_versions(cache_dir, "0.9.0")  # must not raise

    retiring_json = tmp_path / ".autoskillit" / "retiring_cache.json"
    assert not retiring_json.exists(), (
        "No retiring entries should be created for an empty cache dir"
    )


# ---------------------------------------------------------------------------
# Category C — Sweep deletes expired
# ---------------------------------------------------------------------------


def test_sweep_deletes_expired_entries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    from autoskillit.core._plugin_cache import sweep_retiring_cache

    old_dir = tmp_path / "cache" / "0.8.0"
    old_dir.mkdir(parents=True)
    retired_at = (datetime.now(UTC) - timedelta(hours=25)).isoformat()
    retiring_json = tmp_path / ".autoskillit" / "retiring_cache.json"
    retiring_json.parent.mkdir(parents=True, exist_ok=True)
    retiring_json.write_text(
        json.dumps(
            {
                "retiring": [{"version": "0.8.0", "path": str(old_dir), "retired_at": retired_at}],
                "schema_version": 1,
            }
        )
    )

    count = sweep_retiring_cache(grace_hours=24)

    assert count == 1
    assert not old_dir.exists()
    data = json.loads(retiring_json.read_text())
    assert data["retiring"] == []


def test_sweep_deletes_multiple_expired(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    from autoskillit.core._plugin_cache import sweep_retiring_cache

    dir1 = tmp_path / "cache" / "0.7.0"
    dir2 = tmp_path / "cache" / "0.8.0"
    dir1.mkdir(parents=True)
    dir2.mkdir(parents=True)
    old_ts = (datetime.now(UTC) - timedelta(hours=30)).isoformat()
    retiring_json = tmp_path / ".autoskillit" / "retiring_cache.json"
    retiring_json.parent.mkdir(parents=True, exist_ok=True)
    retiring_json.write_text(
        json.dumps(
            {
                "retiring": [
                    {"version": "0.7.0", "path": str(dir1), "retired_at": old_ts},
                    {"version": "0.8.0", "path": str(dir2), "retired_at": old_ts},
                ],
                "schema_version": 1,
            }
        )
    )

    count = sweep_retiring_cache(grace_hours=24)

    assert count == 2
    assert not dir1.exists()
    assert not dir2.exists()


# ---------------------------------------------------------------------------
# Category D — Sweep preserves fresh
# ---------------------------------------------------------------------------


def test_sweep_preserves_fresh_entries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    from autoskillit.core._plugin_cache import sweep_retiring_cache

    fresh_dir = tmp_path / "cache" / "0.8.0"
    fresh_dir.mkdir(parents=True)
    retired_at = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    retiring_json = tmp_path / ".autoskillit" / "retiring_cache.json"
    retiring_json.parent.mkdir(parents=True, exist_ok=True)
    retiring_json.write_text(
        json.dumps(
            {
                "retiring": [
                    {"version": "0.8.0", "path": str(fresh_dir), "retired_at": retired_at}
                ],
                "schema_version": 1,
            }
        )
    )

    count = sweep_retiring_cache(grace_hours=24)

    assert count == 0
    assert fresh_dir.exists()
    data = json.loads(retiring_json.read_text())
    assert len(data["retiring"]) == 1


def test_sweep_mixed_fresh_and_expired(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    from autoskillit.core._plugin_cache import sweep_retiring_cache

    expired_dir = tmp_path / "cache" / "0.7.0"
    fresh_dir = tmp_path / "cache" / "0.8.0"
    expired_dir.mkdir(parents=True)
    fresh_dir.mkdir(parents=True)
    expired_ts = (datetime.now(UTC) - timedelta(hours=30)).isoformat()
    fresh_ts = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    retiring_json = tmp_path / ".autoskillit" / "retiring_cache.json"
    retiring_json.parent.mkdir(parents=True, exist_ok=True)
    retiring_json.write_text(
        json.dumps(
            {
                "retiring": [
                    {"version": "0.7.0", "path": str(expired_dir), "retired_at": expired_ts},
                    {"version": "0.8.0", "path": str(fresh_dir), "retired_at": fresh_ts},
                ],
                "schema_version": 1,
            }
        )
    )

    count = sweep_retiring_cache(grace_hours=24)

    assert count == 1
    assert not expired_dir.exists()
    assert fresh_dir.exists()
    data = json.loads(retiring_json.read_text())
    assert len(data["retiring"]) == 1
    assert data["retiring"][0]["version"] == "0.8.0"


# ---------------------------------------------------------------------------
# Category E — Sweep error handling
# ---------------------------------------------------------------------------


def test_sweep_handles_already_deleted_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    from autoskillit.core._plugin_cache import sweep_retiring_cache

    missing_dir = tmp_path / "cache" / "0.8.0"
    # Deliberately do NOT create the directory
    old_ts = (datetime.now(UTC) - timedelta(hours=30)).isoformat()
    retiring_json = tmp_path / ".autoskillit" / "retiring_cache.json"
    retiring_json.parent.mkdir(parents=True, exist_ok=True)
    retiring_json.write_text(
        json.dumps(
            {
                "retiring": [{"version": "0.8.0", "path": str(missing_dir), "retired_at": old_ts}],
                "schema_version": 1,
            }
        )
    )

    count = sweep_retiring_cache(grace_hours=24)  # must not raise

    assert count == 1
    data = json.loads(retiring_json.read_text())
    assert data["retiring"] == []


def test_sweep_noop_when_file_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    from autoskillit.core._plugin_cache import sweep_retiring_cache

    count = sweep_retiring_cache()
    assert count == 0


def test_sweep_handles_malformed_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    from autoskillit.core._plugin_cache import sweep_retiring_cache

    retiring_json = tmp_path / ".autoskillit" / "retiring_cache.json"
    retiring_json.parent.mkdir(parents=True, exist_ok=True)
    retiring_json.write_text("not valid json {{{")

    count = sweep_retiring_cache()  # must not raise

    assert count == 0


def test_sweep_handles_missing_retired_at(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    from autoskillit.core._plugin_cache import sweep_retiring_cache

    old_dir = tmp_path / "cache" / "0.8.0"
    old_dir.mkdir(parents=True)
    retiring_json = tmp_path / ".autoskillit" / "retiring_cache.json"
    retiring_json.parent.mkdir(parents=True, exist_ok=True)
    retiring_json.write_text(
        json.dumps({"retiring": [{"version": "0.8.0", "path": str(old_dir)}], "schema_version": 1})
    )

    sweep_retiring_cache()  # must not raise

    data = json.loads(retiring_json.read_text())
    assert len(data["retiring"]) == 1, (
        "Entry with missing retired_at must be preserved in survivors"
    )
    assert data["retiring"][0]["version"] == "0.8.0"


# ---------------------------------------------------------------------------
# Category F — Install locking
# ---------------------------------------------------------------------------


def test_install_lock_creates_lock_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    from autoskillit.core._plugin_cache import _InstallLock

    lock_path = tmp_path / ".autoskillit" / "install.lock"
    with _InstallLock():
        assert lock_path.exists()
    # File still exists after release (fcntl lock is released, not deleted)
    assert lock_path.exists()


def test_install_lock_blocks_concurrent_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    from autoskillit.core._plugin_cache import _install_lock_path

    lock_file_path = _install_lock_path()
    lock_file_path.parent.mkdir(parents=True, exist_ok=True)

    acquired = threading.Event()
    release = threading.Event()

    def hold_lock() -> None:
        with open(lock_file_path, "w") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            acquired.set()
            release.wait(timeout=5)

    t = threading.Thread(target=hold_lock, daemon=True)
    t.start()
    assert acquired.wait(timeout=2), "background lock thread did not acquire within 2s"

    # Try non-blocking acquire — must fail while first holder has it
    with open(lock_file_path, "w") as fh2:
        try:
            fcntl.flock(fh2, fcntl.LOCK_EX | fcntl.LOCK_NB)
            blocked = False
            fcntl.flock(fh2, fcntl.LOCK_UN)
        except OSError:
            blocked = True

    release.set()
    t.join(timeout=2)
    assert not t.is_alive(), "lock-holding thread did not exit within 2s after release"

    assert blocked, "Second acquire must be blocked while first holder has the lock"


# ---------------------------------------------------------------------------
# Category G — Retiring registry integrity
# ---------------------------------------------------------------------------


def test_append_preserves_existing_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    from autoskillit.core._plugin_cache import append_retiring_entry

    append_retiring_entry("0.7.0", "/some/path/0.7.0")
    append_retiring_entry("0.8.0", "/some/path/0.8.0")

    retiring_json = tmp_path / ".autoskillit" / "retiring_cache.json"
    data = json.loads(retiring_json.read_text())
    versions = [e["version"] for e in data["retiring"]]
    assert "0.7.0" in versions
    assert "0.8.0" in versions


def test_registry_path_is_absolute(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    from autoskillit.core._plugin_cache import append_retiring_entry

    abs_path = str(tmp_path / "cache" / "0.8.0")
    append_retiring_entry("0.8.0", abs_path)

    retiring_json = tmp_path / ".autoskillit" / "retiring_cache.json"
    data = json.loads(retiring_json.read_text())
    assert len(data["retiring"]) == 1
    assert Path(data["retiring"][0]["path"]).is_absolute()


# ---------------------------------------------------------------------------
# Category K — Kitchen registry
# ---------------------------------------------------------------------------


def test_register_creates_entry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    from autoskillit.core._plugin_cache import register_active_kitchen

    kitchen_id = "test-kitchen-001"
    pid = os.getpid()
    project_path = str(tmp_path)

    register_active_kitchen(kitchen_id, pid, project_path)

    akp = tmp_path / ".autoskillit" / "active_kitchens.json"
    data = json.loads(akp.read_text())
    kitchens = data["kitchens"]
    assert len(kitchens) == 1
    assert kitchens[0]["kitchen_id"] == kitchen_id
    assert kitchens[0]["pid"] == pid
    assert kitchens[0]["project_path"] == project_path
    assert kitchens[0]["create_time"] is not None


def test_unregister_removes_entry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    from autoskillit.core._plugin_cache import register_active_kitchen, unregister_active_kitchen

    kitchen_id = "test-kitchen-002"
    register_active_kitchen(kitchen_id, os.getpid(), str(tmp_path))
    unregister_active_kitchen(kitchen_id)

    akp = tmp_path / ".autoskillit" / "active_kitchens.json"
    data = json.loads(akp.read_text())
    assert data["kitchens"] == []


def test_any_kitchen_open_false_when_pid_dead(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    from autoskillit.core._plugin_cache import any_kitchen_open, register_active_kitchen

    proc = subprocess.Popen(["true"])
    proc.wait()
    dead_pid = proc.pid
    register_active_kitchen("test-kitchen-003", dead_pid, str(tmp_path))

    result = any_kitchen_open()
    assert result is False


def test_any_kitchen_open_sweeps_stale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    from autoskillit.core._plugin_cache import any_kitchen_open, register_active_kitchen

    proc = subprocess.Popen(["true"])
    proc.wait()
    dead_pid = proc.pid
    register_active_kitchen("test-kitchen-004", dead_pid, str(tmp_path))

    any_kitchen_open()

    akp = tmp_path / ".autoskillit" / "active_kitchens.json"
    data = json.loads(akp.read_text())
    assert data["kitchens"] == []


def test_clear_kitchens_for_pid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    from autoskillit.core._plugin_cache import clear_kitchens_for_pid, register_active_kitchen

    pid = os.getpid()
    register_active_kitchen("test-kitchen-005a", pid, str(tmp_path))
    register_active_kitchen("test-kitchen-005b", pid, str(tmp_path))

    clear_kitchens_for_pid(pid)

    akp = tmp_path / ".autoskillit" / "active_kitchens.json"
    data = json.loads(akp.read_text())
    assert data["kitchens"] == []


def test_any_kitchen_open_true_for_live_pid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    from autoskillit.core._plugin_cache import any_kitchen_open, register_active_kitchen

    register_active_kitchen("test-kitchen-006", os.getpid(), str(tmp_path))

    result = any_kitchen_open()
    assert result is True
