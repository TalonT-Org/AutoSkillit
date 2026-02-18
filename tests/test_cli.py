"""Tests for CLI commands."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import ModuleType
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

    # CL2
    def test_serve_command_starts_server(self) -> None:
        mock_mcp = MagicMock()
        with patch("autoskillit.server.mcp", mock_mcp):
            cli.serve_explicit()
        mock_mcp.run.assert_called_once()

    # CL3
    def test_init_creates_config_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        with patch.object(cli, "_quick_init", return_value=["pytest", "-v"]):
            cli.init(quick=True, force=False)
        assert (tmp_path / ".autoskillit").is_dir()

    # CL4
    def test_init_writes_config_yaml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        with patch.object(cli, "_quick_init", return_value=["pytest", "-v"]):
            cli.init(quick=True, force=False)
        config_path = tmp_path / ".autoskillit" / "config.yaml"
        assert config_path.is_file()
        data = yaml.safe_load(config_path.read_text())
        assert data["test_check"]["command"] == ["pytest", "-v"]
        assert data["safety"]["playground_guard"] is True

    # CL5
    def test_init_quick_mode(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        with patch.object(cli, "_prompt", return_value="npm test"):
            cli.init(quick=True, force=False)
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

        cli.init(quick=False, force=False)

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

        with patch.object(cli, "_quick_init", return_value=["pytest", "-v"]):
            cli.init(quick=True, force=True)

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
        assert "# skills:" in yaml_str

    def test_generate_config_yaml_uncommented_parts_are_valid(self) -> None:
        """The uncommented portion of generated YAML parses as valid config."""
        from autoskillit.cli import _generate_config_yaml

        yaml_str = _generate_config_yaml(["task", "test-all"])
        parsed = yaml.safe_load(yaml_str)
        assert parsed["test_check"]["command"] == ["task", "test-all"]
        assert parsed["safety"]["playground_guard"] is True

    def test_interactive_init_asks_minimal_questions(self) -> None:
        """Interactive init only asks about the test command."""
        with patch.object(cli, "_choose", return_value="Python (pytest)"):
            with patch.object(cli, "_prompt", return_value="pytest -v") as mock_prompt:
                cli._interactive_init()

        assert mock_prompt.call_count == 1

    def test_init_writes_template_with_comments(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """init writes a config file containing commented advanced sections."""
        monkeypatch.chdir(tmp_path)
        with patch.object(cli, "_prompt", return_value="pytest -v"):
            cli.init(quick=True)

        config_path = tmp_path / ".autoskillit" / "config.yaml"
        content = config_path.read_text()
        assert "# classify_fix:" in content
        assert "test_check:" in content

    def test_prompt_delegates_to_questionary(self) -> None:
        """_prompt calls questionary.text().unsafe_ask() when available."""
        mock_q = ModuleType("questionary")
        mock_question = MagicMock()
        mock_question.unsafe_ask.return_value = "my answer"
        mock_q.text = MagicMock(return_value=mock_question)

        with patch.dict("sys.modules", {"questionary": mock_q}):
            result = cli._prompt("Test command", "default_val")

        mock_q.text.assert_called_once_with("Test command", default="default_val")
        mock_question.unsafe_ask.assert_called_once()
        assert result == "my answer"

    def test_choose_delegates_to_questionary(self) -> None:
        """_choose calls questionary.select().unsafe_ask() when available."""
        mock_q = ModuleType("questionary")
        mock_question = MagicMock()
        mock_question.unsafe_ask.return_value = "Go"
        mock_q.select = MagicMock(return_value=mock_question)

        with patch.dict("sys.modules", {"questionary": mock_q}):
            result = cli._choose("Project type", ["Python (pytest)", "Go", "Custom"])

        mock_q.select.assert_called_once_with(
            "Project type", choices=["Python (pytest)", "Go", "Custom"]
        )
        mock_question.unsafe_ask.assert_called_once()
        assert result == "Go"

    def test_init_test_command_flag_skips_prompts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--test-command generates config without any interactive prompts."""
        monkeypatch.chdir(tmp_path)

        with patch.object(cli, "_prompt") as mock_prompt:
            with patch.object(cli, "_choose") as mock_choose:
                cli.init(test_command="pytest -v")

        mock_prompt.assert_not_called()
        mock_choose.assert_not_called()

        config_path = tmp_path / ".autoskillit" / "config.yaml"
        data = yaml.safe_load(config_path.read_text())
        assert data["test_check"]["command"] == ["pytest", "-v"]

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
