"""Tests for core/_cmd_runner.py — CmdRunner protocol, default_cmd_runner, run_git, run_gh."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from autoskillit.core import CmdRunner, default_cmd_runner, run_gh, run_git

pytestmark = [pytest.mark.layer("core"), pytest.mark.medium]


class TestDefaultCmdRunner:
    def test_default_cmd_runner_captures_output(self) -> None:
        result = default_cmd_runner(["echo", "hello"])
        assert isinstance(result, subprocess.CompletedProcess)
        assert result.returncode == 0
        assert result.stdout == "hello\n"

    def test_default_cmd_runner_check_raises(self) -> None:
        with pytest.raises(subprocess.CalledProcessError):
            default_cmd_runner(["false"], check=True)

    def test_default_cmd_runner_timeout_passed(self) -> None:
        with patch("autoskillit.core._cmd_runner.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="")
            default_cmd_runner(["echo", "test"], timeout=30.0)
            call_kwargs = mock_run.call_args[1]
            assert call_kwargs["timeout"] == 30.0

    def test_default_cmd_runner_input_data(self) -> None:
        result = default_cmd_runner(["cat"], input_data="hello")
        assert result.stdout == "hello"

    def test_cmd_runner_protocol_compliance(self) -> None:
        assert isinstance(default_cmd_runner, CmdRunner)


class TestRunGit:
    def test_run_git_prepends_git(self) -> None:
        mock_runner = MagicMock(spec=CmdRunner)
        mock_runner.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="")
        run_git(["status"], cwd="/tmp", runner=mock_runner)
        mock_runner.assert_called_once()
        call_args = mock_runner.call_args[0][0]
        assert call_args == ["git", "status"]

    def test_run_git_requires_cwd(self) -> None:
        with pytest.raises(TypeError):
            run_git(["status"])  # type: ignore[call-arg]

    def test_run_git_passes_cwd(self) -> None:
        mock_runner = MagicMock(spec=CmdRunner)
        mock_runner.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="")
        run_git(["status"], cwd="/tmp", runner=mock_runner)
        assert mock_runner.call_args[1]["cwd"] == "/tmp"

    def test_run_git_passes_check(self) -> None:
        mock_runner = MagicMock(spec=CmdRunner)
        mock_runner.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="")
        run_git(["status"], cwd="/tmp", check=True, runner=mock_runner)
        assert mock_runner.call_args[1]["check"] is True

    def test_run_git_passes_timeout(self) -> None:
        mock_runner = MagicMock(spec=CmdRunner)
        mock_runner.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="")
        run_git(["status"], cwd="/tmp", timeout=60.0, runner=mock_runner)
        assert mock_runner.call_args[1]["timeout"] == 60.0


class TestRunGh:
    def test_run_gh_prepends_gh(self) -> None:
        mock_runner = MagicMock(spec=CmdRunner)
        mock_runner.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="")
        run_gh(["pr", "list"], runner=mock_runner)
        mock_runner.assert_called_once()
        call_args = mock_runner.call_args[0][0]
        assert call_args == ["gh", "pr", "list"]

    def test_run_gh_allows_no_cwd(self) -> None:
        mock_runner = MagicMock(spec=CmdRunner)
        mock_runner.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="")
        # Should not raise — cwd is optional
        run_gh(["pr", "list"], runner=mock_runner)
        assert mock_runner.call_args[1]["cwd"] is None

    def test_run_gh_passes_cwd(self) -> None:
        mock_runner = MagicMock(spec=CmdRunner)
        mock_runner.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="")
        run_gh(["pr", "list"], cwd="/tmp", runner=mock_runner)
        assert mock_runner.call_args[1]["cwd"] == "/tmp"

    def test_run_gh_passes_input_data(self) -> None:
        mock_runner = MagicMock(spec=CmdRunner)
        mock_runner.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="")
        run_gh(
            ["api", "graphql", "--input", "-"],
            input_data="payload",
            runner=mock_runner,
        )
        assert mock_runner.call_args[1]["input_data"] == "payload"

    def test_run_gh_passes_check(self) -> None:
        mock_runner = MagicMock(spec=CmdRunner)
        mock_runner.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="")
        run_gh(["pr", "list"], check=True, runner=mock_runner)
        assert mock_runner.call_args[1]["check"] is True


class TestCustomRunnerInjection:
    def test_custom_runner_injection(self) -> None:
        custom_runner = MagicMock(spec=CmdRunner)
        custom_runner.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="custom"
        )
        result = run_git(["status"], cwd="/tmp", runner=custom_runner)
        custom_runner.assert_called_once()
        assert result.stdout == "custom"

    def test_custom_runner_not_default(self) -> None:
        with patch("autoskillit.core._cmd_runner.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="")
            # Using default runner should call subprocess.run
            default_cmd_runner(["echo", "test"])
            mock_run.assert_called_once()
