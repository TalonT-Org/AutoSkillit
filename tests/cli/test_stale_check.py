"""Tests for cli/_stale_check.py — stale-install detection."""

from __future__ import annotations

import io
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest


# SC-1: dev mode via ~/.autoskillit/dev marker
def test_is_dev_mode_home_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / ".autoskillit").mkdir()
    (tmp_path / ".autoskillit" / "dev").touch()
    from autoskillit.cli._stale_check import is_dev_mode

    assert is_dev_mode(home=tmp_path) is True


# SC-2: dev mode via project-level .autoskillit/dev marker
def test_is_dev_mode_project_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".autoskillit").mkdir()
    (tmp_path / ".autoskillit" / "dev").touch()
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home_no_marker")
    from autoskillit.cli._stale_check import is_dev_mode

    assert is_dev_mode(home=tmp_path / "home_no_marker") is True


# SC-3: dev mode via git main checkout (.git dir, not file)
def test_is_dev_mode_git_checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".git").mkdir()  # .git dir = main checkout
    import autoskillit.cli._stale_check as _sc

    monkeypatch.setattr(_sc, "pkg_root", lambda: tmp_path / "src" / "autoskillit")
    assert _sc.is_dev_mode(home=tmp_path / "home_no_marker") is True


# SC-4: no dev markers → False
def test_is_dev_mode_no_markers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    import autoskillit.cli._stale_check as _sc

    monkeypatch.setattr(_sc, "pkg_root", lambda: tmp_path)  # no .git dir, no .autoskillit/dev
    assert _sc.is_dev_mode(home=tmp_path) is False


# SC-5: read_dismiss_state — missing file → {}
def test_read_dismiss_state_empty(tmp_path: Path) -> None:
    from autoskillit.cli._stale_check import _read_dismiss_state

    assert _read_dismiss_state(tmp_path) == {}


# SC-6: read_dismiss_state — malformed JSON → {}
def test_read_dismiss_state_malformed(tmp_path: Path) -> None:
    state_file = tmp_path / ".autoskillit" / "update_check.json"
    state_file.parent.mkdir()
    state_file.write_text("not json!")
    from autoskillit.cli._stale_check import _read_dismiss_state

    assert _read_dismiss_state(tmp_path) == {}


# SC-7: write_dismiss_state round-trips correctly
def test_write_dismiss_state_roundtrip(tmp_path: Path) -> None:
    from autoskillit.cli._stale_check import _read_dismiss_state, _write_dismiss_state

    state: dict[str, object] = {
        "binary": {"dismissed_at": "2026-03-26T00:00:00+00:00", "dismissed_version": "1.2.3"},
    }
    _write_dismiss_state(tmp_path, state)
    result = _read_dismiss_state(tmp_path)
    assert result == state


# SC-8: dismissed within window → is_dismissed returns True
def test_is_dismissed_within_window(tmp_path: Path) -> None:
    from autoskillit.cli._stale_check import _DISMISS_WINDOW, _is_dismissed

    recently = datetime.now(UTC) - (_DISMISS_WINDOW - timedelta(hours=1))
    state: dict[str, object] = {
        "binary": {"dismissed_at": recently.isoformat(), "dismissed_version": "1.2.3"},
    }
    assert _is_dismissed(state, "binary", "1.2.3") is True


# SC-9: dismissed beyond window → expired, returns False
def test_is_dismissed_expired(tmp_path: Path) -> None:
    from autoskillit.cli._stale_check import _DISMISS_WINDOW, _is_dismissed

    long_ago = datetime.now(UTC) - (_DISMISS_WINDOW + timedelta(hours=1))
    state: dict[str, object] = {
        "binary": {"dismissed_at": long_ago.isoformat(), "dismissed_version": "1.2.3"},
    }
    assert _is_dismissed(state, "binary", "1.2.3") is False


