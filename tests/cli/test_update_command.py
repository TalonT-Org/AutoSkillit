"""Tests for cli/_update.py — first-class update command."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from autoskillit.cli._install_info import InstallInfo, InstallType

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]


def _make_info(
    install_type: InstallType,
    revision: str | None = None,
    editable_source: Path | None = None,
) -> InstallInfo:
    return InstallInfo(
        install_type=install_type,
        commit_id="abc123" if install_type == InstallType.GIT_VCS else None,
        requested_revision=revision,
        url=None,
        editable_source=editable_source,
    )


class FakeTG:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _setup_run_update(
    monkeypatch: pytest.MonkeyPatch,
    info: InstallInfo,
    tmp_path: Path,
    current_version: str = "0.7.77",
    new_version: str = "0.9.0",
) -> list[list[str]]:
    """Patch run_update_command dependencies, return captured subprocess.run call args."""
    monkeypatch.setattr("autoskillit.cli._update.detect_install", lambda: info)
    monkeypatch.setattr("autoskillit.cli._update.terminal_guard", FakeTG)

    run_calls: list[list[str]] = []
    monkeypatch.setattr(
        "autoskillit.cli._update.subprocess.run",
        lambda cmd, **kw: run_calls.append(list(cmd)) or subprocess.CompletedProcess(cmd, 0),
    )

    import autoskillit as _pkg

    monkeypatch.setattr(_pkg, "__version__", current_version)

    import importlib.metadata

    monkeypatch.setattr(importlib.metadata, "version", lambda _name: new_version)
    return run_calls


def test_update_subcommand_registered_in_help() -> None:
    """autoskillit update --help must exit 0."""
    import subprocess as _sp
    import sys

    result = _sp.run(
        [sys.executable, "-m", "autoskillit", "update", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"update --help failed: {result.stderr}"


@pytest.mark.parametrize(
    "revision,expected_cmd_prefix",
    [
        ("stable", ["uv", "tool", "upgrade", "autoskillit"]),
        ("main", ["uv", "tool", "upgrade", "autoskillit"]),
        ("v0.7.75", ["uv", "tool", "upgrade", "autoskillit"]),
        ("integration", ["uv", "tool", "install", "--force"]),
    ],
)
def test_update_runs_upgrade_command_for_git_vcs_install(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    revision: str,
    expected_cmd_prefix: list[str],
) -> None:
    from autoskillit.cli._update import run_update_command

    info = _make_info(InstallType.GIT_VCS, revision=revision)
    run_calls = _setup_run_update(monkeypatch, info, tmp_path)
    run_update_command(home=tmp_path)
    assert any(cmd[: len(expected_cmd_prefix)] == expected_cmd_prefix for cmd in run_calls), (
        f"Expected cmd prefix {expected_cmd_prefix} in calls {run_calls}"
    )


def test_update_runs_upgrade_command_for_local_editable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from autoskillit.cli._update import run_update_command

    editable_source = tmp_path / "repo"
    editable_source.mkdir()
    info = _make_info(InstallType.LOCAL_EDITABLE, editable_source=editable_source)
    run_calls = _setup_run_update(monkeypatch, info, tmp_path)
    run_update_command(home=tmp_path)
    assert any(cmd[:4] == ["uv", "pip", "install", "-e"] for cmd in run_calls), (
        f"Expected uv pip install -e in calls {run_calls}"
    )


def test_update_runs_autoskillit_install_after_upgrade_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from autoskillit.cli._update import run_update_command

    info = _make_info(InstallType.GIT_VCS, revision="stable")
    run_calls = _setup_run_update(monkeypatch, info, tmp_path)
    run_update_command(home=tmp_path)
    assert any(cmd[:2] == ["autoskillit", "install"] for cmd in run_calls)


def test_update_passes_skip_env_to_subprocess(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from autoskillit.cli._update import run_update_command

    info = _make_info(InstallType.GIT_VCS, revision="stable")
    monkeypatch.setattr("autoskillit.cli._update.detect_install", lambda: info)
    monkeypatch.setattr("autoskillit.cli._update.terminal_guard", FakeTG)

    env_passed: list[dict] = []
    monkeypatch.setattr(
        "autoskillit.cli._update.subprocess.run",
        lambda cmd, **kw: (
            env_passed.append(kw.get("env", {})) or subprocess.CompletedProcess(cmd, 0)
        ),
    )

    import autoskillit as _pkg

    monkeypatch.setattr(_pkg, "__version__", "0.7.77")

    import importlib.metadata

    monkeypatch.setattr(importlib.metadata, "version", lambda _name: "0.9.0")
    run_update_command(home=tmp_path)

    for env in env_passed:
        assert env.get("AUTOSKILLIT_SKIP_STALE_CHECK") == "1"
        assert env.get("AUTOSKILLIT_SKIP_UPDATE_CHECK") == "1"
        assert env.get("AUTOSKILLIT_SKIP_SOURCE_DRIFT_CHECK") == "1"


def test_update_verifies_version_advance_and_warns_on_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from autoskillit.cli._update import run_update_command

    info = _make_info(InstallType.GIT_VCS, revision="stable")
    monkeypatch.setattr("autoskillit.cli._update.detect_install", lambda: info)
    monkeypatch.setattr("autoskillit.cli._update.terminal_guard", FakeTG)
    monkeypatch.setattr(
        "autoskillit.cli._update.subprocess.run",
        lambda *a, **kw: subprocess.CompletedProcess([], 0),
    )
    monkeypatch.setattr(
        "autoskillit.cli._update_checks._fetch_latest_version", lambda *a, **kw: "0.9.0"
    )

    import autoskillit as _pkg

    monkeypatch.setattr(_pkg, "__version__", "0.7.77")

    # version unchanged after upgrade — simulates a silent failure
    import importlib.metadata

    monkeypatch.setattr(importlib.metadata, "version", lambda _name: "0.7.77")

    printed: list[str] = []
    monkeypatch.setattr(
        "builtins.print", lambda *args, **kw: printed.append(" ".join(str(a) for a in args))
    )
    run_update_command(home=tmp_path)
    combined = " ".join(printed)
    assert "still 0.7.77" in combined


def test_update_clears_dismissal_state_on_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from autoskillit.cli._update import run_update_command
    from autoskillit.cli._update_checks import _read_dismiss_state, _write_dismiss_state

    # Seed dismissal state
    (tmp_path / ".autoskillit").mkdir(parents=True, exist_ok=True)
    _write_dismiss_state(
        tmp_path,
        {
            "update_prompt": {
                "dismissed_at": "2026-01-01T00:00:00+00:00",
                "dismissed_version": "0.7.77",
                "conditions": ["binary"],
            }
        },
    )

    info = _make_info(InstallType.GIT_VCS, revision="stable")
    monkeypatch.setattr("autoskillit.cli._update.detect_install", lambda: info)
    monkeypatch.setattr("autoskillit.cli._update.terminal_guard", FakeTG)
    monkeypatch.setattr(
        "autoskillit.cli._update.subprocess.run",
        lambda *a, **kw: subprocess.CompletedProcess([], 0),
    )

    import autoskillit as _pkg

    monkeypatch.setattr(_pkg, "__version__", "0.7.77")

    import importlib.metadata

    monkeypatch.setattr(importlib.metadata, "version", lambda _name: "0.9.0")

    monkeypatch.setattr("builtins.print", lambda *a, **kw: None)
    run_update_command(home=tmp_path)
    state = _read_dismiss_state(tmp_path)
    assert "update_prompt" not in state


def test_update_reports_actionable_error_on_unknown_install_type(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from autoskillit.cli._update import run_update_command

    info = _make_info(InstallType.UNKNOWN)
    monkeypatch.setattr("autoskillit.cli._update.detect_install", lambda: info)
    printed: list[str] = []
    monkeypatch.setattr(
        "builtins.print", lambda *args, **kw: printed.append(" ".join(str(a) for a in args))
    )
    with pytest.raises(SystemExit) as exc_info:
        run_update_command(home=tmp_path)
    assert exc_info.value.code == 2
    combined = " ".join(printed)
    assert "Unknown install type" in combined or "install.sh" in combined


def test_run_update_command_warns_on_install_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, tmp_path: Path
) -> None:
    """run_update_command warns user when autoskillit install step exits non-zero."""
    from autoskillit.cli._update import run_update_command

    info = _make_info(InstallType.GIT_VCS, revision="stable")
    monkeypatch.setattr("autoskillit.cli._update.detect_install", lambda: info)
    monkeypatch.setattr("autoskillit.cli._update.terminal_guard", FakeTG)

    upgrade_ok = subprocess.CompletedProcess([], returncode=0)
    install_fail = subprocess.CompletedProcess([], returncode=1)
    mock_run = MagicMock(side_effect=[upgrade_ok, install_fail])
    monkeypatch.setattr("autoskillit.cli._update.subprocess.run", mock_run)
    monkeypatch.setattr(
        "autoskillit.cli._update_checks._fetch_latest_version", lambda *a, **kw: "0.9.1"
    )
    import autoskillit as _pkg

    # Simulate pre-update state: process-cached __version__ is stale (0.9.0),
    # but post-install metadata already reflects the new version (0.9.1).
    monkeypatch.setattr(_pkg, "__version__", "0.9.0")
    import importlib.metadata

    monkeypatch.setattr(importlib.metadata, "version", lambda _: "0.9.1")
    run_update_command(home=tmp_path)
    out = capsys.readouterr().out
    assert "autoskillit install" in out
