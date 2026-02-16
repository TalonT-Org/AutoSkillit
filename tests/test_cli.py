"""Tests for CLI commands."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from automation_mcp import cli


class TestCLI:
    # CL1
    def test_default_command_starts_server(self) -> None:
        mock_mcp = MagicMock()
        with patch.object(cli, "serve", wraps=cli.serve) as mock_serve:
            with patch("automation_mcp.server.mcp", mock_mcp):
                cli.serve()
        mock_mcp.run.assert_called_once()

    # CL2
    def test_serve_command_starts_server(self) -> None:
        mock_mcp = MagicMock()
        with patch("automation_mcp.server.mcp", mock_mcp):
            cli.serve_explicit()
        mock_mcp.run.assert_called_once()

    # CL3
    def test_init_creates_config_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        with patch.object(cli, "_quick_init", return_value={"version": 1}):
            cli.init(quick=True, force=False)
        assert (tmp_path / ".automation-mcp").is_dir()

    # CL4
    def test_init_writes_config_yaml(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        with patch.object(cli, "_quick_init", return_value={"version": 1, "test_check": {"command": ["pytest", "-v"]}}):
            cli.init(quick=True, force=False)
        config_path = tmp_path / ".automation-mcp" / "config.yaml"
        assert config_path.is_file()
        data = yaml.safe_load(config_path.read_text())
        assert data["version"] == 1
        assert data["test_check"]["command"] == ["pytest", "-v"]

    # CL5
    def test_init_quick_mode(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        with patch.object(cli, "_prompt", return_value="npm test"):
            cli.init(quick=True, force=False)
        config_path = tmp_path / ".automation-mcp" / "config.yaml"
        data = yaml.safe_load(config_path.read_text())
        assert data["test_check"]["command"] == ["npm", "test"]

    # CL6
    def test_init_no_overwrite_without_force(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        monkeypatch.chdir(tmp_path)
        config_dir = tmp_path / ".automation-mcp"
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
            with patch("sys.argv", ["automation-mcp", "nonexistent"]):
                cli.main()
        assert exc_info.value.code != 0

    # CL9
    def test_update_refreshes_builtins(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        monkeypatch.chdir(tmp_path)
        wf_dir = tmp_path / ".automation-mcp" / "workflows"
        wf_dir.mkdir(parents=True)

        from automation_mcp.workflow_loader import builtin_workflows_dir

        builtin_dir = builtin_workflows_dir()
        for f in builtin_dir.glob("*.yaml"):
            shutil.copy2(f, wf_dir / f.name)

        (wf_dir / "bugfix-loop.yaml").write_text("name: bugfix-loop\ncustomized: true\n")

        cli.update()
        captured = capsys.readouterr()
        assert "bugfix-loop" in captured.out
        assert "Skipped (customized)" in captured.out

    def test_init_force_overwrites(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        config_dir = tmp_path / ".automation-mcp"
        config_dir.mkdir()
        config_path = config_dir / "config.yaml"
        config_path.write_text("old: true\n")

        with patch.object(cli, "_quick_init", return_value={"version": 1}):
            cli.init(quick=True, force=True)

        data = yaml.safe_load(config_path.read_text())
        assert data == {"version": 1}
