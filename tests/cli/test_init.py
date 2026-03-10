"""Tests for CLI init, config, and serve-related commands."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from autoskillit import cli


class TestCLIInit:
    # CL1
    def test_serve_calls_mcp_run(self) -> None:
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
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        cli.init(test_command="pytest -v")
        assert (tmp_path / ".autoskillit").is_dir()

    # CL4
    def test_init_writes_config_yaml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
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
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
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
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
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
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
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
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
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
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text("old: true\n")

        cli.init(test_command="npm test", force=True)

        data = yaml.safe_load((config_dir / "config.yaml").read_text())
        assert data["test_check"]["command"] == ["npm", "test"]

    def test_init_idempotent_rerun(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Running init twice is safe — config preserved on second run."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        cli.init(test_command="pytest -v")
        config_before = (tmp_path / ".autoskillit" / "config.yaml").read_text()
        # Re-run init — should not overwrite without --force
        cli.init(test_command="pytest -v")
        assert (tmp_path / ".autoskillit" / "config.yaml").read_text() == config_before

    # CI-SCOPE-1
    def test_init_registers_mcp_server_in_claude_json(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """init --scope user writes mcpServers.autoskillit to ~/.claude.json."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        cli.init(scope="user", test_command="task test-all")
        claude_json = tmp_path / ".claude.json"
        data = json.loads(claude_json.read_text())
        assert "autoskillit" in data["mcpServers"]
        assert data["mcpServers"]["autoskillit"]["command"] == "autoskillit"
        assert data["mcpServers"]["autoskillit"]["args"] == []

    # CI-SCOPE-2
    def test_init_registers_hooks_in_settings_json(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """init --scope user registers all HOOK_REGISTRY hooks in settings.json."""
        from autoskillit.hook_registry import HOOK_REGISTRY

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        cli.init(scope="user", test_command="task test-all")
        settings_path = tmp_path / ".claude" / "settings.json"
        data = json.loads(settings_path.read_text())
        registered = " ".join(
            cmd
            for event_entries in data["hooks"].values()
            for entry in event_entries
            for hook in entry.get("hooks", [])
            for cmd in [hook.get("command", "")]
        )
        for hdef in HOOK_REGISTRY:
            for script in hdef.scripts:
                assert script in registered, f"Expected hook script {script!r} to be registered"

    # CI-SCOPE-3
    def test_init_idempotent_no_duplicates(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Running init twice does not duplicate mcpServers.autoskillit or hook entries."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        cli.init(scope="user", test_command="task test-all")
        cli.init(scope="user", test_command="task test-all")
        claude_json = tmp_path / ".claude.json"
        data = json.loads(claude_json.read_text())
        assert list(data["mcpServers"].keys()).count("autoskillit") == 1
        settings_path = tmp_path / ".claude" / "settings.json"
        settings = json.loads(settings_path.read_text())
        pretooluse = settings.get("hooks", {}).get("PreToolUse", [])
        matchers = [e.get("matcher", "") for e in pretooluse]
        # No duplicate matchers (run_skill appears exactly once)
        run_skill_count = sum(1 for m in matchers if "run_skill" in m)
        assert run_skill_count == 1

    # CI-SCOPE-4
    def test_init_default_scope_is_user(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """init without --scope defaults to user scope (writes to ~/.claude.json)."""
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        monkeypatch.chdir(project_dir)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        cli.init(test_command="task test-all")
        # MCP server should be registered to user home, not project dir
        assert (tmp_path / ".claude.json").exists()


class TestEnsureProjectTemp:
    """N5: ensure_project_temp moved from config.py to _io.py."""

    def test_ensure_project_temp_importable_from_io(self):
        from autoskillit.core.io import ensure_project_temp  # noqa: F401

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
