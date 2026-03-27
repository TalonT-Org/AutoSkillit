"""Tests for cli/_stale_check.py — stale-install detection."""

from __future__ import annotations

import io
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

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


# SC-8: dismissed within 7 days → is_dismissed returns True
def test_is_dismissed_within_window(tmp_path: Path) -> None:
    from autoskillit.cli._stale_check import _is_dismissed

    recently = datetime.now(UTC) - timedelta(days=1)
    state: dict[str, object] = {
        "binary": {"dismissed_at": recently.isoformat(), "dismissed_version": "1.2.3"},
    }
    assert _is_dismissed(state, "binary", "1.2.3") is True


# SC-9: dismissed > 7 days ago → expired, returns False
def test_is_dismissed_expired(tmp_path: Path) -> None:
    from autoskillit.cli._stale_check import _is_dismissed

    old = datetime.now(UTC) - timedelta(days=8)
    state: dict[str, object] = {
        "binary": {"dismissed_at": old.isoformat(), "dismissed_version": "1.2.3"},
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
    from autoskillit.cli._stale_check import run_stale_check

    run_stale_check()
    assert "fetch" not in calls


# SC-12: run_stale_check is no-op when stdin is not a tty
def test_run_stale_check_skips_non_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    calls: list[str] = []
    import autoskillit.cli._stale_check as _sc

    monkeypatch.setattr(_sc, "_fetch_latest_version", lambda *a: calls.append("fetch") or None)
    from autoskillit.cli._stale_check import run_stale_check

    run_stale_check()
    assert "fetch" not in calls


# SC-13: run_stale_check writes dismiss state when user says no to binary update
def test_run_stale_check_writes_dismiss_on_no(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CLAUDECODE", raising=False)
    fake_stdin = io.StringIO("n\n")
    monkeypatch.setattr(sys, "stdin", fake_stdin)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    import autoskillit
    import autoskillit.cli._stale_check as _sc

    monkeypatch.setattr(autoskillit, "__version__", "0.5.0")
    monkeypatch.setattr(_sc, "_fetch_latest_version", lambda dev_mode: "0.9.0")
    monkeypatch.setattr(_sc, "is_dev_mode", lambda home=None: False)
    import autoskillit.cli._doctor as _doctor

    monkeypatch.setattr(_doctor, "_count_hook_registry_drift", lambda p: 0)
    from autoskillit.cli._stale_check import _read_dismiss_state, run_stale_check

    run_stale_check(home=tmp_path)
    state = _read_dismiss_state(tmp_path)
    binary_entry = state.get("binary")
    assert isinstance(binary_entry, dict)
    assert binary_entry.get("dismissed_version") == "0.9.0"
