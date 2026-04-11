"""Tests for cli/_stale_check.py — stale-install detection."""

from __future__ import annotations

import io
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from autoskillit.hook_registry import HookDriftResult


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
    monkeypatch.setattr(_sc, "_fetch_latest_version", lambda *args, **kwargs: "0.9.0")
    monkeypatch.setattr(_sc, "is_dev_mode", lambda home=None: False)
    import autoskillit.cli as _cli

    monkeypatch.setattr(
        _cli, "_count_hook_registry_drift", lambda p: HookDriftResult(missing=0, orphaned=0)
    )
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
    monkeypatch.setattr(_sc, "_fetch_latest_version", lambda *args, **kwargs: "9.9.9")
    monkeypatch.setattr(_sc, "is_dev_mode", lambda home=None: False)
    import autoskillit.cli as _cli

    monkeypatch.setattr(
        _cli, "_count_hook_registry_drift", lambda p: HookDriftResult(missing=0, orphaned=0)
    )

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
    monkeypatch.setattr(_sc, "_fetch_latest_version", lambda *args, **kwargs: None)
    import autoskillit.cli as _cli

    monkeypatch.setattr(
        _cli, "_count_hook_registry_drift", lambda p: HookDriftResult(missing=3, orphaned=0)
    )
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
    monkeypatch.setattr(_sc, "_fetch_latest_version", lambda *args, **kwargs: "99.0.0")
    monkeypatch.setattr(_sc, "_read_dismiss_state", lambda home: {})
    monkeypatch.setattr(_sc, "_write_dismiss_state", lambda home, state: None)
    # Force non-editable path so this test continues to assert uv tool install --force
    monkeypatch.setattr(_sc, "_detect_install_type", lambda: ("tool", None))

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
    monkeypatch.setattr(
        _sc, "_fetch_latest_version", lambda *args, **kwargs: "0.6.7"
    )  # no binary prompt
    monkeypatch.setattr(_sc.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(_sc.sys.stdout, "isatty", lambda: True)
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_SKIP_STALE_CHECK", raising=False)

    import autoskillit as _pkg

    monkeypatch.setattr(_pkg, "__version__", "0.6.7")

    written: dict[str, object] = {}
    monkeypatch.setattr(_sc, "_write_dismiss_state", lambda home, state: written.update(state))
    monkeypatch.setattr(
        "autoskillit.cli._count_hook_registry_drift",
        lambda settings_path: HookDriftResult(missing=2, orphaned=0),
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
    monkeypatch.setattr(_sc, "_fetch_latest_version", lambda *args, **kwargs: "99.0.0")
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
    monkeypatch.setattr(_sc, "_fetch_latest_version", lambda *args, **kwargs: "99.0.0")
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


# SC-B-2: is_dev_mode() returns False when pkg_root() is inside a git worktree
def test_is_dev_mode_git_file_worktree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """is_dev_mode() returns False when pkg_root() is inside a git worktree
    (i.e., .git is a file, not a directory)."""
    import autoskillit.cli._stale_check as _sc

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    worktree_dir = repo_root / "src" / "autoskillit"
    worktree_dir.mkdir(parents=True)
    # .git as a file = worktree indicator
    (repo_root / ".git").write_text("gitdir: /some/real/repo/.git/worktrees/foo")

    monkeypatch.setattr(_sc, "pkg_root", lambda: worktree_dir)

    result = _sc.is_dev_mode(home=tmp_path)
    assert result is False, (
        "is_dev_mode() must return False when pkg_root() is inside a git worktree (.git is a file)"
    )


# SC-B-3: dev-mode Y-path dispatches editable install command when direct_url.json reports editable
def test_run_stale_check_dev_mode_editable_install_y_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Dev-mode Y-path with editable install must dispatch 'uv pip install -e <dir>'
    instead of 'uv tool install --force'."""
    import importlib.metadata

    import autoskillit.cli._stale_check as _sc

    monkeypatch.setattr(_sc, "is_dev_mode", lambda home=None: True)
    monkeypatch.setattr(_sc, "_fetch_latest_version", lambda *args, **kwargs: "99.0.0")
    monkeypatch.setattr(_sc, "_read_dismiss_state", lambda home: {})
    monkeypatch.setattr(_sc.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(_sc.sys.stdout, "isatty", lambda: True)
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_SKIP_STALE_CHECK", raising=False)

    import autoskillit as _pkg

    monkeypatch.setattr(_pkg, "__version__", "0.6.7")

    # Simulate editable install: direct_url.json reports editable=True
    fake_dist = MagicMock()
    fake_dist.read_text.return_value = (
        '{"url": "file:///home/user/autoskillit", "dir_info": {"editable": true}}'
    )
    monkeypatch.setattr(
        importlib.metadata.Distribution,
        "from_name",
        staticmethod(lambda name: fake_dist),
    )
    # Simulate successful update
    monkeypatch.setattr(importlib.metadata, "version", lambda name: "99.0.0")

    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> object:
        calls.append(cmd)
        return type("R", (), {"returncode": 0})()

    monkeypatch.setattr(_sc.subprocess, "run", fake_run)
    monkeypatch.setattr("builtins.input", lambda _: "y")

    _sc.run_stale_check(home=tmp_path)

    uv_calls = [c for c in calls if c[0] == "uv"]
    assert len(uv_calls) == 1
    assert uv_calls[0][:3] == ["uv", "pip", "install"], (
        f"Editable install should use 'uv pip install -e', got: {uv_calls[0]}"
    )
    assert "-e" in uv_calls[0]
    assert "/home/user/autoskillit" in uv_calls[0]
    # Must NOT use uv tool install --force for editable installs
    assert not any("tool" in str(c) and "install" in str(c) for c in uv_calls)


# SC-23: hooks YES-path must write state and not re-prompt on second call
def test_run_stale_check_hooks_y_path_writes_state_and_returns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """After answering Y to the hook-drift prompt:
    1. _write_dismiss_state must be called (dismiss or snooze)
    2. A second call to run_stale_check() must NOT prompt again

    Regression: the hooks YES-path ran the install subprocess but wrote no state
    and did not return, causing an infinite prompt loop.
    """
    import autoskillit.cli._stale_check as _sc

    monkeypatch.setattr(_sc, "is_dev_mode", lambda home=None: False)
    monkeypatch.setattr(_sc, "_fetch_latest_version", lambda *args, **kwargs: None)
    monkeypatch.setattr(_sc.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(_sc.sys.stdout, "isatty", lambda: True)
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_SKIP_STALE_CHECK", raising=False)

    written_states: list[dict[str, object]] = []

    def _tracking_write(home: Path, state: dict[str, object]) -> None:
        written_states.append(dict(state))
        # Actually persist so _read_dismiss_state picks up state on second call
        state_file = home / ".autoskillit" / "update_check.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps(state))

    monkeypatch.setattr(_sc, "_write_dismiss_state", _tracking_write)
    monkeypatch.setattr(
        "autoskillit.cli._count_hook_registry_drift",
        lambda settings_path: HookDriftResult(missing=3, orphaned=0),
    )

    def fake_run(cmd: list[str], **kwargs: object) -> object:
        return type("R", (), {"returncode": 0})()

    monkeypatch.setattr(_sc.subprocess, "run", fake_run)

    input_calls = []

    def fake_input(prompt: str = "") -> str:
        input_calls.append(prompt)
        return "y"

    monkeypatch.setattr("builtins.input", fake_input)

    # First call: should prompt and write state
    _sc.run_stale_check(home=tmp_path)

    assert len(input_calls) == 1, (
        f"Expected exactly 1 input prompt on first call, got {len(input_calls)}"
    )
    assert len(written_states) >= 1, (
        "hooks YES-path must write dismiss/snooze state after install, "
        f"but _write_dismiss_state was called {len(written_states)} times"
    )

    # Second call: must NOT prompt again (snooze state persisted to disk)
    prompt_count_before = len(input_calls)
    _sc.run_stale_check(home=tmp_path)
    assert len(input_calls) == prompt_count_before, (
        "A second call to run_stale_check() must NOT prompt again, "
        f"but {len(input_calls) - prompt_count_before} new prompt(s) appeared"
    )


# ---------------------------------------------------------------------------
# Phase 1 — Fetch cache tests (_fetch_with_cache, _fetch_latest_version)
# ---------------------------------------------------------------------------


def _make_mock_client(
    *,
    status_code: int = 200,
    json_data: dict | None = None,
    etag: str | None = None,
    side_effect: BaseException | None = None,
) -> MagicMock:
    """Build a MagicMock httpx.Client context manager."""
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = json_data if json_data is not None else {"tag_name": "v1.0.0"}
    response.headers = {"ETag": etag} if etag else {}

    client = MagicMock()
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)
    if side_effect is not None:
        client.get = MagicMock(side_effect=side_effect)
    else:
        client.get = MagicMock(return_value=response)
    return client


def test_fetch_latest_version_uses_cache_within_ttl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Second call within TTL must use cached response without hitting network."""
    import json
    import time

    import httpx

    import autoskillit.cli._stale_check as _sc

    url = "https://api.github.com/test_within_ttl"
    # Pre-populate cache with a fresh entry (cached 10s ago, TTL=3600)
    fresh_entry = {"body": {"tag_name": "v1.0.0"}, "etag": None, "cached_at": time.time() - 10}
    cache = {url: fresh_entry}
    cache_path = tmp_path / ".autoskillit" / _sc._FETCH_CACHE_FILE
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache), encoding="utf-8")

    mock_client = _make_mock_client()
    monkeypatch.setattr(httpx, "Client", MagicMock(return_value=mock_client))

    result1 = _sc._fetch_with_cache(url, home=tmp_path, ttl=3600)
    result2 = _sc._fetch_with_cache(url, home=tmp_path, ttl=3600)

    assert mock_client.get.call_count == 0, (
        f"Expected 0 network calls within TTL, got {mock_client.get.call_count}"
    )
    assert result1 == result2 == {"tag_name": "v1.0.0"}


