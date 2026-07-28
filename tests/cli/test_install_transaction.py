"""A failed install must leave the machine exactly as it found it.

F3: `install()` retired the live plugin cache *before* securing its replacement
and never rolled back. Four windows could exit between those two points. A later
startup sweep then created a dangling registry pointer, which is how the
reporting machine reached the state that crashed `cook`.

Both halves are covered: the preflight ordering (nothing mutates before every
decline path has run) and the rollback (every failure after that restores the
manifest, the registry, and the retiring queue).
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("cli"), pytest.mark.medium]

_PLUGIN_KEY = "autoskillit@autoskillit-local"


def _seed_installed_state(home: Path, version: str) -> Path:
    """A machine with a working install of *version* already in place."""
    cache = home / ".claude" / "plugins" / "cache" / "autoskillit-local" / "autoskillit" / version
    cache.mkdir(parents=True)
    (cache / ".claude-plugin").mkdir()
    (cache / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "autoskillit", "version": version})
    )

    registry = home / ".claude" / "plugins" / "installed_plugins.json"
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(
        json.dumps({"version": 2, "plugins": {_PLUGIN_KEY: {"installPath": str(cache)}}})
    )

    manifest = home / ".autoskillit" / "marketplace" / ".claude-plugin" / "marketplace.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps({"name": "autoskillit-local", "plugins": [{"version": version}]})
    )
    return cache


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text())


def _retiring_paths(home: Path) -> set[str]:
    retiring = home / ".autoskillit" / "retiring_cache.json"
    if not retiring.is_file():
        return set()
    data = _read_json(retiring)
    if data.get("schema_version") == 2:
        return {str(entry.get("managed_path", "")) for entry in data.get("records", [])}
    return {str(entry.get("path", "")) for entry in data.get("retiring", [])}


class TestRollbackOnFailure:
    @pytest.mark.parametrize("failing_step", ["marketplace add", "plugin install"])
    def test_failed_install_restores_pre_attempt_state(
        self, failing_step: str, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        """Neither `claude` subcommand failing may leave a dangling pointer."""
        from autoskillit.cli import _marketplace

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.setattr(_marketplace, "is_git_worktree", lambda path: False)
        monkeypatch.setattr(_marketplace.shutil, "which", lambda _cmd: "/usr/bin/claude")

        old_cache = _seed_installed_state(tmp_path, "0.0.1-old")
        manifest_path = _marketplace._marketplace_manifest_path()
        registry_path = _marketplace._installed_plugins_json_path()
        manifest_before = manifest_path.read_text()
        registry_before = registry_path.read_text()
        retiring_before = _retiring_paths(tmp_path)

        def fake_run(cmd, **_kw):
            joined = " ".join(str(c) for c in cmd)
            failed = failing_step in joined
            return subprocess.CompletedProcess(
                cmd, 1 if failed else 0, stdout="", stderr="boom" if failed else ""
            )

        monkeypatch.setattr(_marketplace.subprocess, "run", fake_run)

        with pytest.raises(SystemExit):
            _marketplace.install(scope="user")
        capsys.readouterr()

        assert manifest_path.read_text() == manifest_before, (
            "marketplace.json was left at the new version after a failed install"
        )
        assert registry_path.read_text() == registry_before, (
            "installed_plugins.json was not restored after a failed install"
        )

        registered = {
            e["installPath"] for e in [_read_json(registry_path)["plugins"][_PLUGIN_KEY]]
        }
        assert not (registered & _retiring_paths(tmp_path)), (
            "a directory still named by installed_plugins.json is queued for deletion — "
            "this is the dangling-pointer state F3 produced"
        )
        assert _retiring_paths(tmp_path) == retiring_before
        assert old_cache.is_dir(), "the live cache was destroyed by a failed install"

    def test_partial_backup_cleanup_cannot_rearm_root_rollback(
        self,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        from autoskillit.cli import _marketplace

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        target = _marketplace._installed_plugin_root()
        target.mkdir(parents=True)
        (target / "old").write_text("old")
        snapshot = _marketplace._InstallSnapshot()
        snapshot.stage_target_root()
        backup = snapshot._target_backup
        assert backup is not None
        target.mkdir()
        new_marker = target / "new"
        new_marker.write_text("new")

        def partially_remove_then_fail(path: Path) -> None:
            assert path == backup
            (path / "old").unlink()
            raise PermissionError("injected partial backup cleanup")

        monkeypatch.setattr(_marketplace.shutil, "rmtree", partially_remove_then_fail)

        snapshot.commit()
        snapshot.rollback()

        assert new_marker.read_text() == "new"
        assert snapshot._target_backup is None
        assert snapshot._target_mutation_owned is False

    def test_persisted_retirement_is_tracked_when_parent_fsync_fails(
        self,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        from autoskillit.cli import _marketplace, _plugin_artifact
        from autoskillit.core import read_retiring_cache

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        old_cache = _seed_installed_state(tmp_path, "0.0.1-old")
        _plugin_artifact.publish_installed_plugin_artifact(
            old_cache,
            semantic_key="autoskillit@autoskillit-local:0.0.1-old",
        )
        snapshot = _marketplace._InstallSnapshot()
        real_fsync = os.fsync
        calls = 0

        def fail_retiring_parent_fsync(fd: int) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected retirement parent fsync failure")
            real_fsync(fd)

        monkeypatch.setattr(os, "fsync", fail_retiring_parent_fsync)

        with pytest.raises(OSError, match="parent fsync failure"):
            _marketplace._clear_plugin_cache(
                on_retirement_created=snapshot.track_retirement,
            )

        assert len(read_retiring_cache().records) == 1
        snapshot.rollback()
        assert read_retiring_cache().records == ()

    def test_target_lease_contention_does_not_mutate_the_live_root(
        self,
        tmp_path: Path,
        monkeypatch,
        capsys,
    ) -> None:
        from autoskillit import __version__
        from autoskillit.cli import _marketplace, _plugin_artifact
        from autoskillit.core import ArtifactLease

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.setattr(_marketplace, "is_git_worktree", lambda path: False)
        monkeypatch.setattr(_marketplace.shutil, "which", lambda _cmd: "/usr/bin/claude")
        monkeypatch.setattr(
            _marketplace,
            "_ensure_marketplace",
            lambda: pytest.fail("marketplace mutation started before target lease ownership"),
        )
        monkeypatch.setattr(
            _marketplace,
            "_ensure_workspace_ready",
            lambda: pytest.fail("workspace mutation started before target lease ownership"),
        )
        monkeypatch.setattr(
            _marketplace._InstallSnapshot,
            "rollback",
            lambda _snapshot: pytest.fail("contention rolled back unowned shared state"),
        )
        target = _seed_installed_state(tmp_path, __version__)
        marker = target / "must-survive"
        marker.write_text("leased live tree")
        reader = ArtifactLease.acquire_shared(
            _plugin_artifact.installed_artifact_lock_path(target)
        )
        try:
            with pytest.raises(SystemExit):
                _marketplace.install(scope="user")
        finally:
            reader.close()

        assert "Installed plugin is in use" in capsys.readouterr().out
        assert marker.read_text() == "leased live tree"

    def test_partial_cache_clear_registers_retirement_before_later_failure(
        self,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        from autoskillit.cli import _marketplace, _plugin_artifact
        from autoskillit.cli._installed_plugins import InstalledPluginsFile

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.setattr(_marketplace, "is_git_worktree", lambda path: False)
        monkeypatch.setattr(_marketplace.shutil, "which", lambda _cmd: "/usr/bin/claude")
        monkeypatch.setattr(
            _marketplace,
            "_ensure_marketplace",
            lambda: tmp_path / ".autoskillit" / "marketplace",
        )
        monkeypatch.setattr(_marketplace, "_ensure_workspace_ready", lambda: None)
        old_cache = _seed_installed_state(tmp_path, "0.0.1-old")
        _plugin_artifact.publish_installed_plugin_artifact(
            old_cache,
            semantic_key="autoskillit@autoskillit-local:0.0.1-old",
        )
        retiring_before = _retiring_paths(tmp_path)

        def fail_remove(self, plugin_key):
            raise OSError(f"injected removal failure for {plugin_key}")

        monkeypatch.setattr(InstalledPluginsFile, "remove", fail_remove)

        with pytest.raises(OSError, match="injected removal failure"):
            _marketplace.install(scope="user")

        assert old_cache.is_dir()
        assert _retiring_paths(tmp_path) == retiring_before

    def test_publication_failure_rolls_back_while_target_lease_is_held(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        from autoskillit.cli import _marketplace, _plugin_artifact
        from autoskillit.core import (
            ArtifactLease,
            ArtifactLeaseContention,
            PluginArtifactPublicationError,
        )

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.setattr(_marketplace, "is_git_worktree", lambda path: False)
        monkeypatch.setattr(_marketplace.shutil, "which", lambda _cmd: "/usr/bin/claude")

        old_cache = _seed_installed_state(tmp_path, "0.0.1-old")
        target_root = _marketplace._installed_plugin_root()
        _plugin_artifact.publish_installed_plugin_artifact(
            old_cache,
            semantic_key="autoskillit@autoskillit-local:0.0.1-old",
        )
        artifact_manifest = _plugin_artifact.installed_artifact_manifest_path(old_cache)
        manifest_path = _marketplace._marketplace_manifest_path()
        registry_path = _marketplace._installed_plugins_json_path()
        artifact_manifest_before = artifact_manifest.read_text()
        manifest_before = manifest_path.read_text()
        registry_before = registry_path.read_text()
        retiring_before = _retiring_paths(tmp_path)

        monkeypatch.setattr(
            _marketplace.subprocess,
            "run",
            lambda cmd, **_kw: subprocess.CompletedProcess(
                cmd,
                0,
                stdout="",
                stderr="",
            ),
        )

        def fail_publication(*_args, **_kwargs):
            raise PluginArtifactPublicationError("injected publication failure")

        monkeypatch.setattr(
            _plugin_artifact,
            "publish_installed_plugin_artifact",
            fail_publication,
        )

        original_rollback = _marketplace._InstallSnapshot.rollback

        def assert_lease_then_rollback(snapshot) -> None:
            with pytest.raises(ArtifactLeaseContention):
                ArtifactLease.acquire_exclusive(
                    _plugin_artifact.installed_artifact_lock_path(target_root),
                    blocking=False,
                )
            original_rollback(snapshot)

        monkeypatch.setattr(
            _marketplace._InstallSnapshot,
            "rollback",
            assert_lease_then_rollback,
        )

        with pytest.raises(SystemExit):
            _marketplace.install(scope="user")
        capsys.readouterr()

        assert old_cache.is_dir()
        assert artifact_manifest.read_text() == artifact_manifest_before
        assert manifest_path.read_text() == manifest_before
        assert registry_path.read_text() == registry_before
        assert _retiring_paths(tmp_path) == retiring_before


class TestPreflightOrdering:
    def test_worktree_guard_precedes_the_claudecode_deferral(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """A worktree install inside a Claude Code session must name the worktree.

        If CLAUDECODE were checked first, the user would be told to "run these
        commands in a regular terminal" — advice that cannot work, because the
        real problem is that the source is a transient worktree.
        """
        from autoskillit.cli import _marketplace

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("CLAUDECODE", "1")
        monkeypatch.setattr(_marketplace, "is_git_worktree", lambda path: True)

        with pytest.raises(SystemExit, match="worktree"):
            _marketplace.install(scope="user")

    def test_declining_under_claudecode_mutates_nothing(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        """The CLAUDECODE deferral used to fire *after* _ensure_marketplace()."""
        from autoskillit.cli import _marketplace

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("CLAUDECODE", "1")
        monkeypatch.setattr(_marketplace, "is_git_worktree", lambda path: False)

        assert _marketplace.install(scope="user") is False
        capsys.readouterr()
        assert not (tmp_path / ".autoskillit" / "marketplace").exists(), (
            "install() mutated the marketplace before deciding to defer"
        )

    def test_missing_claude_binary_mutates_nothing(
        self, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        from autoskillit.cli import _marketplace

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.setattr(_marketplace, "is_git_worktree", lambda path: False)
        monkeypatch.setattr(_marketplace.shutil, "which", lambda _cmd: None)

        with pytest.raises(SystemExit):
            _marketplace.install(scope="user")
        capsys.readouterr()
        assert not (tmp_path / ".autoskillit" / "marketplace").exists()


class TestInstallTargetSafety:
    def test_symlinked_version_target_is_rejected_without_following_it(
        self,
        tmp_path: Path,
        monkeypatch,
        capsys,
    ) -> None:
        from autoskillit.cli import _marketplace

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.setattr(_marketplace, "is_git_worktree", lambda path: False)
        monkeypatch.setattr(_marketplace.shutil, "which", lambda _cmd: "/usr/bin/claude")
        monkeypatch.setattr(
            _marketplace,
            "_ensure_marketplace",
            lambda: pytest.fail("unsafe target reached marketplace mutation"),
        )
        outside = tmp_path / "outside-managed-cache"
        outside.mkdir()
        marker = outside / "must-survive"
        marker.write_text("owned elsewhere")
        target = _marketplace._installed_plugin_root()
        target.parent.mkdir(parents=True)
        target.symlink_to(outside, target_is_directory=True)

        with pytest.raises(SystemExit):
            _marketplace.install(scope="user")

        assert "Unsafe installed plugin target" in capsys.readouterr().out
        assert target.is_symlink()
        assert marker.read_text() == "owned elsewhere"


class TestRecordOwnedRetirementDeadline:
    @pytest.mark.parametrize(
        "cache_bytes",
        [
            b"{not-json",
            b'{"schema_version":999,"records":[]}',
        ],
        ids=["corrupt", "unsupported-future"],
    )
    def test_coordinator_preserves_unsafe_cache_without_dispatch(
        self,
        cache_bytes: bytes,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        from datetime import UTC, datetime

        from autoskillit.cli._plugin_artifact import DefaultPluginRetirementCoordinator
        from autoskillit.core import PluginArtifactKind

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        cache = tmp_path / ".autoskillit" / "retiring_cache.json"
        cache.parent.mkdir(parents=True)
        cache.write_bytes(cache_bytes)

        class NoDispatchOwner:
            def __init__(
                self,
                *,
                artifact_kind: PluginArtifactKind,
                managed_root: Path,
            ) -> None:
                self.artifact_kind = artifact_kind
                self.managed_root = managed_root

            def try_reclaim(self, _record, _sweep_now):
                pytest.fail("unsafe retirement cache must not dispatch an owner")

        coordinator = DefaultPluginRetirementCoordinator(
            projection_owner=NoDispatchOwner(
                artifact_kind=PluginArtifactKind.PROJECTION,
                managed_root=tmp_path / "projections",
            ),
            installed_owner=NoDispatchOwner(
                artifact_kind=PluginArtifactKind.INSTALLED_PLUGIN,
                managed_root=tmp_path / "cache",
            ),
            projection_root=tmp_path / "projections",
        )

        with pytest.warns(RuntimeWarning, match="sweep skipped unsafe state"):
            assert coordinator.sweep_due(datetime.now(UTC)) == ()
        assert cache.read_bytes() == cache_bytes

    def test_coordinator_dispatches_only_due_exact_records(
        self,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        from datetime import UTC, datetime, timedelta

        from autoskillit.cli._plugin_artifact import DefaultPluginRetirementCoordinator
        from autoskillit.core import (
            PluginArtifactKind,
            RetirementOutcome,
            RetiringArtifactRecord,
            append_retiring_record,
        )

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        now = datetime.now(UTC)
        installed = RetiringArtifactRecord(
            record_id="installed-due",
            artifact_kind=PluginArtifactKind.INSTALLED_PLUGIN,
            semantic_key="plugin:installed",
            managed_path=(tmp_path / "cache" / "installed").absolute(),
            manifest_path=(tmp_path / "cache" / ".installed.json").absolute(),
            incarnation_id="00000000000040008000000000000001",
            manifest_schema_version=1,
            artifact_digest="a" * 64,
            retired_at=now - timedelta(hours=2),
            not_before=now,
        )
        projection = RetiringArtifactRecord(
            record_id="projection-due",
            artifact_kind=PluginArtifactKind.PROJECTION,
            semantic_key="plugin:projection",
            managed_path=(tmp_path / "projections" / "projection").absolute(),
            manifest_path=(tmp_path / "projections" / ".projection.json").absolute(),
            incarnation_id="00000000000040008000000000000002",
            manifest_schema_version=2,
            artifact_digest="b" * 64,
            retired_at=now - timedelta(hours=2),
            not_before=now,
        )
        future = RetiringArtifactRecord(
            record_id="future",
            artifact_kind=PluginArtifactKind.INSTALLED_PLUGIN,
            semantic_key="plugin:future",
            managed_path=(tmp_path / "cache" / "future").absolute(),
            manifest_path=(tmp_path / "cache" / ".future.json").absolute(),
            incarnation_id="00000000000040008000000000000003",
            manifest_schema_version=1,
            artifact_digest="a" * 64,
            retired_at=now,
            not_before=now + timedelta(hours=6),
        )
        for record in (installed, projection, future):
            append_retiring_record(record)
        dispatched: list[tuple[PluginArtifactKind, str, datetime]] = []

        class Owner:
            def __init__(
                self,
                *,
                artifact_kind: PluginArtifactKind,
                managed_root: Path,
                outcome: RetirementOutcome,
            ) -> None:
                self.artifact_kind = artifact_kind
                self.managed_root = managed_root
                self.outcome = outcome

            def try_reclaim(self, record, sweep_now):
                assert sweep_now == now
                assert record.artifact_kind is self.artifact_kind
                dispatched.append((record.artifact_kind, record.record_id, sweep_now))
                return self.outcome

        installed_owner = Owner(
            artifact_kind=PluginArtifactKind.INSTALLED_PLUGIN,
            managed_root=tmp_path / "cache",
            outcome=RetirementOutcome.RECLAIMED,
        )
        projection_owner = Owner(
            artifact_kind=PluginArtifactKind.PROJECTION,
            managed_root=tmp_path / "projections",
            outcome=RetirementOutcome.DEFERRED_CONTENDED,
        )
        coordinator = DefaultPluginRetirementCoordinator(
            projection_owner=projection_owner,
            installed_owner=installed_owner,
            projection_root=tmp_path / "projections",
        )

        assert coordinator.sweep_due(now) == (
            RetirementOutcome.RECLAIMED,
            RetirementOutcome.DEFERRED_CONTENDED,
        )
        assert dispatched == [
            (PluginArtifactKind.INSTALLED_PLUGIN, "installed-due", now),
            (PluginArtifactKind.PROJECTION, "projection-due", now),
        ]


class TestClearPluginCacheKeepsItsPromise:
    def test_docstring_claim_holds(self) -> None:
        """A documented-but-unimplemented guarantee is worse than an absent one.

        `_clear_plugin_cache`'s docstring has claimed since #924 that it removes
        the `installed_plugins.json` entry. It did not, and
        `InstalledPluginsFile.remove()` had zero callers anywhere in `src/`.
        Readers trusted a guarantee the code did not provide.
        """
        from autoskillit.cli._marketplace import _clear_plugin_cache

        doc = _clear_plugin_cache.__doc__ or ""
        assert "installed_plugins.json" in doc

    def test_remove_is_actually_wired_in(self, tmp_path: Path, monkeypatch) -> None:
        from autoskillit.cli._marketplace import _clear_plugin_cache

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        _seed_installed_state(tmp_path, "0.0.1-old")
        registry = tmp_path / ".claude" / "plugins" / "installed_plugins.json"
        assert _PLUGIN_KEY in _read_json(registry)["plugins"]

        _clear_plugin_cache()

        assert _PLUGIN_KEY not in _read_json(registry)["plugins"], (
            "_clear_plugin_cache still does not do what its docstring says"
        )

    def test_active_reader_is_still_queued_before_registry_removal(
        self,
        tmp_path: Path,
        monkeypatch,
    ) -> None:
        from autoskillit.cli._marketplace import _clear_plugin_cache
        from autoskillit.cli._plugin_artifact import (
            installed_artifact_lock_path,
            publish_installed_plugin_artifact,
        )
        from autoskillit.core import ArtifactLease, read_retiring_cache

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        root = _seed_installed_state(tmp_path, "0.0.1-old")
        identity = publish_installed_plugin_artifact(
            root,
            semantic_key="autoskillit@autoskillit-local:0.0.1-old",
        )
        registry = tmp_path / ".claude" / "plugins" / "installed_plugins.json"
        reader = ArtifactLease.acquire_shared(installed_artifact_lock_path(root))
        try:
            _clear_plugin_cache()
        finally:
            reader.close()

        assert _PLUGIN_KEY not in _read_json(registry)["plugins"]
        assert [record.identity for record in read_retiring_cache().records] == [identity]
