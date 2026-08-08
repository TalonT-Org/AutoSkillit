"""Tests for exact plugin retirement, install locking, and kitchen state."""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import threading
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import structlog

from autoskillit.core import (
    ArtifactLease,
    PluginArtifactIdentity,
    PluginArtifactKind,
    RetirementOutcome,
    RetiringArtifactRecord,
    append_retiring_record,
    read_retiring_cache,
    remove_retiring_records,
)
from tests._helpers import _flush_structlog_proxy_caches

pytestmark = [pytest.mark.layer("cli"), pytest.mark.medium]


def _installed_identity(tmp_path: Path, version: str = "1.0.0"):
    from autoskillit.cli._plugin_artifact import publish_installed_plugin_artifact

    root = (
        tmp_path / ".claude" / "plugins" / "cache" / "autoskillit-local" / "autoskillit" / version
    ).absolute()
    root.mkdir(parents=True)
    (root / "plugin.json").write_text('{"name":"autoskillit"}', encoding="utf-8")
    metadata = root / ".claude-plugin" / "plugin.json"
    metadata.parent.mkdir(parents=True)
    metadata.write_text(
        json.dumps({"name": "autoskillit", "version": version}),
        encoding="utf-8",
    )
    return publish_installed_plugin_artifact(
        root,
        semantic_key=f"autoskillit@autoskillit-local:{version}",
    )


def _record(
    tmp_path: Path,
    *,
    record_id: str,
    incarnation_id: str,
    not_before: datetime,
) -> RetiringArtifactRecord:
    return RetiringArtifactRecord(
        record_id=record_id,
        artifact_kind=PluginArtifactKind.INSTALLED_PLUGIN,
        semantic_key="autoskillit@market:1.0.0",
        managed_path=(tmp_path / "cache" / incarnation_id).absolute(),
        manifest_path=(tmp_path / "cache" / f".{incarnation_id}.json").absolute(),
        incarnation_id=incarnation_id,
        manifest_schema_version=1,
        artifact_digest="a" * 64,
        retired_at=not_before - timedelta(hours=1),
        not_before=not_before,
    )


def test_exact_append_is_ordered_idempotent_and_removed_by_record_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    deadline = datetime.now(UTC) + timedelta(hours=6)
    first = _record(
        tmp_path,
        record_id="record-a",
        incarnation_id="00000000000040008000000000000001",
        not_before=deadline,
    )
    second = _record(
        tmp_path,
        record_id="record-b",
        incarnation_id="00000000000040008000000000000002",
        not_before=deadline,
    )

    assert append_retiring_record(first).created is True
    assert append_retiring_record(second).created is True
    duplicate = replace(second, record_id="different-id")
    duplicate_result = append_retiring_record(duplicate)

    assert duplicate_result.record_id == second.record_id
    assert duplicate_result.created is False
    assert read_retiring_cache().records == (first, second)
    assert remove_retiring_records((first.record_id,)) == 1
    assert read_retiring_cache().records == (second,)


def test_installed_reclaim_defers_until_final_reader_closes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoskillit.cli._plugin_artifact import (
        InstalledPluginArtifactRetirementOwner,
        installed_plugin_artifact_lease_path,
    )

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    identity = _installed_identity(tmp_path)
    owner = InstalledPluginArtifactRetirementOwner(identity.managed_path.parent)
    deadline = datetime.now(UTC) + timedelta(seconds=1)
    append_result = owner.enqueue_retirement(identity, deadline)
    record = next(
        record
        for record in read_retiring_cache().records
        if record.record_id == append_result.record_id
    )
    reader = ArtifactLease.acquire_shared(
        installed_plugin_artifact_lease_path(identity.managed_path)
    )
    try:
        assert (
            owner.try_reclaim(record, deadline + timedelta(seconds=1))
            is RetirementOutcome.DEFERRED_CONTENDED
        )
        assert identity.managed_path.is_dir()
        assert identity.manifest_path.is_file()
    finally:
        reader.close()

    assert (
        owner.try_reclaim(record, deadline + timedelta(seconds=1)) is RetirementOutcome.RECLAIMED
    )
    assert not identity.managed_path.exists()
    assert not identity.manifest_path.exists()