def test_fetch_cache_expires_after_ttl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When cache entry is older than TTL, a new network request must be made."""
    import json
    import time

    import httpx

    import autoskillit.cli._stale_check as _sc

    url = "https://api.github.com/test_expires"
    # Stale entry: cached 90s ago, TTL=60s
    stale_entry = {"body": {"tag_name": "v0.5.0"}, "etag": None, "cached_at": time.time() - 90}
    cache = {url: stale_entry}
    cache_path = tmp_path / ".autoskillit" / _sc._FETCH_CACHE_FILE
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache), encoding="utf-8")

    mock_client = _make_mock_client(json_data={"tag_name": "v1.0.0"})
    monkeypatch.setattr(httpx, "Client", MagicMock(return_value=mock_client))

    result = _sc._fetch_with_cache(url, home=tmp_path, ttl=60)

    assert mock_client.get.call_count == 1, (
        f"Expected 1 network call after TTL expiry, got {mock_client.get.call_count}"
    )
    assert result == {"tag_name": "v1.0.0"}


def test_fetch_cache_respects_env_var_ttl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """AUTOSKILLIT_FETCH_CACHE_TTL_SECONDS=60 is honored.

    50s-old entry stays cached; 70s-old misses.
    """
    import json
    import time

    import httpx

    import autoskillit.cli._stale_check as _sc

    monkeypatch.setenv("AUTOSKILLIT_FETCH_CACHE_TTL_SECONDS", "60")
    url = "https://api.github.com/test_env_ttl"

    # Within TTL: cached 50s ago
    fresh_entry = {"body": {"tag_name": "v1.0.0"}, "etag": None, "cached_at": time.time() - 50}
    cache_path = tmp_path / ".autoskillit" / _sc._FETCH_CACHE_FILE
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps({url: fresh_entry}), encoding="utf-8")

    mock_client = _make_mock_client()
    monkeypatch.setattr(httpx, "Client", MagicMock(return_value=mock_client))

    _sc._fetch_with_cache(url, home=tmp_path)  # no explicit ttl → reads env var
    assert mock_client.get.call_count == 0, "Within 60s TTL: must not hit network"

    # Past TTL: cached 70s ago
    stale_entry = {"body": {"tag_name": "v1.0.0"}, "etag": None, "cached_at": time.time() - 70}
    cache_path.write_text(json.dumps({url: stale_entry}), encoding="utf-8")

    mock_client2 = _make_mock_client()
    monkeypatch.setattr(httpx, "Client", MagicMock(return_value=mock_client2))

    _sc._fetch_with_cache(url, home=tmp_path)
    assert mock_client2.get.call_count == 1, "After 60s TTL: must hit network once"


def test_fetch_sends_github_token_auth_header(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With GITHUB_TOKEN set, request must include Authorization: Bearer <token>."""
    import httpx

    import autoskillit.cli._stale_check as _sc

    monkeypatch.setenv("GITHUB_TOKEN", "abc123token")
    mock_client = _make_mock_client()
    monkeypatch.setattr(httpx, "Client", MagicMock(return_value=mock_client))

    _sc._fetch_with_cache("https://api.github.com/test_auth", home=tmp_path)

    assert mock_client.get.call_count == 1
    call_headers = mock_client.get.call_args[1].get("headers", {})
    assert call_headers.get("Authorization") == "Bearer abc123token"


