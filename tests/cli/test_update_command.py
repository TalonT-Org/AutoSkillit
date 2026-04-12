"""Tests for cli/_update.py — first-class update command."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.cli._install_info import InstallInfo, InstallType


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
        lambda cmd, **kw: run_calls.append(list(cmd)),
    )

    import autoskillit as _pkg

    monkeypatch.setattr(_pkg, "__version__", current_version)

    import importlib.metadata

    monkeypatch.setattr(importlib.metadata, "version", lambda _name: new_version)
    return run_calls


def test_update_subcommand_registered_in_help() -> None:
    """autoskillit update --help must exit 0."""
    import subprocess as _sp

    result = _sp.run(
        ["autoskillit", "update", "--help"],
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
        lambda cmd, **kw: env_passed.append(kw.get("env", {})),
    )

    import autoskillit as _pkg

    monkeypatch.setattr(_pkg, "__version__", "0.7.77")

    import importlib.metadata

    monkeypatch.setattr(importlib.metadata, "version", lambda _name: "0.9.0")
    run_update_command(home=tmp_path)

    for env in env_passed:
        assert env.get("AUTOSKILLIT_SKIP_STALE_CHECK") == "1"
        assert env.get("AUTOSKILLIT_SKIP_SOURCE_DRIFT_CHECK") == "1"


def test_update_verifies_version_advance_and_warns_on_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from autoskillit.cli._update import run_update_command
    from autoskillit.cli._update_checks import _read_dismiss_state

    info = _make_info(InstallType.GIT_VCS, revision="stable")
    monkeypatch.setattr("autoskillit.cli._update.detect_install", lambda: info)
    monkeypatch.setattr("autoskillit.cli._update.terminal_guard", FakeTG)
    monkeypatch.setattr("autoskillit.cli._update.subprocess.run", lambda *a, **kw: None)
    monkeypatch.setattr("autoskillit.cli._update._fetch_latest_version", lambda *a, **kw: "0.9.0")

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
    assert "still" in combined or "unchanged" in combined or "still 0.7.77" in combined

    # Verify binary_snoozed disk state records the correct attempted_version
    state = _read_dismiss_state(tmp_path)
    assert "binary_snoozed" in state, (
        f"Expected 'binary_snoozed' key in dismiss state after failed update; got {list(state)}"
    )
    snooze = state["binary_snoozed"]
    assert isinstance(snooze, dict), f"Expected dict for binary_snoozed; got {type(snooze)}"
    assert snooze.get("attempted_version") == "0.9.0", (
        f"Expected attempted_version='0.9.0' (from _fetch_latest_version); "
        f"got {snooze.get('attempted_version')!r}"
    )


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
    monkeypatch.setattr("autoskillit.cli._update.subprocess.run", lambda *a, **kw: None)

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
