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


class TestCLIInstall:
    def test_install_validates_scope(self, capsys: pytest.CaptureFixture) -> None:
        """install rejects invalid scope values."""
        with pytest.raises(SystemExit) as exc_info:
            cli.install(scope="invalid")
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
        _marketplace_mod = importlib.import_module("autoskillit.cli._marketplace")
        monkeypatch.setattr(_marketplace_mod, "is_git_worktree", lambda path: False)
        with pytest.raises(SystemExit) as exc_info:
            cli.install()
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "claude plugin marketplace add" in captured.out

    def test_install_creates_marketplace_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """install creates the marketplace directory structure."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        _marketplace_mod = importlib.import_module("autoskillit.cli._marketplace")
        monkeypatch.setattr(_marketplace_mod, "is_git_worktree", lambda path: False)
        marketplace_dir = cli._ensure_marketplace()
        assert (marketplace_dir / ".claude-plugin" / "marketplace.json").is_file()
        assert (marketplace_dir / "plugins" / "autoskillit").is_symlink()

    def test_install_symlink_target_is_independent_of_test_file_location(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Symlink target verified using importlib.resources, not __file__ depth-counting."""
        import importlib.resources as ir

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        _marketplace_mod = importlib.import_module("autoskillit.cli._marketplace")
        monkeypatch.setattr(_marketplace_mod, "is_git_worktree", lambda path: False)
        marketplace_dir = cli._ensure_marketplace()
        link = marketplace_dir / "plugins" / "autoskillit"
        expected = Path(ir.files("autoskillit"))
        assert link.resolve() == expected.resolve()

    def test_install_marketplace_json_content(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Marketplace manifest has correct structure and plugin name."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        _marketplace_mod = importlib.import_module("autoskillit.cli._marketplace")
        monkeypatch.setattr(_marketplace_mod, "is_git_worktree", lambda path: False)
        marketplace_dir = cli._ensure_marketplace()
        data = json.loads((marketplace_dir / ".claude-plugin" / "marketplace.json").read_text())
        assert data["name"] == "autoskillit-local"
        assert len(data["plugins"]) == 1
        assert data["plugins"][0]["name"] == "autoskillit"
        assert data["plugins"][0]["source"] == "./plugins/autoskillit"

    @patch("autoskillit.cli.subprocess.run")
    def test_install_calls_claude_cli(
        self, mock_run: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """install calls claude plugin marketplace add + claude plugin install."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/claude")
        monkeypatch.delenv("CLAUDECODE", raising=False)
        _marketplace_mod = importlib.import_module("autoskillit.cli._marketplace")
        monkeypatch.setattr(_marketplace_mod, "is_git_worktree", lambda path: False)
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )

        cli.install(scope="user")

        assert mock_run.call_count == 2
        marketplace_call = mock_run.call_args_list[0]
        install_call = mock_run.call_args_list[1]
        assert "marketplace" in marketplace_call[0][0]
        assert "add" in marketplace_call[0][0]
        assert "install" in install_call[0][0]
        assert "autoskillit@autoskillit-local" in install_call[0][0]
        assert "--scope" in install_call[0][0]
        assert "user" in install_call[0][0]

    @patch("autoskillit.cli.subprocess.run")
    def test_install_passes_scope_to_claude(
        self, mock_run: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """install forwards the scope argument to claude plugin install."""
        _marketplace_mod = importlib.import_module("autoskillit.cli._marketplace")

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/claude")
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.setattr(_marketplace_mod, "is_git_worktree", lambda path: False)
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )

        cli.install(scope="project")

        install_call = mock_run.call_args_list[1][0][0]
        scope_idx = install_call.index("--scope")
        assert install_call[scope_idx + 1] == "project"

    @patch("autoskillit.cli.subprocess.run")
    def test_install_idempotent_marketplace(
        self, mock_run: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Running install twice recreates the symlink without error."""
        _marketplace_mod = importlib.import_module("autoskillit.cli._marketplace")

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/claude")
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.setattr(_marketplace_mod, "is_git_worktree", lambda path: False)
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )

        cli.install()
        cli.install()  # second run should not fail

        assert (tmp_path / ".autoskillit" / "marketplace" / "plugins" / "autoskillit").is_symlink()


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
        _marketplace_mod = importlib.import_module("autoskillit.cli._marketplace")
        monkeypatch.setattr(_marketplace_mod, "is_git_worktree", lambda path: True)

        with pytest.raises(SystemExit, match="worktree"):
            cli._ensure_marketplace()

    def test_ensure_marketplace_succeeds_in_main_checkout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_ensure_marketplace() succeeds when is_git_worktree() returns False."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        _marketplace_mod = importlib.import_module("autoskillit.cli._marketplace")
        monkeypatch.setattr(_marketplace_mod, "is_git_worktree", lambda path: False)

        result = cli._ensure_marketplace()
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

        if is_git_worktree(pkg_root()):
            pytest.skip("Cannot verify non-worktree install from a worktree environment")

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        marketplace_dir = cli._ensure_marketplace()
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
        """upgrade() must call _atomic_write, not yaml_file.write_text."""
        import autoskillit.core as _core
        import autoskillit.cli._marketplace as _marketplace_mod

        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "scripts"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "test.yaml").write_text("inputs:\n  foo: bar\n")

        atomic_calls: list[tuple] = []
        original = _core._atomic_write

        def capture(path, content):
            atomic_calls.append((path, content))
            return original(path, content)

        monkeypatch.setattr(_marketplace_mod, "_atomic_write", capture)
        cli.upgrade()

        assert len(atomic_calls) == 1, "Expected exactly one _atomic_write call"
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
        hook_script = pkg_dir / "hooks" / "quota_check.py"
        assert hook_script.exists(), f"Expected hook script at {hook_script}"

    def test_quota_hook_json_exists(self):
        """The plugin hooks.json must be present for automatic hook registration."""
        from pathlib import Path

        import autoskillit

        pkg_dir = Path(autoskillit.__file__).parent
        hooks_json = pkg_dir / "hooks" / "hooks.json"
        assert hooks_json.exists(), f"Expected hooks.json at {hooks_json}"

    def test_install_writes_pretooluse_hooks(self, tmp_path, monkeypatch):
        """install must register the quota PreToolUse hook in .claude/settings.json."""

        settings_path = tmp_path / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True)

        _marketplace_mod = importlib.import_module("autoskillit.cli._marketplace")
        monkeypatch.setattr(_marketplace_mod, "_claude_settings_path", lambda scope: settings_path)
        monkeypatch.setattr("subprocess.run", lambda *a, **kw: type("R", (), {"returncode": 0})())
        monkeypatch.setattr("shutil.which", lambda cmd: f"/usr/bin/{cmd}")
        # Clear CLAUDECODE env var so install doesn't short-circuit with the early-return path
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.setattr(_marketplace_mod, "is_git_worktree", lambda path: False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        cli.install(scope="local")

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
        hook_script = pkg_dir / "hooks" / "remove_clone_guard.py"
        assert hook_script.exists(), f"Expected hook script at {hook_script}"

    def test_install_registers_remove_clone_guard_hook(self, tmp_path, monkeypatch):
        """install must register the remove_clone_guard PreToolUse hook in settings.json."""

        settings_path = tmp_path / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True)

        _marketplace_mod = importlib.import_module("autoskillit.cli._marketplace")
        monkeypatch.setattr(_marketplace_mod, "_claude_settings_path", lambda scope: settings_path)
        monkeypatch.setattr("subprocess.run", lambda *a, **kw: type("R", (), {"returncode": 0})())
        monkeypatch.setattr("shutil.which", lambda cmd: f"/usr/bin/{cmd}")
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.setattr(_marketplace_mod, "is_git_worktree", lambda path: False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        cli.install(scope="local")

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

        _marketplace_mod = importlib.import_module("autoskillit.cli._marketplace")
        monkeypatch.setattr(_marketplace_mod, "_claude_settings_path", lambda scope: settings_path)
        monkeypatch.setattr("subprocess.run", lambda *a, **kw: type("R", (), {"returncode": 0})())
        monkeypatch.setattr("shutil.which", lambda cmd: f"/usr/bin/{cmd}")
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.setattr(_marketplace_mod, "is_git_worktree", lambda path: False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        cli.install(scope="local")
        cli.install(scope="local")

        data = json.loads(settings_path.read_text())
        pretooluse = data.get("hooks", {}).get("PreToolUse", [])
        remove_clone_entries = [h for h in pretooluse if "remove_clone" in h.get("matcher", "")]
        assert len(remove_clone_entries) == 1, (
            f"Expected exactly 1 remove_clone hook entry, got {len(remove_clone_entries)}"
        )