def test_fetch_sends_if_none_match_when_cached_etag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When cache has an etag, request must include If-None-Match header."""
    import json
    import time

    import httpx

    import autoskillit.cli._stale_check as _sc

    url = "https://api.github.com/test_etag"
    # Stale entry (ttl=0) but with an etag
    stale_entry = {
        "body": {"tag_name": "v1.0.0"},
        "etag": 'W/"abc-etag-123"',
        "cached_at": time.time() - 120,
    }
    cache_path = tmp_path / ".autoskillit" / _sc._FETCH_CACHE_FILE
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps({url: stale_entry}), encoding="utf-8")

    mock_client = _make_mock_client()
    monkeypatch.setattr(httpx, "Client", MagicMock(return_value=mock_client))

    _sc._fetch_with_cache(url, home=tmp_path, ttl=60)

    assert mock_client.get.call_count == 1
    call_headers = mock_client.get.call_args[1].get("headers", {})
    assert call_headers.get("If-None-Match") == 'W/"abc-etag-123"'


def test_fetch_304_response_returns_cached_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On 304 response, the previously-cached body must be returned without re-parsing."""
    import json
    import time

    import httpx

    import autoskillit.cli._stale_check as _sc

    url = "https://api.github.com/test_304"
    cached_body = {"tag_name": "v0.9.0", "description": "cached-payload"}
    stale_entry = {"body": cached_body, "etag": "etag-abc", "cached_at": time.time() - 120}
    cache_path = tmp_path / ".autoskillit" / _sc._FETCH_CACHE_FILE
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps({url: stale_entry}), encoding="utf-8")

    mock_client = _make_mock_client(status_code=304)
    monkeypatch.setattr(httpx, "Client", MagicMock(return_value=mock_client))

    result = _sc._fetch_with_cache(url, home=tmp_path, ttl=60)
    assert result == cached_body, f"Expected cached body on 304, got {result}"