# SC-10: version beyond dismissed_version resets dismissal even within window
def test_is_dismissed_newer_version_resets(tmp_path: Path) -> None:
    from autoskillit.cli._stale_check import _is_dismissed

    recently = datetime.now(UTC) - timedelta(days=1)
    state: dict[str, object] = {
        "binary": {"dismissed_at": recently.isoformat(), "dismissed_version": "1.2.3"},
    }
    # latest is 1.3.0, beyond dismissed 1.2.3 → not dismissed
    assert _is_dismissed(state, "binary", "1.3.0") is False


# SC-11: run_stale_check is no-op when CLAUDECODE=1
def test_run_stale_check_skips_claudecode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAUDECODE", "1")
    calls: list[str] = []
    import autoskillit.cli._stale_check as _sc

    monkeypatch.setattr(_sc, "_fetch_latest_version", lambda *a: calls.append("fetch") or None)
    _sc.run_stale_check()
    assert "fetch" not in calls


# SC-12: run_stale_check is no-op when stdin is not a tty
def test_run_stale_check_skips_non_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    calls: list[str] = []
    import autoskillit.cli._stale_check as _sc

    monkeypatch.setattr(_sc, "_fetch_latest_version", lambda *a: calls.append("fetch") or None)
    _sc.run_stale_check()
    assert "fetch" not in calls


