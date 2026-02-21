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
            with patch("autoskillit.server.mcp", mock_mcp):
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
        with patch.object(cli, "_prompt_test_command", return_value=["npm", "test"]):
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

    # CL9
    def test_update_refreshes_builtins(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        monkeypatch.chdir(tmp_path)
        wf_dir = tmp_path / ".autoskillit" / "workflows"
        wf_dir.mkdir(parents=True)

        from autoskillit.workflow_loader import builtin_workflows_dir

        builtin_dir = builtin_workflows_dir()
        for f in builtin_dir.glob("*.yaml"):
            shutil.copy2(f, wf_dir / f.name)

        (wf_dir / "bugfix-loop.yaml").write_text("name: bugfix-loop\ncustomized: true\n")

        cli.update()
        captured = capsys.readouterr()
        assert "bugfix-loop" in captured.out
        assert "Skipped (customized)" in captured.out

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

    def test_workspace_init_creates_dir_with_marker(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """workspace init creates directory and drops marker file."""
        monkeypatch.chdir(tmp_path)
        target = tmp_path / "test-workspace"
        cli.workspace_init(str(target))
        assert target.is_dir()
        assert (target / ".autoskillit-workspace").is_file()

    def test_workspace_init_refuses_nonempty_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """workspace init refuses to initialize a non-empty directory."""
        monkeypatch.chdir(tmp_path)
        target = tmp_path / "existing"
        target.mkdir()
        (target / "important.txt").touch()
        with pytest.raises(SystemExit):
            cli.workspace_init(str(target))

    def test_workspace_init_idempotent_on_empty_with_marker(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """workspace init is safe to re-run on a directory that only has the marker."""
        monkeypatch.chdir(tmp_path)
        target = tmp_path / "workspace"
        cli.workspace_init(str(target))
        cli.workspace_init(str(target))  # second run — should not fail
        assert (target / ".autoskillit-workspace").is_file()

    def test_workspace_init_marker_has_content(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Marker file contains human-readable identifying content."""
        monkeypatch.chdir(tmp_path)
        target = tmp_path / "workspace"
        cli.workspace_init(str(target))
        content = (target / ".autoskillit-workspace").read_text()
        assert "autoskillit" in content
        assert "do not delete" in content

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
        """doctor does not warn about legitimate co-registered MCP servers."""
        fake_claude_json = tmp_path / ".claude.json"
        fake_bin = tmp_path / "legit-server"
        fake_bin.write_text("#!/bin/sh\n")
        fake_bin.chmod(0o755)
        fake_claude_json.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "other-server": {"type": "stdio", "command": str(fake_bin)},
                        "autoskillit": {"type": "stdio", "command": "autoskillit"},
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
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """doctor reports error when plugin.json version differs from package."""
        from autoskillit import server

        plugin_dir = tmp_path / "fake_plugin" / ".claude-plugin"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "plugin.json").write_text(
            json.dumps({"name": "autoskillit", "version": "0.0.0"})
        )
        monkeypatch.setattr(server, "_plugin_dir", str(tmp_path / "fake_plugin"))
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
                {
                    "plugins": [
                        {"name": "autoskillit", "version": "0.0.0-stale", "source": "."}
                    ]
                }
            )
        )
        link_dir = mkt_dir / "plugins"
        link_dir.mkdir(parents=True)
        link = link_dir / "autoskillit"
        link.symlink_to(Path(cli.__file__).parent)

        cli.doctor(output_json=True)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        mkt_checks = [r for r in data["results"] if r["check"] == "marketplace_freshness"]
        assert len(mkt_checks) == 1
        assert mkt_checks[0]["severity"] == "warning"

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
            "plugin_metadata",
            "autoskillit_on_path",
            "project_config",
            "version_consistency",
        }
        assert expected <= check_names

    def test_doctor_human_output_shows_severity(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """doctor human output includes severity prefixes for problems."""
        from autoskillit import server

        plugin_dir = tmp_path / "fake_plugin" / ".claude-plugin"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "plugin.json").write_text(
            json.dumps({"name": "autoskillit", "version": "0.0.0"})
        )
        monkeypatch.setattr(server, "_plugin_dir", str(tmp_path / "fake_plugin"))
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.chdir(tmp_path)
        cli.doctor()
        captured = capsys.readouterr()
        assert "ERROR:" in captured.out

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
        marketplace_dir = cli._ensure_marketplace()
        assert (marketplace_dir / ".claude-plugin" / "marketplace.json").is_file()
        assert (marketplace_dir / "plugins" / "autoskillit").is_symlink()

    def test_install_symlink_points_to_package(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Marketplace symlink resolves to the autoskillit package directory."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        marketplace_dir = cli._ensure_marketplace()
        link = marketplace_dir / "plugins" / "autoskillit"
        assert link.resolve() == Path(cli.__file__).parent.resolve()

    def test_install_marketplace_json_content(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Marketplace manifest has correct structure and plugin name."""
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
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
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/claude")
        monkeypatch.delenv("CLAUDECODE", raising=False)
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
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/bin/claude")
        monkeypatch.delenv("CLAUDECODE", raising=False)
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )

        cli.install()
        cli.install()  # second run should not fail

        assert (tmp_path / ".autoskillit" / "marketplace" / "plugins" / "autoskillit").is_symlink()