def test_installed_reclaim_io_failure_stays_queued_for_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import autoskillit.core._plugin_cache as plugin_cache
    from autoskillit.cli._plugin_artifact import InstalledPluginArtifactRetirementOwner

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    identity = _installed_identity(tmp_path)
    owner = InstalledPluginArtifactRetirementOwner(identity.managed_path.parent)
    deadline = datetime.now(UTC)
    append_result = owner.enqueue_retirement(identity, deadline)
    record = read_retiring_cache().records[0]

    real_rmtree = plugin_cache.shutil.rmtree

    def fail_reclaim(path):
        (path / "plugin.json").unlink()
        raise PermissionError("injected installed reclaim failure")

    monkeypatch.setattr(plugin_cache.shutil, "rmtree", fail_reclaim)

    assert owner.try_reclaim(record, deadline) is RetirementOutcome.DEFERRED_IO_ERROR
    assert not identity.managed_path.exists()
    assert not identity.manifest_path.exists()
    assert append_result.record_id in {
        queued.record_id for queued in read_retiring_cache().records
    }
    monkeypatch.setattr(plugin_cache.shutil, "rmtree", real_rmtree)

    assert owner.try_reclaim(record, deadline) is RetirementOutcome.RECLAIMED
    assert append_result.record_id not in {
        queued.record_id for queued in read_retiring_cache().records
    }


