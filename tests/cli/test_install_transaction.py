"""A failed install must leave the machine exactly as it found it.

F3: `install()` retired the live plugin cache *before* securing its replacement
and never rolled back. Four windows could exit between those two points. Since
`sweep_retiring_cache` is scheduled on every MCP server startup, a failed install
became a dangling registry pointer about two hours later — which is how the
reporting machine reached the state that crashed `cook`.

Both halves are covered: the preflight ordering (nothing mutates before every
decline path has run) and the rollback (every failure after that restores the
manifest, the registry, and the retiring queue).
"""

from __future__ import annotations

import json
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
    return {e.get("path", "") for e in _read_json(retiring).get("retiring", [])}


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


class TestSweeperDefersRegisteredDirectories:
    """The sweeper must never be the thing that creates a dangling pointer."""

    def _retire(self, home: Path, path: Path, *, hours_ago: float) -> None:
        from datetime import UTC, datetime, timedelta

        from autoskillit.core import write_versioned_json

        retired_at = (datetime.now(UTC) - timedelta(hours=hours_ago)).isoformat()
        write_versioned_json(
            home / ".autoskillit" / "retiring_cache.json",
            {"retiring": [{"version": "x", "path": str(path), "retired_at": retired_at}]},
            schema_version=1,
        )

    def test_registered_and_young_is_deferred(self, tmp_path: Path, monkeypatch) -> None:
        from autoskillit.core import sweep_retiring_cache

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        cache = _seed_installed_state(tmp_path, "0.0.1-old")
        self._retire(tmp_path, cache, hours_ago=5)

        assert sweep_retiring_cache(grace_hours=2) == 0
        assert cache.is_dir(), "the sweeper deleted a directory the registry still names"
        assert str(cache) in _retiring_paths(tmp_path)

    def test_past_the_ceiling_it_is_deleted_and_reported(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Deferral must be bounded or it livelocks.

        `retired_at` is stamped once, so an entry blocked by a stale registry is
        re-deferred on every later sweep with no state change — unbounded
        retention. Past the ceiling the directory goes and the *registry* is
        reported as the thing that is wrong.
        """
        from autoskillit.core import sweep_retiring_cache
        from autoskillit.workspace import verify_install_state

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        cache = _seed_installed_state(tmp_path, "0.0.1-old")
        self._retire(tmp_path, cache, hours_ago=1000)

        assert sweep_retiring_cache(grace_hours=2, max_defer_hours=72) == 1
        assert not cache.exists()

        checks = {f.check for f in verify_install_state()}
        assert "installed_plugins_install_path" in checks, (
            "deleting past the ceiling must surface the registry inconsistency"
        )

    def test_unregistered_and_aged_out_is_deleted(self, tmp_path: Path, monkeypatch) -> None:
        from autoskillit.core import sweep_retiring_cache

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        orphan = tmp_path / "orphan"
        orphan.mkdir()
        self._retire(tmp_path, orphan, hours_ago=5)

        assert sweep_retiring_cache(grace_hours=2) == 1
        assert not orphan.exists()


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