# SC-13: run_stale_check writes dismiss state when user says no to binary update
def test_run_stale_check_writes_dismiss_on_no(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CLAUDECODE", raising=False)
    fake_stdin = io.StringIO("n\n")
    fake_stdout = io.StringIO()
    import autoskillit
    import autoskillit.cli._stale_check as _sc

    # Patch at module boundary — swap fakes in, never mutate real stdin/stdout
    monkeypatch.setattr(_sc.sys, "stdin", fake_stdin)
    monkeypatch.setattr(_sc.sys, "stdout", fake_stdout)
    monkeypatch.setattr(fake_stdin, "isatty", lambda: True)
    monkeypatch.setattr(fake_stdout, "isatty", lambda: True)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(autoskillit, "__version__", "0.5.0")
    monkeypatch.setattr(_sc, "_fetch_latest_version", lambda dev_mode: "0.9.0")
    monkeypatch.setattr(_sc, "is_dev_mode", lambda home=None: False)
    import autoskillit.cli._doctor as _doctor

    monkeypatch.setattr(_doctor, "_count_hook_registry_drift", lambda p: 0)
    from autoskillit.cli._stale_check import _read_dismiss_state

    _sc.run_stale_check(home=tmp_path)
    state = _read_dismiss_state(tmp_path)
    binary_entry = state.get("binary")
    assert isinstance(binary_entry, dict)
    assert binary_entry.get("dismissed_version") == "0.9.0"


# SC-14: binary update Y-path — subprocess calls receive AUTOSKILLIT_SKIP_STALE_CHECK env
def test_run_stale_check_binary_update_y_path_injects_guard_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_SKIP_STALE_CHECK", raising=False)
    fake_stdin = io.StringIO("y\n")
    fake_stdout = io.StringIO()
    import autoskillit
    import autoskillit.cli._stale_check as _sc

    monkeypatch.setattr(_sc.sys, "stdin", fake_stdin)
    monkeypatch.setattr(_sc.sys, "stdout", fake_stdout)
    monkeypatch.setattr(fake_stdin, "isatty", lambda: True)
    monkeypatch.setattr(fake_stdout, "isatty", lambda: True)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(autoskillit, "__version__", "0.1.0")
    monkeypatch.setattr(_sc, "_fetch_latest_version", lambda dev_mode: "9.9.9")
    monkeypatch.setattr(_sc, "is_dev_mode", lambda home=None: False)
    import autoskillit.cli._doctor as _doctor

    monkeypatch.setattr(_doctor, "_count_hook_registry_drift", lambda p: 0)

    mock_run = MagicMock(return_value=subprocess.CompletedProcess([], 0))
    monkeypatch.setattr(_sc.subprocess, "run", mock_run)
    monkeypatch.setattr(_sc, "_is_dismissed", lambda *a, **kw: False)

    _sc.run_stale_check(home=tmp_path)

    assert mock_run.call_count == 2
    assert mock_run.call_args_list[0][0][0] == ["uv", "tool", "upgrade", "autoskillit"]
    assert mock_run.call_args_list[1][0][0] == ["autoskillit", "install"]
    assert "AUTOSKILLIT_SKIP_STALE_CHECK" in mock_run.call_args_list[0].kwargs["env"]
    assert "AUTOSKILLIT_SKIP_STALE_CHECK" in mock_run.call_args_list[1].kwargs["env"]
    assert mock_run.call_args_list[0].kwargs["env"]["AUTOSKILLIT_SKIP_STALE_CHECK"] == "1"
    assert mock_run.call_args_list[1].kwargs["env"]["AUTOSKILLIT_SKIP_STALE_CHECK"] == "1"


# SC-15: hook drift Y-path — subprocess call receives AUTOSKILLIT_SKIP_STALE_CHECK env
def test_run_stale_check_hook_drift_y_path_injects_guard_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_SKIP_STALE_CHECK", raising=False)
    fake_stdin = io.StringIO("y\n")
    fake_stdout = io.StringIO()
    import autoskillit.cli._stale_check as _sc

    monkeypatch.setattr(_sc.sys, "stdin", fake_stdin)
    monkeypatch.setattr(_sc.sys, "stdout", fake_stdout)
    monkeypatch.setattr(fake_stdin, "isatty", lambda: True)
    monkeypatch.setattr(fake_stdout, "isatty", lambda: True)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(_sc, "_fetch_latest_version", lambda dev_mode: None)
    import autoskillit.cli._doctor as _doctor

    monkeypatch.setattr(_doctor, "_count_hook_registry_drift", lambda p: 3)
    monkeypatch.setattr(_sc, "_is_dismissed", lambda *a, **kw: False)

    mock_run = MagicMock(return_value=subprocess.CompletedProcess([], 0))
    monkeypatch.setattr(_sc.subprocess, "run", mock_run)

    _sc.run_stale_check(home=tmp_path)

    assert mock_run.call_count == 1
    assert mock_run.call_args_list[0][0][0] == ["autoskillit", "install"]
    assert "AUTOSKILLIT_SKIP_STALE_CHECK" in mock_run.call_args_list[0].kwargs["env"]
    assert mock_run.call_args_list[0].kwargs["env"]["AUTOSKILLIT_SKIP_STALE_CHECK"] == "1"


# SC-16: re-entry guard — run_stale_check returns immediately when env var set
def test_run_stale_check_returns_immediately_when_skip_env_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import autoskillit.cli._stale_check as _sc

    monkeypatch.setenv("AUTOSKILLIT_SKIP_STALE_CHECK", "1")
    monkeypatch.setattr(_sc.sys, "stdin", MagicMock(isatty=lambda: True))
    monkeypatch.setattr(_sc.sys, "stdout", MagicMock(isatty=lambda: True))
    calls: list[str] = []

    monkeypatch.setattr(_sc, "_fetch_latest_version", lambda *a: calls.append("fetch") or None)
    mock_run = MagicMock()
    monkeypatch.setattr(_sc.subprocess, "run", mock_run)

    _sc.run_stale_check()

    assert "fetch" not in calls
    mock_run.assert_not_called()


# SC-17: dev-mode Y-path calls uv tool install --force, not uv tool upgrade
def test_run_stale_check_dev_mode_y_path_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Dev-mode Y-path must call uv tool install --force, not uv tool upgrade."""
    import autoskillit.cli._stale_check as _sc
    from autoskillit.cli._stale_check import _INSTALL_FROM_INTEGRATION

    calls: list[tuple[list[str], dict[str, object]]] = []
    monkeypatch.setattr(_sc, "is_dev_mode", lambda home=None: True)
    monkeypatch.setattr(_sc, "_fetch_latest_version", lambda dev_mode: "99.0.0")
    monkeypatch.setattr(_sc, "_read_dismiss_state", lambda home: {})
    monkeypatch.setattr(_sc, "_write_dismiss_state", lambda home, state: None)

    def fake_run(cmd: list[str], **kwargs: object) -> object:
        calls.append((cmd, kwargs))
        return type("R", (), {"returncode": 0})()

    monkeypatch.setattr(_sc.subprocess, "run", fake_run)
    monkeypatch.setattr(_sc.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(_sc.sys.stdout, "isatty", lambda: True)
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_SKIP_STALE_CHECK", raising=False)

    import autoskillit as _pkg

    monkeypatch.setattr(_pkg, "__version__", "0.6.7")
    monkeypatch.setattr("builtins.input", lambda _: "y")

    _sc.run_stale_check(home=tmp_path)

    uv_calls = [(c, kw) for c, kw in calls if c[0] == "uv"]
    assert len(uv_calls) == 1
    assert uv_calls[0][0] == ["uv", "tool", "install", "--force", _INSTALL_FROM_INTEGRATION], (
        f"Expected dev-mode install command, got: {uv_calls[0][0]}"
    )
    assert uv_calls[0][1].get("env", {}).get("AUTOSKILLIT_SKIP_STALE_CHECK") == "1", (  # type: ignore[union-attr]
        "Dev-mode uv call must carry AUTOSKILLIT_SKIP_STALE_CHECK=1 in env"
    )
    autoskillit_calls = [c for c, kw in calls if c[0] == "autoskillit"]
    assert autoskillit_calls == [["autoskillit", "install"]]


# SC-18: hooks N-path writes state["hooks"], not state["binary"]
def test_run_stale_check_hooks_n_path_writes_dismiss(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Answering N to the hook-drift prompt writes state['hooks'], not state['binary']."""
    import autoskillit.cli._stale_check as _sc

    monkeypatch.setattr(_sc, "is_dev_mode", lambda home=None: False)
    monkeypatch.setattr(_sc, "_fetch_latest_version", lambda dev_mode: "0.6.7")  # no binary prompt
    monkeypatch.setattr(_sc.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(_sc.sys.stdout, "isatty", lambda: True)
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_SKIP_STALE_CHECK", raising=False)

    import autoskillit as _pkg

    monkeypatch.setattr(_pkg, "__version__", "0.6.7")

    written: dict[str, object] = {}
    monkeypatch.setattr(_sc, "_write_dismiss_state", lambda home, state: written.update(state))
    monkeypatch.setattr(
        "autoskillit.cli._doctor._count_hook_registry_drift",
        lambda settings_path: 2,
    )
    monkeypatch.setattr("builtins.input", lambda _: "n")

    _sc.run_stale_check(home=tmp_path)

    assert "hooks" in written, "hooks dismiss state not written on N answer"
    assert "dismissed_at" in written["hooks"]  # type: ignore[index]
    assert "dismissed_version" in written["hooks"]  # type: ignore[index]
    assert "binary" not in written, (
        "binary dismiss should not be written when no binary update needed"
    )


# SC-19: Y-path + silent failure (version unchanged) writes binary_snoozed, not binary
def test_run_stale_check_y_path_silent_failure_writes_snooze(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """
    After Y-path subprocesses complete, if importlib.metadata.version() reports
    the same version as before the update, a snooze record (not a dismiss record)
    must be written.
    """
    import importlib.metadata

    import autoskillit.cli._stale_check as _sc

    monkeypatch.setattr(_sc, "is_dev_mode", lambda home=None: False)
    monkeypatch.setattr(_sc, "_fetch_latest_version", lambda dev_mode: "99.0.0")
    monkeypatch.setattr(_sc, "_read_dismiss_state", lambda home: {})
    monkeypatch.setattr(_sc.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(_sc.sys.stdout, "isatty", lambda: True)
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_SKIP_STALE_CHECK", raising=False)

    import autoskillit as _pkg

    monkeypatch.setattr(_pkg, "__version__", "0.6.7")
    # Simulate failed update: importlib.metadata still reports old version
    monkeypatch.setattr(importlib.metadata, "version", lambda name: "0.6.7")

    def fake_run(cmd: list[str], **kwargs: object) -> object:
        return type("R", (), {"returncode": 0})()

    monkeypatch.setattr(_sc.subprocess, "run", fake_run)

    written: dict[str, object] = {}
    monkeypatch.setattr(_sc, "_write_dismiss_state", lambda home, state: written.update(state))
    monkeypatch.setattr("builtins.input", lambda _: "y")

    _sc.run_stale_check(home=tmp_path)

    assert "binary_snoozed" in written, (
        "Expected binary_snoozed state after silent update failure, "
        f"but written state was: {list(written.keys())}"
    )
    assert "binary" not in written, (
        "Dismissed state must NOT be written after a failed Y-path update"
    )
    assert "snoozed_at" in written["binary_snoozed"]  # type: ignore[index]
    assert "attempted_version" in written["binary_snoozed"]  # type: ignore[index]
    assert written["binary_snoozed"]["attempted_version"] == "99.0.0"  # type: ignore[index]


# SC-20a: _is_snoozed() returns False when snooze window has elapsed
def test_is_snoozed_expired(monkeypatch: pytest.MonkeyPatch) -> None:
    """_is_snoozed() returns False when the snooze window has elapsed."""
    from autoskillit.cli._stale_check import _SNOOZE_WINDOW, _is_snoozed

    long_ago = datetime.now(UTC) - (_SNOOZE_WINDOW + timedelta(hours=1))
    state: dict[str, object] = {
        "binary_snoozed": {"snoozed_at": long_ago.isoformat(), "attempted_version": "1.0.0"}
    }
    assert _is_snoozed(state, "binary") is False


# SC-20b: _is_snoozed() returns True when within the snooze window
def test_is_snoozed_within_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """_is_snoozed() returns True when within the snooze window."""
    from autoskillit.cli._stale_check import _SNOOZE_WINDOW, _is_snoozed

    recently = datetime.now(UTC) - (_SNOOZE_WINDOW - timedelta(minutes=10))
    state: dict[str, object] = {
        "binary_snoozed": {"snoozed_at": recently.isoformat(), "attempted_version": "1.0.0"}
    }
    assert _is_snoozed(state, "binary") is True


# SC-21: Y-path success (version advanced) writes no state
def test_run_stale_check_y_path_success_no_state_written(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When Y-path update advances the version, no state is written to update_check.json."""
    import importlib.metadata

    import autoskillit.cli._stale_check as _sc

    monkeypatch.setattr(_sc, "is_dev_mode", lambda home=None: False)
    monkeypatch.setattr(_sc, "_fetch_latest_version", lambda dev_mode: "99.0.0")
    monkeypatch.setattr(_sc, "_read_dismiss_state", lambda home: {})
    monkeypatch.setattr(_sc.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(_sc.sys.stdout, "isatty", lambda: True)
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_SKIP_STALE_CHECK", raising=False)

    import autoskillit as _pkg

    monkeypatch.setattr(_pkg, "__version__", "0.6.7")
    # Simulate successful update: version advanced
    monkeypatch.setattr(importlib.metadata, "version", lambda name: "99.0.0")

    def fake_run(cmd: list[str], **kwargs: object) -> object:
        return type("R", (), {"returncode": 0})()

    monkeypatch.setattr(_sc.subprocess, "run", fake_run)

    write_called: list[object] = []
    monkeypatch.setattr(
        _sc, "_write_dismiss_state", lambda home, state: write_called.append(state)
    )
    monkeypatch.setattr("builtins.input", lambda _: "y")

    _sc.run_stale_check(home=tmp_path)

    assert write_called == [], "No state should be written after a successful Y-path update"


# SC-22: _read_dismiss_state() returns {} for non-dict JSON root
def test_read_dismiss_state_non_dict_json_returns_empty(tmp_path: Path) -> None:
    """_read_dismiss_state returns {} when update_check.json contains non-dict JSON."""
    from autoskillit.cli._stale_check import _read_dismiss_state

    state_dir = tmp_path / ".autoskillit"
    state_dir.mkdir()
    (state_dir / "update_check.json").write_text("[1, 2, 3]", encoding="utf-8")

    result = _read_dismiss_state(tmp_path)
    assert result == {}, f"Expected empty dict for non-dict JSON root, got: {result}"
