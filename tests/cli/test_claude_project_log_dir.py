"""Tests for the side-effect-free Claude project-log directory command."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]


@pytest.mark.parametrize(
    ("cwd", "encoded"),
    [
        (
            "/workspace/repro/.autoskillit/temp/sandbox",
            "-workspace-repro--autoskillit-temp-sandbox",
        ),
        ("/home/user/projects/my_project", "-home-user-projects-my-project"),
    ],
)
def test_claude_project_log_dir_prints_one_exact_path_without_mcp_activation(
    cwd: str,
    encoded: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)
    from autoskillit.cli.app import app

    app(["claude-project-log-dir", cwd])

    assert capsys.readouterr().out == f"{home / '.claude' / 'projects' / encoded}\n"