@pytest.mark.parametrize("error", [PermissionError("denied"), RuntimeError("invalid sidecar")])
def test_installed_reclaim_lease_failure_stays_queued_for_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> None:
    import autoskillit.core._plugin_cache as plugin_cache
    from autoskillit.cli._plugin_artifact import InstalledPluginArtifactRetirementOwner

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    identity = _installed_identity(tmp_path)
    owner = InstalledPluginArtifactRetirementOwner(identity.managed_path.parent)
    deadline = datetime.now(UTC)
    append_result = owner.enqueue_retirement(identity, deadline)
    record = read_retiring_cache().records[0]

    def fail_acquire(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(plugin_cache.ArtifactLease, "acquire_exclusive", fail_acquire)

    assert owner.try_reclaim(record, deadline) is RetirementOutcome.DEFERRED_IO_ERROR
    assert identity.managed_path.is_dir()
    assert append_result.record_id in {
        queued.record_id for queued in read_retiring_cache().records
    }


def test_installed_reclaim_defers_when_cache_reread_is_unsafe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoskillit.cli._plugin_artifact import InstalledPluginArtifactRetirementOwner

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    identity = _installed_identity(tmp_path)
    owner = InstalledPluginArtifactRetirementOwner(identity.managed_path.parent)
    deadline = datetime.now(UTC)
    owner.enqueue_retirement(identity, deadline)
    record = read_retiring_cache().records[0]
    cache = tmp_path / ".autoskillit" / "retiring_cache.json"
    cache.write_text("{not-json")

    assert owner.try_reclaim(record, deadline) is RetirementOutcome.DEFERRED_IO_ERROR
    assert identity.managed_path.is_dir()
    assert identity.manifest_path.is_file()
    assert cache.read_text() == "{not-json"


def test_installed_reclaim_keeps_authority_on_identity_io_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import autoskillit.core._plugin_artifact_identity as plugin_artifact_identity
    from autoskillit.cli._plugin_artifact import InstalledPluginArtifactRetirementOwner

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    identity = _installed_identity(tmp_path)
    owner = InstalledPluginArtifactRetirementOwner(identity.managed_path.parent)
    deadline = datetime.now(UTC)
    owner.enqueue_retirement(identity, deadline)
    record = read_retiring_cache().records[0]

    def fail_digest(_path: Path) -> str:
        raise PermissionError("injected transient digest failure")

    monkeypatch.setattr(
        plugin_artifact_identity,
        "directory_tree_digest",
        fail_digest,
    )

    assert owner.try_reclaim(record, deadline) is RetirementOutcome.DEFERRED_IO_ERROR
    assert read_retiring_cache().records == (record,)
    assert identity.managed_path.is_dir()
    assert identity.manifest_path.is_file()


def test_installed_lifecycle_events_use_the_shared_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoskillit.cli._plugin_artifact import (
        InstalledPluginArtifactAuthority,
        InstalledPluginArtifactRetirementOwner,
    )
    from autoskillit.core import PluginLoadMode

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    _flush_structlog_proxy_caches()
    try:
        with structlog.testing.capture_logs() as logs:
            identity = _installed_identity(tmp_path)
            registry = tmp_path / ".claude" / "plugins" / "installed_plugins.json"
            registry.write_text(
                json.dumps(
                    {
                        "version": 2,
                        "plugins": {
                            "autoskillit@autoskillit-local": {
                                "installPath": str(identity.managed_path)
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            binding = InstalledPluginArtifactAuthority(
                identity.managed_path,
                semantic_key=identity.semantic_key,
            ).acquire_launch_binding(
                backend=object(),  # type: ignore[arg-type]
                load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
            )
            binding.close()
            InstalledPluginArtifactRetirementOwner(
                identity.managed_path.parent
            ).enqueue_retirement(
                identity,
                datetime.now(UTC) + timedelta(hours=6),
            )
    finally:
        _flush_structlog_proxy_caches()

    lifecycle = [entry for entry in logs if entry.get("event") == "plugin_artifact_lifecycle"]
    assert [entry["action"] for entry in lifecycle] == [
        "publish",
        "acquire",
        "release",
        "retire",
    ]
    assert all(entry["artifact_kind"] == "installed_plugin" for entry in lifecycle)
    assert all(entry["semantic_key"] == identity.semantic_key for entry in lifecycle)
    assert all(entry["incarnation"] == identity.incarnation_id for entry in lifecycle)
    assert all("actor_pid" in entry and "child_pid" in entry for entry in lifecycle)


def test_launch_binding_validates_generation_selected_during_lease_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoskillit.cli._plugin_artifact import InstalledPluginArtifactAuthority
    from autoskillit.core import PluginLoadMode, _InstallLock
    from autoskillit.workspace import publish_generation

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    source_root = tmp_path / "source"
    source_root.mkdir()
    (source_root / "content.txt").write_text("first", encoding="utf-8")

    def publish() -> PluginArtifactIdentity:
        with _InstallLock():
            return publish_generation(
                home=tmp_path,
                plugin_ref="autoskillit",
                version="1.0.0",
                semantic_key="autoskillit@autoskillit-local:1.0.0",
                source_root=source_root,
            )

    first = publish()
    acquire_existing_shared = ArtifactLease.acquire_existing_shared
    refreshed: list[PluginArtifactIdentity] = []

    def advance_then_acquire(_cls: type[ArtifactLease], lock_path: Path) -> ArtifactLease:
        if not refreshed:
            (source_root / "content.txt").write_text("second", encoding="utf-8")
            refreshed.append(publish())
            raise OSError("injected reclaim race")
        return acquire_existing_shared(lock_path)

    monkeypatch.setattr(
        ArtifactLease,
        "acquire_existing_shared",
        classmethod(advance_then_acquire),
    )

    binding = InstalledPluginArtifactAuthority(
        tmp_path / "legacy" / "1.0.0",
        semantic_key="autoskillit@autoskillit-local:1.0.0",
    ).acquire_launch_binding(
        backend=object(),  # type: ignore[arg-type]
        load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
    )
    try:
        assert binding.plugin_dir == refreshed[0].managed_path
        assert binding.plugin_dir != first.managed_path
    finally:
        binding.close()


def test_identity_mismatch_removes_record_without_deleting_current_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoskillit.cli._plugin_artifact import InstalledPluginArtifactRetirementOwner

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    identity = _installed_identity(tmp_path)
    owner = InstalledPluginArtifactRetirementOwner(identity.managed_path.parent)
    deadline = datetime.now(UTC) + timedelta(seconds=1)
    append_result = owner.enqueue_retirement(identity, deadline)
    record = read_retiring_cache().records[0]
    raw = json.loads(identity.manifest_path.read_text(encoding="utf-8"))
    raw["incarnation_id"] = "0" * 32
    mutated_manifest = json.dumps(raw).encode()
    identity.manifest_path.write_bytes(mutated_manifest)

    assert (
        owner.try_reclaim(record, deadline + timedelta(seconds=1))
        is RetirementOutcome.REJECTED_IDENTITY
    )
    assert identity.managed_path.is_dir()
    assert identity.manifest_path.read_bytes() == mutated_manifest
    assert append_result.record_id not in {
        queued.record_id for queued in read_retiring_cache().records
    }


def test_install_lock_creates_lock_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    from autoskillit.core._plugin_cache import _InstallLock

    lock_path = tmp_path / ".autoskillit" / "install.lock"
    with _InstallLock():
        assert lock_path.exists()
    assert lock_path.exists()


def test_install_lock_blocks_concurrent_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    from autoskillit.core._plugin_cache import _install_lock_path

    lock_file_path = _install_lock_path()
    lock_file_path.parent.mkdir(parents=True, exist_ok=True)
    acquired = threading.Event()
    release = threading.Event()

    def hold_lock() -> None:
        with open(lock_file_path, "w") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            acquired.set()
            release.wait(timeout=5)

    thread = threading.Thread(target=hold_lock, daemon=True)
    thread.start()
    assert acquired.wait(timeout=2)
    with open(lock_file_path, "w") as second:
        with pytest.raises(OSError):
            fcntl.flock(second, fcntl.LOCK_EX | fcntl.LOCK_NB)
    release.set()
    thread.join(timeout=2)
    assert not thread.is_alive()


def test_register_creates_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    from autoskillit.core._plugin_cache import register_active_kitchen

    register_active_kitchen("test-kitchen-001", os.getpid(), str(tmp_path))

    kitchens = json.loads((tmp_path / ".autoskillit" / "active_kitchens.json").read_text())[
        "kitchens"
    ]
    assert len(kitchens) == 1
    assert kitchens[0]["kitchen_id"] == "test-kitchen-001"
    assert kitchens[0]["pid"] == os.getpid()
    assert kitchens[0]["project_path"] == str(tmp_path)
    assert kitchens[0]["create_time"] is not None


def test_unregister_removes_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    from autoskillit.core._plugin_cache import (
        register_active_kitchen,
        unregister_active_kitchen,
    )

    register_active_kitchen("test-kitchen-002", os.getpid(), str(tmp_path))
    unregister_active_kitchen("test-kitchen-002")

    data = json.loads((tmp_path / ".autoskillit" / "active_kitchens.json").read_text())
    assert data["kitchens"] == []


def test_any_kitchen_open_false_when_pid_dead(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    from autoskillit.core._plugin_cache import (
        any_kitchen_open,
        register_active_kitchen,
    )

    process = subprocess.Popen(["true"])
    process.wait()
    register_active_kitchen("test-kitchen-003", process.pid, str(tmp_path))

    assert any_kitchen_open() is False


def test_any_kitchen_open_sweeps_stale(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    from autoskillit.core._plugin_cache import (
        any_kitchen_open,
        register_active_kitchen,
    )

    process = subprocess.Popen(["true"])
    process.wait()
    register_active_kitchen("test-kitchen-004", process.pid, str(tmp_path))
    any_kitchen_open()

    data = json.loads((tmp_path / ".autoskillit" / "active_kitchens.json").read_text())
    assert data["kitchens"] == []


def test_clear_kitchens_for_pid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    from autoskillit.core._plugin_cache import (
        clear_kitchens_for_pid,
        register_active_kitchen,
    )

    register_active_kitchen("test-kitchen-005a", os.getpid(), str(tmp_path))
    register_active_kitchen("test-kitchen-005b", os.getpid(), str(tmp_path))
    clear_kitchens_for_pid(os.getpid())

    data = json.loads((tmp_path / ".autoskillit" / "active_kitchens.json").read_text())
    assert data["kitchens"] == []


def test_any_kitchen_open_true_for_live_pid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    from autoskillit.core._plugin_cache import (
        any_kitchen_open,
        register_active_kitchen,
    )

    register_active_kitchen("test-kitchen-006", os.getpid(), str(tmp_path))

    assert any_kitchen_open() is True


def test_any_kitchen_open_scoped_excludes_other_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    from autoskillit.core._plugin_cache import (
        any_kitchen_open,
        register_active_kitchen,
    )

    register_active_kitchen("test-kitchen-007", os.getpid(), "/project_A")

    assert any_kitchen_open(project_path="/project_B") is False
    assert any_kitchen_open(project_path="/project_A") is True


def test_any_kitchen_open_no_project_path_returns_global(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    from autoskillit.core._plugin_cache import (
        any_kitchen_open,
        register_active_kitchen,
    )

    register_active_kitchen("test-kitchen-008", os.getpid(), "/project_A")

    assert any_kitchen_open() is True


def test_stale_cache_after_reorg_detected(tmp_path: Path) -> None:
    """validate_plugin_cache_hooks must find the real installed layout.

    hooks.json lives two segments below the cache plugin dir
    (<version>/hooks/hooks.json — the write site is
    cli/_marketplace.py's public_plugin_root / "hooks" / "hooks.json"), not
    one (<version>/hooks.json). This fixture pins the real layout so the
    validator's glob is exercised against it, not a shape it never sees in
    production.
    """
    from autoskillit.hook_registry import validate_plugin_cache_hooks

    version_dir = tmp_path / "cache" / "0.9.347"
    hooks_dir = version_dir / "hooks"
    hooks_dir.mkdir(parents=True)
    stale_hooks_json = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": ".*",
                    "hooks": [
                        {
                            "type": "command",
                            "command": f"python3 {tmp_path}/pkg/hooks/quota_guard.py",
                        },
                        {
                            "type": "command",
                            "command": f"python3 {tmp_path}/pkg/hooks/pretty_output_hook.py",
                        },
                    ],
                }
            ]
        }
    }
    (hooks_dir / "hooks.json").write_text(json.dumps(stale_hooks_json))

    broken = validate_plugin_cache_hooks(cache_dir=tmp_path / "cache")

    assert len(broken) == 2
    assert any("quota_guard.py" in command for command in broken)
    assert any("pretty_output_hook.py" in command for command in broken)


def test_stale_cache_relocatable_command_resolved_against_incarnation_dir(
    tmp_path: Path,
) -> None:
    """A relocatable (${CLAUDE_PLUGIN_ROOT}) command resolves against its own
    <version> incarnation directory — hooks_json_path.parent.parent, the
    plugin root Claude Code binds the token to — not hooks_json_path.parent
    (which would wrongly yield <version>/hooks/hooks/_dispatch.py).
    """
    from autoskillit.hook_registry import validate_plugin_cache_hooks

    version_dir = tmp_path / "cache" / "0.9.999"
    hooks_dir = version_dir / "hooks"
    hooks_dir.mkdir(parents=True)
    (hooks_dir / "_dispatch.py").write_text("# dispatcher stub")
    relocatable_hooks_json = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": ".*",
                    "hooks": [
                        {
                            "type": "command",
                            "command": (
                                'python3 "${CLAUDE_PLUGIN_ROOT}/hooks/_dispatch.py" '
                                "guards/quota_guard"
                            ),
                        }
                    ],
                }
            ]
        }
    }
    (hooks_dir / "hooks.json").write_text(json.dumps(relocatable_hooks_json))

    broken = validate_plugin_cache_hooks(cache_dir=tmp_path / "cache")

    assert broken == []