def test_fetch_uses_2s_connect_1s_read_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """_HTTP_TIMEOUT must be Timeout(connect=2.0, read=1.0, write=5.0, pool=1.0)."""
    import httpx

    import autoskillit.cli._stale_check as _sc

    timeout = _sc._HTTP_TIMEOUT
    assert isinstance(timeout, httpx.Timeout), f"Expected httpx.Timeout, got {type(timeout)}"
    assert timeout.connect == 2.0, f"Expected connect=2.0, got {timeout.connect}"
    assert timeout.read == 1.0, f"Expected read=1.0, got {timeout.read}"
    assert timeout.write == 5.0, f"Expected write=5.0, got {timeout.write}"
    assert timeout.pool == 1.0, f"Expected pool=1.0, got {timeout.pool}"


def test_fetch_sends_modern_github_api_version_header(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Request must include X-GitHub-Api-Version: 2022-11-28 and modern Accept header."""
    import httpx

    import autoskillit.cli._stale_check as _sc

    mock_client = _make_mock_client()
    monkeypatch.setattr(httpx, "Client", MagicMock(return_value=mock_client))

    _sc._fetch_with_cache("https://api.github.com/test_api_version", home=tmp_path)

    assert mock_client.get.call_count == 1
    call_headers = mock_client.get.call_args[1].get("headers", {})
    assert call_headers.get("X-GitHub-Api-Version") == "2022-11-28"
    accept = call_headers.get("Accept", "")
    assert accept == "application/vnd.github+json"
    assert ".v3" not in accept, "Accept header must use modern form (no .v3 suffix)"


def test_fetch_sends_user_agent_with_package_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """User-Agent header must start with 'autoskillit/' followed by the installed version."""
    import httpx

    import autoskillit.cli._stale_check as _sc

    mock_client = _make_mock_client()
    monkeypatch.setattr(httpx, "Client", MagicMock(return_value=mock_client))

    _sc._fetch_with_cache("https://api.github.com/test_ua", home=tmp_path)

    assert mock_client.get.call_count == 1
    call_headers = mock_client.get.call_args[1].get("headers", {})
    user_agent = call_headers.get("User-Agent", "")
    assert user_agent.startswith("autoskillit/"), (
        f"User-Agent must start with 'autoskillit/', got: {user_agent!r}"
    )


def test_fetch_scrubs_authorization_header_from_logged_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """When GITHUB_TOKEN is set and a network error occurs, the token must not appear in logs."""
    import logging

    import httpx

    import autoskillit.cli._stale_check as _sc

    monkeypatch.setenv("GITHUB_TOKEN", "secret123")
    mock_client = _make_mock_client(
        side_effect=httpx.ConnectError("Connection refused Bearer secret123 endpoint")
    )
    monkeypatch.setattr(httpx, "Client", MagicMock(return_value=mock_client))

    with caplog.at_level(logging.DEBUG, logger="autoskillit.cli._stale_check"):
        result = _sc._fetch_with_cache("https://api.github.com/test_scrub", home=tmp_path)

    assert result is None
    assert "secret123" not in caplog.text, (
        f"GITHUB_TOKEN value leaked into log output! Log text: {caplog.text!r}"
    )


def test_fetch_fails_fast_offline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """On ConnectError, _fetch_latest_version must return None without raising."""
    import httpx

    import autoskillit.cli._stale_check as _sc

    mock_client = _make_mock_client(side_effect=httpx.ConnectError("offline"))
    monkeypatch.setattr(httpx, "Client", MagicMock(return_value=mock_client))

    result = _sc._fetch_latest_version(dev_mode=False, home=tmp_path)
    assert result is None, f"Expected None on ConnectError, got {result!r}"
