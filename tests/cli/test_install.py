"""Tests for CLI install, upgrade, and quota-related commands."""

from __future__ import annotations

import importlib
import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from autoskillit import cli

pytestmark = [pytest.mark.layer("cli"), pytest.mark.medium]


class TestCLIInstall:
    def test_install_validates_scope(self, capsys: pytest.CaptureFixture) -> None:
        """install rejects invalid scope values."""
        from autoskillit.cli._marketplace import install

        with pytest.raises(SystemExit) as exc_info:
            install(scope="invalid")
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "Invalid scope" in captured.out

    def test_install_errors_without_claude(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """install prints manual instructions when claude is not on PATH."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setattr(shutil, "which", lambda cmd: None)
        monkeypatch.delenv("CLAUDECODE", raising=False)
        import importlib as _importlib

        _app_mod = _importlib.import_module("autoskillit.cli._marketplace")
        monkeypatch.setattr(_app_mod, "is_git_worktree", lambda path: False)
        from autoskillit.cli._marketplace import install

        with pytest.raises(SystemExit) as exc_info:
            install()
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "claude plugin marketplace add" in captured.out

    def test_install_creates_marketplace_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """install creates the marketplace directory structure."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        import importlib as _importlib

        _app_mod = _importlib.import_module("autoskillit.cli._marketplace")
        monkeypatch.setattr(_app_mod, "is_git_worktree", lambda path: False)
        from autoskillit.cli._marketplace import _ensure_marketplace

        marketplace_dir = _ensure_marketplace()
        assert (marketplace_dir / ".claude-plugin" / "marketplace.json").is_file()
        assert (marketplace_dir / "plugins" / "autoskillit").is_symlink()

    def test_install_symlink_target_is_independent_of_test_file_location(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Symlink target verified using importlib.resources, not __file__ depth-counting."""
        import importlib.resources as ir

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        import importlib as _importlib

        _app_mod = _importlib.import_module("autoskillit.cli._marketplace")
        monkeypatch.setattr(_app_mod, "is_git_worktree", lambda path: False)
        from autoskillit.cli._marketplace import _ensure_marketplace

        marketplace_dir = _ensure_marketplace()
        link = marketplace_dir / "plugins" / "autoskillit"
        expected = Path(ir.files("autoskillit"))
        assert link.resolve() == expected.resolve()

    def test_install_marketplace_json_content(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Marketplace manifest has correct structure and plugin name."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        import importlib as _importlib

        _app_mod = _importlib.import_module("autoskillit.cli._marketplace")
        monkeypatch.setattr(_app_mod, "is_git_worktree", lambda path: False)
        from autoskillit.cli._marketplace import _ensure_marketplace

        marketplace_dir = _ensure_marketplace()
        data = json.loads((marketplace_dir / ".claude-plugin" / "marketplace.json").read_text())
        assert data["name"] == "autoskillit-local"
        assert len(data["plugins"]) == 1
        assert data["plugins"][0]["name"] == "autoskillit"
        assert data["plugins"][0]["source"] == "./plugins/autoskillit"

    @patch("autoskillit.cli._marketplace.subprocess.run")
    def test_install_calls_claude_cli(
        self, mock_run: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """install calls claude plugin marketplace add + claude plugin install."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/claude")
        monkeypatch.delenv("CLAUDECODE", raising=False)
        import importlib as _importlib

        _app_mod = _importlib.import_module("autoskillit.cli._marketplace")
        monkeypatch.setattr(_app_mod, "is_git_worktree", lambda path: False)
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        from autoskillit.cli._marketplace import install

        install(scope="user")

        assert mock_run.call_count == 2
        marketplace_call = mock_run.call_args_list[0]
        install_call = mock_run.call_args_list[1]
        assert "marketplace" in marketplace_call[0][0]
        assert "add" in marketplace_call[0][0]
        assert "install" in install_call[0][0]
        assert "autoskillit@autoskillit-local" in install_call[0][0]
        assert "--scope" in install_call[0][0]
        assert "user" in install_call[0][0]

    @patch("autoskillit.cli._marketplace.subprocess.run")
    def test_install_passes_scope_to_claude(
        self, mock_run: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """install forwards the scope argument to claude plugin install."""
        import importlib as _importlib

        _app_mod = _importlib.import_module("autoskillit.cli._marketplace")

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/claude")
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.setattr(_app_mod, "is_git_worktree", lambda path: False)
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        from autoskillit.cli._marketplace import install

        install(scope="project")

        install_call = mock_run.call_args_list[1][0][0]
        scope_idx = install_call.index("--scope")
        assert install_call[scope_idx + 1] == "project"

    @patch("autoskillit.cli._marketplace.subprocess.run")
    def test_install_idempotent_marketplace(
        self, mock_run: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Running install twice recreates the symlink without error."""
        import importlib as _importlib

        _app_mod = _importlib.import_module("autoskillit.cli._marketplace")

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/claude")
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.setattr(_app_mod, "is_git_worktree", lambda path: False)
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        from autoskillit.cli._marketplace import install

        install()
        install()  # second run should not fail

        assert (tmp_path / ".autoskillit" / "marketplace" / "plugins" / "autoskillit").is_symlink()

    @patch("autoskillit.cli._marketplace.subprocess.run")
    def test_install_evicts_stale_direct_mcp_entry(
        self, mock_run: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """install() must remove a stale mcpServers.autoskillit entry left by a prior init."""
        import importlib as _importlib

        _app_mod = _importlib.import_module("autoskillit.cli._marketplace")

        # Seed stale direct entry as left by a prior `autoskillit init`
        claude_json = tmp_path / ".claude.json"
        claude_json.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "autoskillit": {"type": "stdio", "command": "autoskillit", "args": []}
                    }
                }
            )
        )
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/claude")
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.setattr(_app_mod, "is_git_worktree", lambda path: False)
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        from autoskillit.cli._marketplace import install

        install(scope="user")

        data = json.loads(claude_json.read_text())
        assert "autoskillit" not in data.get("mcpServers", {})


class TestMigrateCommand:
    """Tests for the ``autoskillit migrate`` CLI command."""

    # MIG1: --check reports outdated scripts without modifying them
    def test_check_reports_outdated_scripts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """migrate --check lists scripts needing migration and does not modify them."""
        import autoskillit

        monkeypatch.setattr(autoskillit, "__version__", "99.0.0")
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        script_content = 'name: my-pipeline\ndescription: Test\nautoskillit_version: "0.1.0"\n'
        (scripts_dir / "my-pipeline.yaml").write_text(script_content)

        with pytest.raises(SystemExit) as exc_info:
            cli.migrate(check=True)
        assert exc_info.value.code == 1

        captured = capsys.readouterr()
        assert "my-pipeline" in captured.out
        # Original file untouched
        assert (scripts_dir / "my-pipeline.yaml").read_text() == script_content

    # MIG2: No scripts to migrate prints "all scripts up to date"
    def test_no_pending_migrations_reports_up_to_date(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """migrate reports all scripts up to date when versions match."""
        import autoskillit

        current_version = autoskillit.__version__
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "current.yaml").write_text(
            f'name: current\ndescription: Up to date\nautoskillit_version: "{current_version}"\n'
        )

        cli.migrate(check=False)

        captured = capsys.readouterr()
        assert "All" in captured.out
        assert "at version" in captured.out

    # MIG3: Reports count of scripts needing migration
    def test_reports_count_of_pending_scripts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """migrate reports the number of scripts needing migration."""
        import autoskillit

        monkeypatch.setattr(autoskillit, "__version__", "99.0.0")
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "old1.yaml").write_text(
            'name: old1\ndescription: Old\nautoskillit_version: "0.1.0"\n'
        )
        (scripts_dir / "old2.yaml").write_text(
            'name: old2\ndescription: Also old\nautoskillit_version: "0.1.0"\n'
        )

        cli.migrate(check=False)

        captured = capsys.readouterr()
        assert "2 recipe(s) need migration" in captured.out

    # MIG4: --check returns exit code 1 when migrations pending
    def test_check_exits_1_when_pending(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """migrate --check exits with code 1 when scripts need migration."""
        import autoskillit

        monkeypatch.setattr(autoskillit, "__version__", "99.0.0")
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "outdated.yaml").write_text(
            'name: outdated\ndescription: Old\nautoskillit_version: "0.1.0"\n'
        )

        with pytest.raises(SystemExit) as exc_info:
            cli.migrate(check=True)
        assert exc_info.value.code == 1

    # MIG5: --check returns exit code 0 when all current
    def test_check_exits_0_when_current(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """migrate --check exits normally (no SystemExit) when all scripts are current."""
        import autoskillit

        current_version = autoskillit.__version__
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "current.yaml").write_text(
            f'name: current\ndescription: Up to date\nautoskillit_version: "{current_version}"\n'
        )

        # Should not raise SystemExit
        cli.migrate(check=True)

    # MC3: Without --check, output contains no "Claude Code session" instructions
    def test_migrate_no_check_prints_summary_only(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """MC3: Without --check, output lists pending but omits Claude Code session text."""
        import autoskillit

        monkeypatch.setattr(autoskillit, "__version__", "99.0.0")
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "old.yaml").write_text(
            'name: old\ndescription: Old recipe\nautoskillit_version: "0.1.0"\n'
        )

        cli.migrate(check=False)

        captured = capsys.readouterr()
        assert "old" in captured.out
        assert "Claude Code session" not in captured.out


class TestInstallCommand:
    def test_ensure_marketplace_raises_in_worktree(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_ensure_marketplace() raises SystemExit when is_git_worktree() returns True."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        import importlib as _importlib

        _app_mod = _importlib.import_module("autoskillit.cli._marketplace")
        monkeypatch.setattr(_app_mod, "is_git_worktree", lambda path: True)
        from autoskillit.cli._marketplace import _ensure_marketplace

        with pytest.raises(SystemExit, match="worktree"):
            _ensure_marketplace()

    def test_ensure_marketplace_succeeds_in_main_checkout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_ensure_marketplace() succeeds when is_git_worktree() returns False."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        import importlib as _importlib

        _app_mod = _importlib.import_module("autoskillit.cli._marketplace")
        monkeypatch.setattr(_app_mod, "is_git_worktree", lambda path: False)
        from autoskillit.cli._marketplace import _ensure_marketplace

        result = _ensure_marketplace()
        assert result == tmp_path / ".autoskillit" / "marketplace"

    def test_install_symlink_target_is_not_inside_git_worktree(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """After install, the symlink target must not be inside a git worktree.

        This is the regression test for the broken-symlink-after-cleanup bug.
        Skipped when running from a worktree install (which is the expected
        dev environment during worktree-based implementation).
        """
        from autoskillit.core.paths import is_git_worktree, pkg_root

        # Check filesystem directly — the cli conftest patches is_git_worktree
        # to return False, so we cannot rely on it for the skip guard.
        pkg = pkg_root()
        for ancestor in [pkg, *pkg.parents]:
            if (ancestor / ".git").is_file():
                pytest.skip("Cannot verify non-worktree install from a worktree environment")
            if (ancestor / ".git").is_dir():
                break

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        from autoskillit.cli._marketplace import _ensure_marketplace

        marketplace_dir = _ensure_marketplace()
        link = marketplace_dir / "plugins" / "autoskillit"

        target = link.resolve()
        assert target.is_dir(), "Symlink target must exist and be a directory"
        assert not is_git_worktree(target), (
            f"Symlink target {target} is inside a git worktree — "
            "it will break when the worktree is deleted."
        )


class TestGroupFInstall:
    """P8-2, P3-2, P5-4: CLI refactoring — install/quota/upgrade tests."""

    def test_upgrade_uses_atomic_write(self, tmp_path, monkeypatch):
        """upgrade() must call atomic_write, not yaml_file.write_text."""
        import autoskillit.cli._marketplace as _mkt
        import autoskillit.core as _core

        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "scripts"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "test.yaml").write_text("inputs:\n  foo: bar\n")

        atomic_calls: list[tuple] = []
        original = _core.atomic_write

        def capture(path, content):
            atomic_calls.append((path, content))
            return original(path, content)

        monkeypatch.setattr(_mkt, "atomic_write", capture)
        from autoskillit.cli._marketplace import upgrade

        upgrade()

        assert len(atomic_calls) == 1, "Expected exactly one atomic_write call"
        _, content = atomic_calls[0]
        assert "ingredients:" in content
        assert "inputs:" not in content

    def test_quota_status_subcommand_outputs_json(self, monkeypatch, capsys, tmp_path):
        """quota-status must emit JSON with required keys."""

        async def _mock_check(config):
            return {"should_sleep": False, "sleep_seconds": 0, "utilization": 45.0}

        monkeypatch.setattr("autoskillit.execution.check_and_sleep_if_needed", _mock_check)
        monkeypatch.chdir(tmp_path)
        cli.quota_status()
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "should_sleep" in data
        assert "sleep_seconds" in data

    def test_quota_hook_script_exists(self):
        """The hook script must be present as a runnable module in the installed package."""
        from pathlib import Path

        import autoskillit

        pkg_dir = Path(autoskillit.__file__).parent
        hook_script = pkg_dir / "hooks" / "guards" / "quota_guard.py"
        assert hook_script.exists(), f"Expected hook script at {hook_script}"

    def test_generate_hooks_json_includes_quota_hook(self):
        """generate_hooks_json() must include quota_guard.py in PreToolUse and pretty_output_hook.py in PostToolUse."""  # noqa: E501
        from autoskillit.hook_registry import generate_hooks_json

        data = generate_hooks_json()
        pretooluse_commands = [
            hook["command"] for entry in data["hooks"]["PreToolUse"] for hook in entry["hooks"]
        ]
        assert any("quota_guard" in cmd for cmd in pretooluse_commands)
        assert "PostToolUse" in data["hooks"]
        posttooluse_commands = [
            hook["command"] for entry in data["hooks"]["PostToolUse"] for hook in entry["hooks"]
        ]
        assert any("pretty_output_hook" in cmd for cmd in posttooluse_commands)

    def test_install_writes_pretooluse_hooks(self, tmp_path, monkeypatch):
        """install must register the quota PreToolUse hook in .claude/settings.json."""

        settings_path = tmp_path / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True)

        # monkeypatch via the actual module object — string path resolves to the App object
        # due to autoskillit.cli.__init__.py re-exporting `app = App(...)` as attribute `app`
        app_module = importlib.import_module("autoskillit.cli._hooks")
        monkeypatch.setattr(app_module, "_claude_settings_path", lambda scope: settings_path)
        monkeypatch.setattr("subprocess.run", lambda *a, **kw: type("R", (), {"returncode": 0})())
        monkeypatch.setattr("shutil.which", lambda cmd: f"/usr/bin/{cmd}")
        # Clear CLAUDECODE env var so install doesn't short-circuit with the early-return path
        monkeypatch.delenv("CLAUDECODE", raising=False)
        import importlib as _importlib

        _app_mod = _importlib.import_module("autoskillit.cli._marketplace")
        monkeypatch.setattr(_app_mod, "is_git_worktree", lambda path: False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        from autoskillit.cli._marketplace import install

        install(scope="local")

        data = json.loads(settings_path.read_text())
        hooks = data.get("hooks", {})
        pretooluse = hooks.get("PreToolUse", [])
        matchers = [h.get("matcher", "") for h in pretooluse]
        assert any("run_skill" in m for m in matchers), (
            "PreToolUse hook for run_skill not found in settings.json"
        )

    def test_remove_clone_guard_script_exists(self):
        """The remove_clone_guard hook script must be present as a runnable module."""
        import autoskillit

        pkg_dir = Path(autoskillit.__file__).parent
        hook_script = pkg_dir / "hooks" / "guards" / "remove_clone_guard.py"
        assert hook_script.exists(), f"Expected hook script at {hook_script}"

    def test_install_registers_remove_clone_guard_hook(self, tmp_path, monkeypatch):
        """install must register the remove_clone_guard PreToolUse hook in settings.json."""

        settings_path = tmp_path / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True)

        app_module = importlib.import_module("autoskillit.cli._hooks")
        monkeypatch.setattr(app_module, "_claude_settings_path", lambda scope: settings_path)
        monkeypatch.setattr("subprocess.run", lambda *a, **kw: type("R", (), {"returncode": 0})())
        monkeypatch.setattr("shutil.which", lambda cmd: f"/usr/bin/{cmd}")
        monkeypatch.delenv("CLAUDECODE", raising=False)

        _app_mod = importlib.import_module("autoskillit.cli._marketplace")
        monkeypatch.setattr(_app_mod, "is_git_worktree", lambda path: False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        from autoskillit.cli._marketplace import install

        install(scope="local")

        data = json.loads(settings_path.read_text())
        hooks = data.get("hooks", {})
        pretooluse = hooks.get("PreToolUse", [])
        matchers = [h.get("matcher", "") for h in pretooluse]
        assert any("remove_clone" in m for m in matchers), (
            "PreToolUse hook for remove_clone not found in settings.json"
        )

    def test_install_remove_clone_guard_hook_idempotent(self, tmp_path, monkeypatch):
        """Running install twice must not duplicate the remove_clone_guard hook entry."""

        settings_path = tmp_path / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True)

        app_module = importlib.import_module("autoskillit.cli._hooks")
        monkeypatch.setattr(app_module, "_claude_settings_path", lambda scope: settings_path)
        monkeypatch.setattr("subprocess.run", lambda *a, **kw: type("R", (), {"returncode": 0})())
        monkeypatch.setattr("shutil.which", lambda cmd: f"/usr/bin/{cmd}")
        monkeypatch.delenv("CLAUDECODE", raising=False)

        _app_mod = importlib.import_module("autoskillit.cli._marketplace")
        monkeypatch.setattr(_app_mod, "is_git_worktree", lambda path: False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        from autoskillit.cli._marketplace import install

        install(scope="local")
        install(scope="local")

        data = json.loads(settings_path.read_text())
        pretooluse = data.get("hooks", {}).get("PreToolUse", [])
        remove_clone_entries = [h for h in pretooluse if "remove_clone" in h.get("matcher", "")]
        assert len(remove_clone_entries) == 1, (
            f"Expected exactly 1 remove_clone hook entry, got {len(remove_clone_entries)}"
        )


def test_clear_plugin_cache_removes_nested_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_clear_plugin_cache must remove the entry from data['plugins'], not top-level data.

    With the retiring-cache feature, old version directories survive under a grace
    period instead of being immediately deleted.
    """
    from autoskillit.cli._marketplace import _clear_plugin_cache

    plugins_dir = tmp_path / ".claude" / "plugins"
    plugins_dir.mkdir(parents=True)
    installed_json = plugins_dir / "installed_plugins.json"
    installed_json.write_text(
        json.dumps(
            {
                "version": 2,
                "plugins": {"autoskillit@autoskillit-local": {"name": "autoskillit"}},
            }
        )
    )
    # Simulate an old version directory in the plugin cache
    cache_dir = tmp_path / ".claude" / "plugins" / "cache" / "autoskillit-local" / "autoskillit"
    old_version_dir = cache_dir / "0.9.0"
    old_version_dir.mkdir(parents=True)
    # Ensure the running __version__ differs from "0.9.0" so retirement applies
    import autoskillit as _pkg

    monkeypatch.setattr(_pkg, "__version__", "0.9.99-test")

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    _clear_plugin_cache()

    data = json.loads(installed_json.read_text())
    assert "autoskillit@autoskillit-local" not in data.get("plugins", {})
    assert data["version"] == 2  # other keys preserved
    # Old version dir survives under grace period
    assert old_version_dir.exists(), "Old version dir must survive under grace period"
    # Retiring registry must record the old version
    retiring_json = tmp_path / ".autoskillit" / "retiring_cache.json"
    assert retiring_json.exists(), "retiring_cache.json must be created"
    retiring_data = json.loads(retiring_json.read_text())
    assert any(e["version"] == "0.9.0" for e in retiring_data["retiring"])


def test_clear_plugin_cache_noop_when_entry_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_clear_plugin_cache must not raise when the entry is already absent."""
    from autoskillit.cli._marketplace import _clear_plugin_cache

    plugins_dir = tmp_path / ".claude" / "plugins"
    plugins_dir.mkdir(parents=True)
    installed_json = plugins_dir / "installed_plugins.json"
    installed_json.write_text(json.dumps({"version": 2, "plugins": {}}))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    _clear_plugin_cache()  # must not raise
    data = json.loads(installed_json.read_text())
    assert data == {"version": 2, "plugins": {}}


def test_install_claudecode_guard_returns_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """install() must return False when CLAUDECODE guard fires, not None-as-success."""
    import importlib as _importlib

    from autoskillit.cli._marketplace import install as _install

    _app_mod = _importlib.import_module("autoskillit.cli._marketplace")
    monkeypatch.setattr(_app_mod, "is_git_worktree", lambda path: False)
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    result = _install(scope="user")
    assert result is False, f"Expected False, got {result!r}"


def test_install_claudecode_guard_does_not_print_next_steps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """app.install() must not print Next Steps when CLAUDECODE guard fires."""

    import autoskillit.cli._init_helpers as _init_helpers_mod
    import autoskillit.cli._marketplace as _mkt_mod

    next_steps_called: list[dict] = []
    monkeypatch.setattr(
        _init_helpers_mod, "_print_next_steps", lambda **kw: next_steps_called.append(kw)
    )
    monkeypatch.setattr(_mkt_mod, "install", lambda **kw: False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    from autoskillit.cli.app import install as app_install

    app_install(scope="user")
    assert not next_steps_called, (
        "_print_next_steps must not be called when install() returns False"
    )


def test_install_sweeps_all_scopes_for_orphans(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """install() evicts orphaned autoskillit hooks from non-target scopes."""
    import json as _json

    from autoskillit.cli._hooks import sweep_all_scopes_for_orphans

    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()

    project_settings = project / ".claude" / "settings.json"
    project_settings.parent.mkdir(parents=True)
    project_settings.write_text(
        _json.dumps(
            {
                "hooks": {
                    "PostToolUse": [
                        {
                            "matcher": "mcp__.*autoskillit.*",
                            "hooks": [
                                {"type": "command", "command": "python3 /stale/pretty_output.py"}
                            ],
                        }
                    ]
                }
            }
        )
    )

    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.chdir(project)

    sweep_all_scopes_for_orphans(project)

    data = _json.loads(project_settings.read_text())
    for event_hooks in data.get("hooks", {}).values():
        for entry in event_hooks:
            for hook in entry.get("hooks", []):
                assert "pretty_output.py" not in hook["command"], (
                    "Orphaned hook was not evicted from project scope"
                )


def test_install_creates_autoskillit_gitignore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After install(), .autoskillit/.gitignore must exist (ensure_project_temp was called)."""
    import importlib as _importlib

    from autoskillit.cli._marketplace import install as _install

    _app_mod = _importlib.import_module("autoskillit.cli._marketplace")
    monkeypatch.setattr(_app_mod, "is_git_worktree", lambda path: False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(Path, "cwd", lambda: tmp_path)
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/claude")
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **kw: type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})(),
    )
    monkeypatch.setattr("autoskillit.cli._marketplace.evict_direct_mcp_entry", lambda _: False)
    monkeypatch.setattr(
        "autoskillit.cli._marketplace.sweep_all_scopes_for_orphans", lambda _: None
    )
    monkeypatch.setattr("autoskillit.cli._marketplace.sync_hooks_to_settings", lambda _: None)
    monkeypatch.setattr("autoskillit.cli._marketplace.generate_hooks_json", lambda: {})
    monkeypatch.setattr("autoskillit.cli._marketplace.atomic_write", lambda *a, **kw: None)

    (tmp_path / ".autoskillit").mkdir()
    _install(scope="user")

    assert (tmp_path / ".autoskillit" / ".gitignore").exists(), (
        ".autoskillit/.gitignore must be created by install(), not just by init()"
    )


def test_install_calls_upgrade_when_scripts_dir_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """install() must migrate .autoskillit/scripts/ → .autoskillit/recipes/ if scripts/ exists."""
    import importlib as _importlib

    from autoskillit.cli._marketplace import install as _install

    _app_mod = _importlib.import_module("autoskillit.cli._marketplace")
    monkeypatch.setattr(_app_mod, "is_git_worktree", lambda path: False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(Path, "cwd", lambda: tmp_path)
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/claude")
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **kw: type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})(),
    )
    monkeypatch.setattr("autoskillit.cli._marketplace.evict_direct_mcp_entry", lambda _: False)
    monkeypatch.setattr(
        "autoskillit.cli._marketplace.sweep_all_scopes_for_orphans", lambda _: None
    )
    monkeypatch.setattr("autoskillit.cli._marketplace.sync_hooks_to_settings", lambda _: None)
    monkeypatch.setattr("autoskillit.cli._marketplace.generate_hooks_json", lambda: {})
    monkeypatch.setattr("autoskillit.cli._marketplace.atomic_write", lambda *a, **kw: None)

    scripts_dir = tmp_path / ".autoskillit" / "scripts"
    scripts_dir.mkdir(parents=True)

    _install(scope="user")

    assert (tmp_path / ".autoskillit" / "recipes").exists(), (
        "install() must migrate scripts/ to recipes/ when scripts/ exists"
    )
    assert not scripts_dir.exists(), "scripts/ must be renamed away"
