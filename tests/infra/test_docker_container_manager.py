"""Tests for the tracked AutoSkillit session-container manager."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

from autoskillit.core.io import load_yaml

pytestmark = [pytest.mark.layer("infra"), pytest.mark.medium]

_ROOT = Path(__file__).resolve().parents[2]
_MANAGER = _ROOT / "scripts" / "docker" / "autoskillit-container"
_COMMANDS = ("update", "start", "stop", "shell", "status", "sync-auth")
_BASH_TIMEOUT_SECONDS = 30


def _run_manager(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(_MANAGER), *args],
        capture_output=True,
        text=True,
        env={"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/tmp")},
        timeout=_BASH_TIMEOUT_SECONDS,
    )


def _assignment(text: str, name: str) -> str:
    match = re.search(rf"^{name}=(\S+)$", text, re.MULTILINE)
    assert match is not None, f"{_MANAGER.name} must assign {name}"
    return match.group(1)


class TestManagerScript:
    def test_syntax_is_valid(self) -> None:
        result = subprocess.run(
            ["bash", "-n", str(_MANAGER)],
            capture_output=True,
            text=True,
            timeout=_BASH_TIMEOUT_SECONDS,
        )
        assert result.returncode == 0, result.stderr

    def test_is_executable(self) -> None:
        assert os.access(_MANAGER, os.X_OK)

    def test_help_lists_every_command(self) -> None:
        result = _run_manager("--help")

        assert result.returncode == 0, result.stderr
        for command in _COMMANDS:
            assert re.search(rf"^  {command}\s", result.stdout, re.MULTILINE), command

    def test_unknown_command_is_a_usage_error(self) -> None:
        result = _run_manager("bogus")

        assert result.returncode == 2
        assert "Usage" in result.stderr

    def test_update_pulls_instead_of_building(self) -> None:
        script = _MANAGER.read_text(encoding="utf-8")

        assert "docker pull" in script
        assert "docker build" not in script

    def test_agent_probe_matches_process_names(self) -> None:
        """``pgrep -f`` would match the probe's own ``sh -c`` command line."""
        script = _MANAGER.read_text(encoding="utf-8")

        assert "pgrep -f" not in script
        for name in ("autoskillit", '"claude(\\.exe)?"', "codex"):
            assert f"pgrep -x {name} " in script, name


class TestManagerAgreesWithTheImage:
    def test_runs_the_published_repository(self) -> None:
        workflow = load_yaml(_ROOT / ".github" / "workflows" / "docker-image.yml")
        script = _MANAGER.read_text(encoding="utf-8")

        assert _assignment(script, "IMAGE_REPOSITORY") == workflow["env"]["IMAGE_NAME"]

    def test_mounts_the_home_of_the_user_target(self) -> None:
        dockerfile = (_ROOT / "scripts" / "docker" / "Dockerfile").read_text(encoding="utf-8")
        user_stage = next(
            section
            for section in re.split(r"^FROM ", dockerfile, flags=re.MULTILINE)
            if section.startswith("toolchain AS user\n")
        )
        home = re.search(r"\bHOME=(\S+)", user_stage)
        script = _MANAGER.read_text(encoding="utf-8")

        assert home is not None
        assert _assignment(script, "CONTAINER_HOME") == home.group(1)
