"""Tests for CLI commands."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from autoskillit import cli


class TestCLI:
    # CL1
    def test_default_command_starts_server(self) -> None:
        mock_mcp = MagicMock()
        with patch.object(cli, "serve", wraps=cli.serve):
            with (
                patch("autoskillit.server.mcp", mock_mcp),
                patch("autoskillit.core.configure_logging"),
            ):
                cli.serve()
        mock_mcp.run.assert_called_once()

    # CL3
    def test_init_creates_config_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        cli.init(test_command="pytest -v")
        assert (tmp_path / ".autoskillit").is_dir()

    # CL4
    def test_init_writes_config_yaml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        cli.init(test_command="pytest -v")
        config_path = tmp_path / ".autoskillit" / "config.yaml"
        assert config_path.is_file()
        data = yaml.safe_load(config_path.read_text())
        assert data["test_check"]["command"] == ["pytest", "-v"]
        assert data["safety"]["reset_guard_marker"] == ".autoskillit-workspace"

    # CL5
    def test_init_interactive_prompts_for_test_command(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        with patch("autoskillit.cli.app._prompt_test_command", return_value=["npm", "test"]):
            cli.init()
        config_path = tmp_path / ".autoskillit" / "config.yaml"
        data = yaml.safe_load(config_path.read_text())
        assert data["test_check"]["command"] == ["npm", "test"]

    # CL6
    def test_init_no_overwrite_without_force(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        monkeypatch.chdir(tmp_path)
        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        config_path = config_dir / "config.yaml"
        config_path.write_text("original: true\n")

        cli.init(force=False)

        assert config_path.read_text() == "original: true\n"
        captured = capsys.readouterr()
        assert "already exists" in captured.out

    # CL7
    def test_config_show_outputs_json(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        monkeypatch.chdir(tmp_path)
        cli.config_show()
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "test_check" in data
        assert "safety" in data

    # CL8
    def test_unknown_command_exits_nonzero(self) -> None:
        with pytest.raises(SystemExit) as exc_info:
            with patch("sys.argv", ["autoskillit", "nonexistent"]):
                cli.main()
        assert exc_info.value.code != 0

    def test_init_force_overwrites(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        config_path = config_dir / "config.yaml"
        config_path.write_text("old: true\n")

        cli.init(test_command="pytest -v", force=True)

        data = yaml.safe_load(config_path.read_text())
        assert data["test_check"]["command"] == ["pytest", "-v"]

    def test_generate_config_yaml_contains_test_command(self) -> None:
        """_generate_config_yaml embeds the test command in active YAML."""
        from autoskillit.cli import _generate_config_yaml

        yaml_str = _generate_config_yaml(["pytest", "-v"])
        assert 'command: ["pytest", "-v"]' in yaml_str

    def test_generate_config_yaml_has_commented_advanced_sections(self) -> None:
        """Generated YAML includes commented-out advanced config sections."""
        from autoskillit.cli import _generate_config_yaml

        yaml_str = _generate_config_yaml(["pytest", "-v"])
        assert "# classify_fix:" in yaml_str
        assert "# reset_workspace:" in yaml_str
        assert "# implement_gate:" in yaml_str

    def test_generate_config_yaml_uncommented_parts_are_valid(self) -> None:
        """The uncommented portion of generated YAML parses as valid config."""
        from autoskillit.cli import _generate_config_yaml

        yaml_str = _generate_config_yaml(["task", "test-all"])
        parsed = yaml.safe_load(yaml_str)
        assert parsed["test_check"]["command"] == ["task", "test-all"]
        assert parsed["safety"]["reset_guard_marker"] == ".autoskillit-workspace"

    def test_init_writes_template_with_comments(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """init writes a config file containing commented advanced sections."""
        monkeypatch.chdir(tmp_path)
        cli.init(test_command="pytest -v")

        config_path = tmp_path / ".autoskillit" / "config.yaml"
        content = config_path.read_text()
        assert "# classify_fix:" in content
        assert "test_check:" in content

    def test_init_test_command_with_force(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--test-command combined with --force overwrites existing config."""
        monkeypatch.chdir(tmp_path)
        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text("old: true\n")

        cli.init(test_command="npm test", force=True)

        data = yaml.safe_load((config_dir / "config.yaml").read_text())
        assert data["test_check"]["command"] == ["npm", "test"]

    def test_init_idempotent_rerun(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Running init twice is safe — config preserved on second run."""
        monkeypatch.chdir(tmp_path)
        cli.init(test_command="pytest -v")
        config_before = (tmp_path / ".autoskillit" / "config.yaml").read_text()
        # Re-run init — should not overwrite without --force
        cli.init(test_command="pytest -v")
        assert (tmp_path / ".autoskillit" / "config.yaml").read_text() == config_before

    # --- workspace init ---

    def test_prep_station_init_creates_dir_with_marker(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """prep station init creates directory and drops marker file."""
        monkeypatch.chdir(tmp_path)
        target = tmp_path / "test-workspace"
        cli.workspace_init(str(target))
        assert target.is_dir()
        assert (target / ".autoskillit-workspace").is_file()

    def test_prep_station_init_refuses_nonempty_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """prep station init refuses to initialize a non-empty directory."""
        monkeypatch.chdir(tmp_path)
        target = tmp_path / "existing"
        target.mkdir()
        (target / "important.txt").touch()
        with pytest.raises(SystemExit):
            cli.workspace_init(str(target))

    def test_prep_station_init_idempotent_on_empty_with_marker(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """prep station init is safe to re-run on a directory that only has the marker."""
        monkeypatch.chdir(tmp_path)
        target = tmp_path / "prep-station"
        cli.workspace_init(str(target))
        cli.workspace_init(str(target))  # second run — should not fail
        assert (target / ".autoskillit-workspace").is_file()

    def test_prep_station_init_marker_has_content(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Marker file contains human-readable identifying content."""
        monkeypatch.chdir(tmp_path)
        target = tmp_path / "prep-station"
        cli.workspace_init(str(target))
        content = (target / ".autoskillit-workspace").read_text()
        assert "autoskillit" in content
        assert "do not delete" in content

    # --- T_WC: workspace clean ---

    def test_workspace_clean_removes_subdirs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """T_WC1: workspace_clean removes all subdirs of autoskillit-runs/."""
        runs_dir = tmp_path / "autoskillit-runs"
        (runs_dir / "run-a").mkdir(parents=True)
        (runs_dir / "run-b").mkdir(parents=True)
        cli.workspace_clean(dir=str(tmp_path))
        assert not (runs_dir / "run-a").exists()
        assert not (runs_dir / "run-b").exists()

    def test_workspace_clean_reports_nothing_when_no_runs_dir(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """T_WC2: workspace_clean prints message when autoskillit-runs/ doesn't exist."""
        cli.workspace_clean(dir=str(tmp_path))
        captured = capsys.readouterr()
        assert "No autoskillit-runs/" in captured.out

    def test_workspace_clean_defaults_to_parent_of_cwd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """T_WC3: workspace_clean without --dir uses parent of CWD."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        monkeypatch.chdir(project_dir)
        runs_dir = tmp_path / "autoskillit-runs"
        (runs_dir / "run-x").mkdir(parents=True)
        cli.workspace_clean()
        assert not (runs_dir / "run-x").exists()

    # --- T6: skills list CLI output ---

    def test_skills_list_shows_all_sources(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """skills list outputs skill names with source labels."""
        monkeypatch.chdir(tmp_path)
        cli.skills_list()
        captured = capsys.readouterr()
        assert "investigate" in captured.out
        assert "bundled" in captured.out
        assert "NAME" in captured.out

    # --- T7: doctor ---

    def test_doctor_warns_dead_binary(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """doctor warns about MCP servers with nonexistent command binaries."""
        fake_claude_json = tmp_path / ".claude.json"
        fake_claude_json.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "bugfix-loop": {
                            "type": "stdio",
                            "command": "/nonexistent/path/to/old-server",
                        },
                        "autoskillit": {"type": "stdio", "command": "autoskillit"},
                    }
                }
            )
        )
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        cli.doctor()
        captured = capsys.readouterr()
        assert "bugfix-loop" in captured.out
        assert "ERROR" in captured.out

    def test_doctor_ignores_healthy_coregistered_servers(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """doctor ignores legitimate co-registered MCP servers (no standalone autoskillit)."""
        fake_claude_json = tmp_path / ".claude.json"
        fake_bin = tmp_path / "legit-server"
        fake_bin.write_text("#!/bin/sh\n")
        fake_bin.chmod(0o755)
        fake_claude_json.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "other-server": {"type": "stdio", "command": str(fake_bin)},
                    }
                }
            )
        )
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".autoskillit").mkdir(exist_ok=True)
        (tmp_path / ".autoskillit" / "config.yaml").write_text(
            "test_check:\n  command: ['pytest']\n"
        )
        with patch(
            "autoskillit.cli.shutil.which",
            side_effect=lambda cmd: (
                "/usr/local/bin/autoskillit" if cmd == "autoskillit" else shutil.which(cmd)
            ),
        ):
            cli.doctor()
        captured = capsys.readouterr()
        assert "WARNING" not in captured.out
        assert "ERROR" not in captured.out

    def test_doctor_warns_missing_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """doctor warns when no project config exists."""
        fake_claude_json = tmp_path / ".claude.json"
        fake_claude_json.write_text(
            json.dumps(
                {"mcpServers": {"autoskillit": {"type": "stdio", "command": "autoskillit"}}}
            )
        )
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        cli.doctor()
        captured = capsys.readouterr()
        assert "No project config" in captured.out

    def test_doctor_json_output(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """doctor --json outputs structured results JSON."""
        fake_claude_json = tmp_path / ".claude.json"
        fake_claude_json.write_text(
            json.dumps(
                {"mcpServers": {"autoskillit": {"type": "stdio", "command": "autoskillit"}}}
            )
        )
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        cli.doctor(output_json=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "results" in data
        assert isinstance(data["results"], list)
        for entry in data["results"]:
            assert "severity" in entry
            assert "check" in entry
            assert "message" in entry

    def test_doctor_result_has_severity_tiers(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """doctor JSON output uses ok/warning/error severity tiers."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        cli.doctor(output_json=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        severities = {r["severity"] for r in data["results"]}
        assert severities <= {"ok", "warning", "error"}

    def test_doctor_warns_version_mismatch(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
        tool_ctx,
    ) -> None:
        """doctor reports error when plugin.json version differs from package."""
        plugin_dir = tmp_path / "fake_plugin" / ".claude-plugin"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "plugin.json").write_text(
            json.dumps({"name": "autoskillit", "version": "0.0.0"})
        )
        tool_ctx.plugin_dir = str(tmp_path / "fake_plugin")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        cli.doctor(output_json=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        version_checks = [r for r in data["results"] if r["check"] == "version_consistency"]
        assert len(version_checks) == 1
        assert version_checks[0]["severity"] == "error"

    def test_doctor_passes_when_versions_match(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """doctor reports ok when plugin.json version matches package."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        cli.doctor(output_json=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        version_checks = [r for r in data["results"] if r["check"] == "version_consistency"]
        assert len(version_checks) == 1
        assert version_checks[0]["severity"] == "ok"

    def test_doctor_warns_marketplace_staleness(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """doctor warns when marketplace manifest has stale version."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)

        mkt_dir = tmp_path / ".autoskillit" / "marketplace"
        plugin_dir = mkt_dir / ".claude-plugin"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "marketplace.json").write_text(
            json.dumps(
                {"plugins": [{"name": "autoskillit", "version": "0.0.0-stale", "source": "."}]}
            )
        )
        link_dir = mkt_dir / "plugins"
        link_dir.mkdir(parents=True)
        link = link_dir / "autoskillit"
        # Use tmp_path subdirectory as target — no .git ancestor, so not a worktree.
        fake_pkg = tmp_path / "fake_pkg"
        fake_pkg.mkdir()
        link.symlink_to(fake_pkg)

        cli.doctor(output_json=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        mkt_checks = [r for r in data["results"] if r["check"] == "marketplace_freshness"]
        assert len(mkt_checks) == 1
        assert mkt_checks[0]["severity"] == "warning"

    def test_doctor_marketplace_freshness_fails_for_worktree_target(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """marketplace_freshness check detects and reports a worktree symlink target."""
        from autoskillit import __version__

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)

        marketplace_dir = tmp_path / ".autoskillit" / "marketplace"
        link = marketplace_dir / "plugins" / "autoskillit"
        link.parent.mkdir(parents=True)

        # Create a fake worktree target: a package dir with a .git FILE ancestor.
        fake_worktree_pkg = tmp_path / "worktrees" / "some-wt" / "src" / "autoskillit"
        fake_worktree_pkg.mkdir(parents=True)
        (fake_worktree_pkg.parent.parent.parent / ".git").write_text(
            "gitdir: /main/.git/worktrees/some-wt\n"
        )
        link.symlink_to(fake_worktree_pkg)

        # Write a valid marketplace.json with the current version so version check passes
        plugin_dir = marketplace_dir / ".claude-plugin"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "marketplace.json").write_text(
            json.dumps(
                {"plugins": [{"name": "autoskillit", "version": __version__, "source": "."}]}
            )
        )

        cli.doctor(output_json=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        mkt_checks = [r for r in data["results"] if r["check"] == "marketplace_freshness"]
        assert len(mkt_checks) == 1
        assert mkt_checks[0]["severity"] == "error"
        assert "worktree" in mkt_checks[0]["message"].lower()

    def test_doctor_json_output_includes_all_checks(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """doctor JSON includes entries for all core check names."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        cli.doctor(output_json=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        check_names = {r["check"] for r in data["results"]}
        expected = {
            "stale_mcp_servers",
            "duplicate_mcp_server",
            "plugin_metadata",
            "autoskillit_on_path",
            "project_config",
            "version_consistency",
            "script_version_health",
        }
        assert expected <= check_names

    def test_doctor_human_output_shows_severity(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
        tool_ctx,
    ) -> None:
        """doctor human output includes severity prefixes for problems."""
        plugin_dir = tmp_path / "fake_plugin" / ".claude-plugin"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "plugin.json").write_text(
            json.dumps({"name": "autoskillit", "version": "0.0.0"})
        )
        tool_ctx.plugin_dir = str(tmp_path / "fake_plugin")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        cli.doctor()
        captured = capsys.readouterr()
        assert "ERROR:" in captured.out

    def test_doctor_detects_duplicate_with_plugin_installed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """doctor errors when standalone MCP entry exists alongside plugin installation."""
        # Standalone entry in ~/.claude.json
        fake_claude_json = tmp_path / ".claude.json"
        fake_claude_json.write_text(
            json.dumps(
                {"mcpServers": {"autoskillit": {"type": "stdio", "command": "autoskillit"}}}
            )
        )
        # Plugin enabled in ~/.claude/settings.json
        settings_dir = tmp_path / ".claude"
        settings_dir.mkdir(parents=True)
        (settings_dir / "settings.json").write_text(
            json.dumps({"enabledPlugins": {"autoskillit@autoskillit-local": True}})
        )
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        cli.doctor(output_json=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        dup_checks = [r for r in data["results"] if r["check"] == "duplicate_mcp_server"]
        assert len(dup_checks) == 1
        assert dup_checks[0]["severity"] == "error"
        assert "duplicate" in dup_checks[0]["message"].lower()

    def test_doctor_warns_standalone_without_plugin(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """doctor warns when standalone entry exists but no plugin is installed."""
        fake_claude_json = tmp_path / ".claude.json"
        fake_claude_json.write_text(
            json.dumps(
                {"mcpServers": {"autoskillit": {"type": "stdio", "command": "autoskillit"}}}
            )
        )
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        cli.doctor(output_json=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        dup_checks = [r for r in data["results"] if r["check"] == "duplicate_mcp_server"]
        assert len(dup_checks) == 1
        assert dup_checks[0]["severity"] == "warning"

    # --- install ---

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
        import importlib as _importlib

        _app_mod = _importlib.import_module("autoskillit.cli.app")
        monkeypatch.setattr(_app_mod, "is_git_worktree", lambda path: False)
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
        import importlib as _importlib

        _app_mod = _importlib.import_module("autoskillit.cli.app")
        monkeypatch.setattr(_app_mod, "is_git_worktree", lambda path: False)
        marketplace_dir = cli._ensure_marketplace()
        assert (marketplace_dir / ".claude-plugin" / "marketplace.json").is_file()
        assert (marketplace_dir / "plugins" / "autoskillit").is_symlink()

    def test_install_symlink_target_is_independent_of_test_file_location(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Symlink target verified using importlib.resources, not __file__ depth-counting."""
        import importlib.resources as ir

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        import importlib as _importlib

        _app_mod = _importlib.import_module("autoskillit.cli.app")
        monkeypatch.setattr(_app_mod, "is_git_worktree", lambda path: False)
        marketplace_dir = cli._ensure_marketplace()
        link = marketplace_dir / "plugins" / "autoskillit"
        expected = Path(ir.files("autoskillit"))
        assert link.resolve() == expected.resolve()

    def test_install_marketplace_json_content(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Marketplace manifest has correct structure and plugin name."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        import importlib as _importlib

        _app_mod = _importlib.import_module("autoskillit.cli.app")
        monkeypatch.setattr(_app_mod, "is_git_worktree", lambda path: False)
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
        import importlib as _importlib

        _app_mod = _importlib.import_module("autoskillit.cli.app")
        monkeypatch.setattr(_app_mod, "is_git_worktree", lambda path: False)
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
        import importlib as _importlib

        _app_mod = _importlib.import_module("autoskillit.cli.app")

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/claude")
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.setattr(_app_mod, "is_git_worktree", lambda path: False)
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
        import importlib as _importlib

        _app_mod = _importlib.import_module("autoskillit.cli.app")

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/claude")
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.setattr(_app_mod, "is_git_worktree", lambda path: False)
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )

        cli.install()
        cli.install()  # second run should not fail

        assert (tmp_path / ".autoskillit" / "marketplace" / "plugins" / "autoskillit").is_symlink()

    # --- cook ---

    _SCRIPT_YAML = """\
name: test-script
description: A test script
summary: Test flow
ingredients:
  target:
    description: Target path
    required: true
steps:
  do-something:
    tool: run_cmd
    with:
      cmd: echo hello
    on_success: done
    on_failure: done
  done:
    action: stop
    message: Finished
kitchen_rules:
  - Only use AutoSkillit MCP tools during pipeline execution
"""

    def test_cook_blocked_inside_claude_session(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """cook exits 1 when CLAUDECODE env var is set."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("CLAUDECODE", "1")
        with pytest.raises(SystemExit) as exc_info:
            cli.cook("any-script")
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "regular terminal" in captured.out.lower()

    def test_cook_script_not_found(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """cook exits 1 when script name doesn't match any entry."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)

        with pytest.raises(SystemExit) as exc_info:
            cli.cook("nonexistent")
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "nonexistent" in captured.out

    def test_cook_no_scripts_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """cook exits 1 with available bundled recipes listed when name not found."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)

        with pytest.raises(SystemExit) as exc_info:
            cli.cook("anything")
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "Recipe not found: 'anything'" in captured.out
        assert "Available recipes:" in captured.out
        assert "implementation-pipeline" in captured.out

    def test_cook_available_scripts_listed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """cook lists available scripts when name doesn't match."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "my-script.yaml").write_text(self._SCRIPT_YAML)

        with pytest.raises(SystemExit) as exc_info:
            cli.cook("nonexistent")
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "Available recipes:" in captured.out
        assert "test-script" in captured.out

    def test_cook_claude_not_on_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """cook exits 1 when claude command is not found."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "my-script.yaml").write_text(self._SCRIPT_YAML)
        monkeypatch.setattr(shutil, "which", lambda cmd: None)

        with pytest.raises(SystemExit) as exc_info:
            cli.cook("test-script")
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "claude" in captured.out.lower()

    def test_cook_invalid_script_exits(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """cook exits 1 when script YAML fails validation."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        # Script with no steps (empty mapping) — will fail validation
        (scripts_dir / "bad-script.yaml").write_text("name: bad-script\nsteps: {}\n")

        with pytest.raises(SystemExit) as exc_info:
            cli.cook("bad-script")
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "validation" in captured.out.lower() or "error" in captured.out.lower()

    @patch("autoskillit.cli.subprocess.run")
    def test_cook_builds_correct_command(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """cook passes correct flags to subprocess.run."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "my-script.yaml").write_text(self._SCRIPT_YAML)
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/claude")
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )

        cli.cook("test-script")

        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "claude"
        assert "--plugin-dir" in cmd
        plugin_dir_idx = cmd.index("--plugin-dir")
        plugin_dir_val = Path(cmd[plugin_dir_idx + 1])
        assert plugin_dir_val.is_dir()
        assert (plugin_dir_val / ".claude-plugin" / "plugin.json").is_file()
        assert "--tools" in cmd
        tools_idx = cmd.index("--tools")
        assert cmd[tools_idx + 1] == "AskUserQuestion"
        assert "--append-system-prompt" in cmd
        # Interactive: must have --allow-dangerous-permissions, no -p, no --dangerously-skip-permissions
        assert "--allow-dangerous-permissions" in cmd
        assert "-p" not in cmd
        assert "--dangerously-skip-permissions" not in cmd
        # Interactive passthrough: no capture_output, no stdin
        kwargs = mock_run.call_args[1] if mock_run.call_args[1] else {}
        assert "capture_output" not in kwargs
        assert "stdin" not in kwargs

    @patch("autoskillit.cli.subprocess.run")
    def test_cook_system_prompt_contains_script(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """cook injects script YAML and orchestrator contract into system prompt."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "my-script.yaml").write_text(self._SCRIPT_YAML)
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/claude")
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )

        cli.cook("test-script")

        cmd = mock_run.call_args[0][0]
        prompt_idx = cmd.index("--append-system-prompt")
        system_prompt = cmd[prompt_idx + 1]
        # Contains the script YAML content
        assert "test-script" in system_prompt
        assert "do-something" in system_prompt
        # Contains routing rules
        assert "ROUTING RULES" in system_prompt
        # Contains failure predicates
        assert "FAILURE PREDICATES" in system_prompt
        # Contains tool discipline block
        assert "capture:" in system_prompt
        assert "${{ context." in system_prompt
        assert "AutoSkillit MCP tools" in system_prompt
        # Mentions both plugin and --plugin-dir loading methods
        assert "--plugin-dir" in system_prompt

    @patch("autoskillit.cli.subprocess.run")
    def test_cook_propagates_exit_code(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """cook does not raise SystemExit on returncode 0."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "my-script.yaml").write_text(self._SCRIPT_YAML)
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/claude")
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )

        cli.cook("test-script")  # should not raise
        mock_run.assert_called_once()

    @patch("autoskillit.cli.subprocess.run")
    def test_cook_subprocess_failure_propagates(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """cook propagates non-zero subprocess exit codes."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "my-script.yaml").write_text(self._SCRIPT_YAML)
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/claude")
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=42, stdout="", stderr=""
        )

        with pytest.raises(SystemExit) as exc_info:
            cli.cook("test-script")
        assert exc_info.value.code == 42

    @patch("autoskillit.cli.subprocess.run")
    def test_cook_no_recipe_prompts_user(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """cook prompts for recipe name when none is provided."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "my-script.yaml").write_text(self._SCRIPT_YAML)
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/claude")
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        monkeypatch.setattr("builtins.input", lambda _prompt="": "test-script")

        cli.cook()  # no recipe argument

        mock_run.assert_called_once()

    def test_cook_no_recipe_no_available_exits(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """cook exits 1 when no recipe is given and no recipes are available."""
        from unittest.mock import MagicMock as _MagicMock

        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)

        mock_result = _MagicMock()
        mock_result.items = []
        monkeypatch.setattr("autoskillit.cli.app.list_recipes", lambda _: mock_result)

        with pytest.raises(SystemExit) as exc_info:
            cli.cook()
        assert exc_info.value.code == 1

    @patch("autoskillit.cli.subprocess.run")
    def test_cook_named_recipe_skips_prompt(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """cook does not prompt for recipe when name is provided directly."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "my-script.yaml").write_text(self._SCRIPT_YAML)
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/claude")
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )

        input_called = []
        monkeypatch.setattr("builtins.input", lambda *a, **kw: input_called.append(1) or "")

        cli.cook("test-script")

        assert not input_called, "input() must not be called when recipe name is provided"

    @patch("autoskillit.cli.subprocess.run")
    def test_cook_uses_allow_dangerous_permissions(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """cook passes --allow-dangerous-permissions to claude."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "my-script.yaml").write_text(self._SCRIPT_YAML)
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/claude")
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )

        cli.cook("test-script")

        cmd = mock_run.call_args[0][0]
        assert "--allow-dangerous-permissions" in cmd

    @patch("autoskillit.cli.subprocess.run")
    def test_cook_env_has_kitchen_open(
        self,
        mock_run: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """cook passes AUTOSKILLIT_KITCHEN_OPEN=1 in the subprocess environment."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "my-script.yaml").write_text(self._SCRIPT_YAML)
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/claude")
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )

        cli.cook("test-script")

        kwargs = mock_run.call_args[1] if mock_run.call_args[1] else {}
        assert "env" in kwargs
        assert kwargs["env"].get("AUTOSKILLIT_KITCHEN_OPEN") == "1"

    def test_cook_recipe_not_found_exits(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ) -> None:
        """cook exits 1 when the given recipe name is not found."""
        monkeypatch.delenv("CLAUDECODE", raising=False)
        monkeypatch.chdir(tmp_path)

        with pytest.raises(SystemExit) as exc_info:
            cli.cook("totally-unknown-recipe-xyz")
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "totally-unknown-recipe-xyz" in captured.out


# ---------------------------------------------------------------------------
# TestDoctorScriptHealth: doctor check for script version staleness
# ---------------------------------------------------------------------------

_MINIMAL_SCRIPT_YAML = """\
name: my-script
description: A test script
steps:
  do_it:
    tool: run_cmd
    with:
      cmd: echo hello
    on_success: done
  done:
    action: stop
    message: Done
kitchen_rules:
  - Only use AutoSkillit MCP tools during pipeline execution
"""


class TestDoctorScriptHealth:
    """Doctor check for script version staleness."""

    # DOC1: No .autoskillit/recipes/ -> OK result
    def test_no_scripts_dir_reports_ok(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """doctor reports OK for script_version_health when no scripts directory exists."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        # No .autoskillit/recipes/ directory created
        cli.doctor(output_json=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        script_checks = [r for r in data["results"] if r["check"] == "script_version_health"]
        assert len(script_checks) == 1
        assert script_checks[0]["severity"] == "ok"

    # DOC2: All scripts at current version -> OK result
    def test_all_scripts_at_current_version_reports_ok(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """doctor reports OK when all scripts carry the current installed version."""
        import autoskillit

        current_version = autoskillit.__version__
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "up-to-date.yaml").write_text(
            f"name: up-to-date\ndescription: Current version\n"
            f'autoskillit_version: "{current_version}"\n'
            + _MINIMAL_SCRIPT_YAML.split("\n", 2)[2]  # reuse steps/constraints block
        )
        cli.doctor(output_json=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        script_checks = [r for r in data["results"] if r["check"] == "script_version_health"]
        assert len(script_checks) == 1
        assert script_checks[0]["severity"] == "ok"

    # DOC3: Scripts below current version -> WARNING result with recipe names
    def test_outdated_scripts_reports_warning_with_count(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """doctor reports WARNING with recipe names when scripts have an older version."""
        import autoskillit

        monkeypatch.setattr(autoskillit, "__version__", "99.0.0")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "old-script.yaml").write_text(
            'name: old-script\ndescription: Old\nautoskillit_version: "0.1.0"\n'
        )
        (scripts_dir / "also-old.yaml").write_text(
            'name: also-old\ndescription: Also old\nautoskillit_version: "0.1.0"\n'
        )
        cli.doctor(output_json=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        script_checks = [r for r in data["results"] if r["check"] == "script_version_health"]
        assert len(script_checks) == 1
        assert script_checks[0]["severity"] == "warning"
        assert "old-script" in script_checks[0]["message"]

    # DOC4: Scripts with no version field -> WARNING result
    def test_scripts_without_version_reports_warning(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """doctor reports WARNING when script YAML has no autoskillit_version field."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "no-version.yaml").write_text(
            "name: no-version\ndescription: No version field\n"
        )
        cli.doctor(output_json=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        script_checks = [r for r in data["results"] if r["check"] == "script_version_health"]
        assert len(script_checks) == 1
        assert script_checks[0]["severity"] == "warning"

    def _setup_recipe(self, scripts_dir: Path, name: str, version: str = "0.1.0") -> None:
        (scripts_dir / f"{name}.yaml").write_text(
            f'name: {name}\ndescription: Test\nautoskillit_version: "{version}"\n'
        )

    def _write_failures_json(self, tmp_path: Path, name: str, retries: int = 3) -> None:
        import json as _json

        failures_path = tmp_path / ".autoskillit" / "temp" / "migrations" / "failures.json"
        failures_path.parent.mkdir(parents=True, exist_ok=True)
        failures_path.write_text(
            _json.dumps(
                {
                    name: {
                        "name": name,
                        "file_path": f"/fake/{name}.yaml",
                        "file_type": "recipe",
                        "timestamp": "2026-01-01T00:00:00+00:00",
                        "error": "validation failed after retries",
                        "retries_attempted": retries,
                    }
                }
            )
        )

    # DR1: failures.json has an entry for a recipe -> error severity
    def test_doctor_error_on_failed_migration(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """doctor reports error severity when failures.json has an entry for a recipe."""
        import autoskillit

        monkeypatch.setattr(autoskillit, "__version__", "99.0.0")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        self._setup_recipe(scripts_dir, "broken-pipeline")
        self._write_failures_json(tmp_path, "broken-pipeline", retries=3)
        cli.doctor(output_json=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        script_checks = [r for r in data["results"] if r["check"] == "script_version_health"]
        assert len(script_checks) == 1
        assert script_checks[0]["severity"] == "error"

    # DR2: Error message includes retries_attempted value from failure record
    def test_doctor_error_message_includes_retry_count(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """doctor error message includes retries_attempted value from the failure record."""
        import autoskillit

        monkeypatch.setattr(autoskillit, "__version__", "99.0.0")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        self._setup_recipe(scripts_dir, "my-pipeline")
        self._write_failures_json(tmp_path, "my-pipeline", retries=3)
        cli.doctor(output_json=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        script_checks = [r for r in data["results"] if r["check"] == "script_version_health"]
        assert len(script_checks) == 1
        assert "3" in script_checks[0]["message"]

    # DR3: Outdated recipe with no failure record -> warning severity
    def test_doctor_warning_on_simply_outdated(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """doctor reports warning when recipe is outdated but has no failure record."""
        import autoskillit

        monkeypatch.setattr(autoskillit, "__version__", "99.0.0")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        self._setup_recipe(scripts_dir, "outdated-pipeline")
        # No failures.json written
        cli.doctor(output_json=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        script_checks = [r for r in data["results"] if r["check"] == "script_version_health"]
        assert len(script_checks) == 1
        assert script_checks[0]["severity"] == "warning"

    # DR4: All recipes current, no failures.json -> ok
    def test_doctor_ok_when_all_current(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """doctor reports ok when all recipes are at current version and no failures.json."""
        import autoskillit

        current_version = autoskillit.__version__
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        self._setup_recipe(scripts_dir, "current-pipeline", version=current_version)
        # No failures.json written
        cli.doctor(output_json=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        script_checks = [r for r in data["results"] if r["check"] == "script_version_health"]
        assert len(script_checks) == 1
        assert script_checks[0]["severity"] == "ok"

    # DR5: Warning message says "Will be auto-migrated on next load"
    def test_doctor_outdated_message_updated(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """doctor warning message says 'Will be auto-migrated on next load'."""
        import autoskillit

        monkeypatch.setattr(autoskillit, "__version__", "99.0.0")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "recipes"
        scripts_dir.mkdir(parents=True)
        self._setup_recipe(scripts_dir, "stale-pipeline")
        cli.doctor(output_json=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        script_checks = [r for r in data["results"] if r["check"] == "script_version_health"]
        assert len(script_checks) == 1
        assert script_checks[0]["severity"] == "warning"
        assert "Will be auto-migrated on next load" in script_checks[0]["message"]


# ---------------------------------------------------------------------------
# TestMigrateCommand: migrate CLI command for reporting outdated scripts
# ---------------------------------------------------------------------------


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


class TestEnsureProjectTemp:
    """N5: ensure_project_temp moved from config.py to _io.py."""

    def test_ensure_project_temp_importable_from_io(self):
        from autoskillit.core.io import ensure_project_temp

        assert callable(ensure_project_temp)

    def test_ensure_project_temp_creates_temp_dir(self, tmp_path):
        from autoskillit.core.io import ensure_project_temp

        result = ensure_project_temp(tmp_path)
        assert result == tmp_path / ".autoskillit" / "temp"
        assert result.is_dir()

    def test_ensure_project_temp_writes_gitignore(self, tmp_path):
        from autoskillit.core.io import ensure_project_temp

        ensure_project_temp(tmp_path)
        gitignore = tmp_path / ".autoskillit" / ".gitignore"
        assert gitignore.read_text() == "temp/\n"

    def test_ensure_project_temp_is_idempotent(self, tmp_path):
        from autoskillit.core.io import ensure_project_temp

        ensure_project_temp(tmp_path)
        ensure_project_temp(tmp_path)  # second call must not raise
        assert (tmp_path / ".autoskillit" / "temp").is_dir()


class TestServeStartupLog:
    """N11: serve() logs startup info including resolved config path and test command."""

    def test_serve_logs_startup_with_config_path(self, tmp_path, monkeypatch):
        from unittest.mock import patch

        import structlog.testing

        import autoskillit.cli as cli_mod
        import autoskillit.server as server_mod

        monkeypatch.chdir(tmp_path)
        (tmp_path / ".autoskillit").mkdir()
        (tmp_path / ".autoskillit" / "config.yaml").write_text(
            "test_check:\n  command: [make, test]\n"
        )

        with (
            patch.object(server_mod.mcp, "run"),
            patch("autoskillit.core.configure_logging"),
            structlog.testing.capture_logs() as logs,
        ):
            cli_mod.serve()

        startup = next((entry for entry in logs if entry.get("event") == "serve_startup"), None)
        assert startup is not None
        assert startup["test_check_command"] == ["make", "test"]
        assert str(tmp_path / ".autoskillit" / "config.yaml") in startup["config_path"]

    def test_serve_logs_startup_config_path_none_when_no_config(self, tmp_path, monkeypatch):
        from unittest.mock import patch

        import structlog.testing

        import autoskillit.cli as cli_mod
        import autoskillit.server as server_mod

        monkeypatch.chdir(tmp_path)

        with (
            patch.object(server_mod.mcp, "run"),
            patch("autoskillit.core.configure_logging"),
            patch("autoskillit.cli.Path.home", return_value=tmp_path),
            structlog.testing.capture_logs() as logs,
        ):
            cli_mod.serve()

        startup = next((entry for entry in logs if entry.get("event") == "serve_startup"), None)
        assert startup is not None
        assert startup["config_path"] is None


class TestSyncRemovalCLI:
    def test_update_command_does_not_exist(self):
        """REQ-APP-002: 'autoskillit update' is not a registered command."""
        assert not hasattr(cli, "update")

    def test_doctor_has_no_recipe_sync_check(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ):
        """REQ-APP-006: doctor output does not include recipe_sync_status."""
        monkeypatch.chdir(tmp_path)
        cli.doctor()
        captured = capsys.readouterr()
        assert "recipe_sync_status" not in captured.out


class TestGroupFRefactoring:
    """P8-2, P3-2, P5-4: CLI refactoring — doctor delegation, public version_info, atomic write."""

    def test_doctor_delegates_to_doctor_module(self, monkeypatch, capsys):
        """cli.doctor() must delegate to cli._doctor.run_doctor(), not contain the logic itself."""
        from autoskillit.cli import _doctor

        called_with: dict = {}

        def mock_run_doctor(*, output_json: bool = False, plugin_dir: str | None = None) -> None:
            called_with["output_json"] = output_json

        monkeypatch.setattr(_doctor, "run_doctor", mock_run_doctor)
        cli.doctor(output_json=True)
        assert called_with == {"output_json": True}

    def test_severity_and_doctorresult_in_doctor_module(self):
        """Severity and DoctorResult must be importable from autoskillit.cli._doctor."""
        from autoskillit.cli._doctor import DoctorResult, Severity

        r = DoctorResult(severity=Severity.OK, check="test", message="ok")
        assert r.severity == Severity.OK
        assert r.check == "test"

    def test_upgrade_uses_atomic_write(self, tmp_path, monkeypatch):
        """upgrade() must call _atomic_write, not yaml_file.write_text."""
        import autoskillit.core as _core

        monkeypatch.chdir(tmp_path)
        scripts_dir = tmp_path / ".autoskillit" / "scripts"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "test.yaml").write_text("inputs:\n  foo: bar\n")

        atomic_calls: list[tuple] = []
        original = _core._atomic_write

        def capture(path, content):
            atomic_calls.append((path, content))
            return original(path, content)

        monkeypatch.setattr(_core, "_atomic_write", capture)
        cli.upgrade()

        assert len(atomic_calls) == 1, "Expected exactly one _atomic_write call"
        _, content = atomic_calls[0]
        assert "ingredients:" in content
        assert "inputs:" not in content

    def test_quota_status_subcommand_outputs_json(self, monkeypatch, capsys, tmp_path):
        """quota-status must emit JSON with required keys."""

        async def _mock_check(config):
            return {"should_sleep": False, "sleep_seconds": 0, "utilization": 45.0}

        monkeypatch.setattr("autoskillit.execution.quota.check_and_sleep_if_needed", _mock_check)
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
        import importlib

        settings_path = tmp_path / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True)

        # monkeypatch via the actual module object — string path resolves to the App object
        # due to autoskillit.cli.__init__.py re-exporting `app = App(...)` as attribute `app`
        app_module = importlib.import_module("autoskillit.cli.app")
        monkeypatch.setattr(app_module, "_claude_settings_path", lambda scope: settings_path)
        monkeypatch.setattr("subprocess.run", lambda *a, **kw: type("R", (), {"returncode": 0})())
        monkeypatch.setattr("shutil.which", lambda cmd: f"/usr/bin/{cmd}")
        # Clear CLAUDECODE env var so install doesn't short-circuit with the early-return path
        monkeypatch.delenv("CLAUDECODE", raising=False)
        import importlib as _importlib

        _app_mod = _importlib.import_module("autoskillit.cli.app")
        monkeypatch.setattr(_app_mod, "is_git_worktree", lambda path: False)
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
        import importlib

        settings_path = tmp_path / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True)

        app_module = importlib.import_module("autoskillit.cli.app")
        monkeypatch.setattr(app_module, "_claude_settings_path", lambda scope: settings_path)
        monkeypatch.setattr("subprocess.run", lambda *a, **kw: type("R", (), {"returncode": 0})())
        monkeypatch.setattr("shutil.which", lambda cmd: f"/usr/bin/{cmd}")
        monkeypatch.delenv("CLAUDECODE", raising=False)

        _app_mod = importlib.import_module("autoskillit.cli.app")
        monkeypatch.setattr(_app_mod, "is_git_worktree", lambda path: False)
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
        import importlib

        settings_path = tmp_path / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True)

        app_module = importlib.import_module("autoskillit.cli.app")
        monkeypatch.setattr(app_module, "_claude_settings_path", lambda scope: settings_path)
        monkeypatch.setattr("subprocess.run", lambda *a, **kw: type("R", (), {"returncode": 0})())
        monkeypatch.setattr("shutil.which", lambda cmd: f"/usr/bin/{cmd}")
        monkeypatch.delenv("CLAUDECODE", raising=False)

        _app_mod = importlib.import_module("autoskillit.cli.app")
        monkeypatch.setattr(_app_mod, "is_git_worktree", lambda path: False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        cli.install(scope="local")
        cli.install(scope="local")

        data = json.loads(settings_path.read_text())
        pretooluse = data.get("hooks", {}).get("PreToolUse", [])
        remove_clone_entries = [h for h in pretooluse if "remove_clone" in h.get("matcher", "")]
        assert len(remove_clone_entries) == 1, (
            f"Expected exactly 1 remove_clone hook entry, got {len(remove_clone_entries)}"
        )


class TestWorktreeDetection:
    def test_detects_main_checkout_as_not_worktree(self, tmp_path: Path) -> None:
        """A directory with a .git DIRECTORY is the main checkout, not a worktree."""
        from autoskillit.core.paths import is_git_worktree

        (tmp_path / ".git").mkdir()
        assert is_git_worktree(tmp_path) is False

    def test_detects_linked_worktree_via_git_file(self, tmp_path: Path) -> None:
        """A directory with a .git FILE is a linked worktree."""
        from autoskillit.core.paths import is_git_worktree

        (tmp_path / ".git").write_text("gitdir: /path/to/main/.git/worktrees/foo\n")
        assert is_git_worktree(tmp_path) is True

    def test_detects_worktree_from_subdirectory(self, tmp_path: Path) -> None:
        """Detection works when called from a subdirectory of the worktree root."""
        from autoskillit.core.paths import is_git_worktree

        (tmp_path / ".git").write_text("gitdir: /path/to/main/.git/worktrees/foo\n")
        subdir = tmp_path / "src" / "autoskillit"
        subdir.mkdir(parents=True)
        assert is_git_worktree(subdir) is True

    def test_not_in_git_repo_returns_false(self, tmp_path: Path) -> None:
        """Directories with no .git ancestor return False (not a worktree)."""
        from autoskillit.core.paths import is_git_worktree

        assert is_git_worktree(tmp_path) is False


class TestPkgRoot:
    def test_pkg_root_matches_importlib_resources(self) -> None:
        """pkg_root() must return the same path as importlib.resources.files('autoskillit')."""
        import importlib.resources as ir

        from autoskillit.core.paths import pkg_root

        assert pkg_root() == Path(ir.files("autoskillit"))

    def test_pkg_root_is_package_directory(self) -> None:
        """pkg_root() must return the autoskillit package root directory."""
        from autoskillit.core.paths import pkg_root

        result = pkg_root()
        assert (result / "__init__.py").is_file(), (
            "pkg_root() must return the autoskillit package root"
        )
        assert result.name == "autoskillit", (
            "pkg_root() must return the autoskillit package directory"
        )


class TestInstallCommand:
    def test_ensure_marketplace_raises_in_worktree(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_ensure_marketplace() raises SystemExit when is_git_worktree() returns True."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        import importlib as _importlib

        _app_mod = _importlib.import_module("autoskillit.cli.app")
        monkeypatch.setattr(_app_mod, "is_git_worktree", lambda path: True)

        with pytest.raises(SystemExit, match="worktree"):
            cli._ensure_marketplace()

    def test_ensure_marketplace_succeeds_in_main_checkout(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_ensure_marketplace() succeeds when is_git_worktree() returns False."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        import importlib as _importlib

        _app_mod = _importlib.import_module("autoskillit.cli.app")
        monkeypatch.setattr(_app_mod, "is_git_worktree", lambda path: False)

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
