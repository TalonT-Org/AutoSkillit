"""Tests for cli/_update_checks.py — UC-3 through UC-10: prompt consolidation,
yes/no paths, dismissal windows, timed_prompt primitives, FORCE env, and passive
notification for dismissed signals."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from autoskillit.cli._install_info import InstallInfo, InstallType
from autoskillit.cli._update_checks import (
    _is_dismissed,
    _read_dismiss_state,
    _write_dismiss_state,
    run_update_checks,
)

from ._update_checks_helpers import _make_integration_info, _make_stable_info

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]

# ---------------------------------------------------------------------------
# UC-3 Prompt consolidation
# ---------------------------------------------------------------------------


def _setup_run_checks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    info: InstallInfo | None = None,
    binary_signal: bool = False,
    hooks_signal: bool = False,
    source_drift_signal: bool = False,
    answer: str = "n",
    current_version: str = "0.7.77",
    state: dict | None = None,
) -> tuple[list[str], list[str]]:
    """Set up mocks for run_update_checks and return (printed_lines, input_calls)."""
    import select as _select_mod

    from autoskillit.cli._update_checks import Signal

    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_SKIP_STALE_CHECK", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_SKIP_UPDATE_CHECK", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_SKIP_SOURCE_DRIFT_CHECK", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_FORCE_UPDATE_CHECK", raising=False)

    fake_stdin = MagicMock()
    fake_stdin.isatty.return_value = True
    fake_stdout = MagicMock()
    fake_stdout.isatty.return_value = True
    monkeypatch.setattr(sys, "stdin", fake_stdin)
    monkeypatch.setattr(sys, "stdout", fake_stdout)

    # timed_prompt uses select.select to implement timeout; mock it to
    # report "stdin is ready" so tests proceed without real file descriptors.
    monkeypatch.setattr(
        _select_mod, "select", lambda rlist, wlist, xlist, timeout=None: (rlist, [], [])
    )

    _info = info or _make_stable_info()
    monkeypatch.setattr("autoskillit.cli._update_checks.detect_install", lambda: _info)

    if state is not None:
        (tmp_path / ".autoskillit").mkdir(parents=True, exist_ok=True)
        (tmp_path / ".autoskillit" / "update_check.json").write_text(
            json.dumps(state), encoding="utf-8"
        )

    import autoskillit as _pkg

    monkeypatch.setattr(_pkg, "__version__", current_version)

    monkeypatch.setattr(
        "autoskillit.cli._update_checks._binary_signal",
        lambda info, home, current: (
            Signal("binary", "New release: 0.9.0 (you have 0.7.77)") if binary_signal else None
        ),
    )
    monkeypatch.setattr(
        "autoskillit.cli._update_checks._hooks_signal",
        lambda settings_path: (
            Signal("hooks", "1 new/changed hook(s) detected") if hooks_signal else None
        ),
    )
    monkeypatch.setattr(
        "autoskillit.cli._update_checks._source_drift_signal",
        lambda info, home: (
            Signal("source_drift", "A newer version is available on the stable branch (aaa..bbb)")
            if source_drift_signal
            else None
        ),
    )
    monkeypatch.setattr(
        "autoskillit.cli._update_checks._claude_settings_path",
        lambda scope: tmp_path / "settings.json",
    )

    printed: list[str] = []
    monkeypatch.setattr(
        "builtins.print", lambda *args, **kw: printed.append(" ".join(str(a) for a in args))
    )

    input_calls: list[str] = []
    monkeypatch.setattr("builtins.input", lambda _="": input_calls.append("called") or answer)

    monkeypatch.setattr("autoskillit.core.any_kitchen_open", lambda **kw: False)

    return printed, input_calls


def test_no_prompt_when_no_conditions_fire(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    printed, input_calls = _setup_run_checks(monkeypatch, tmp_path)
    run_update_checks(home=tmp_path)
    assert not input_calls
    assert not printed, f"No output expected when zero signals fire; got: {printed!r}"


def test_single_prompt_when_only_binary_fires(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    printed, input_calls = _setup_run_checks(monkeypatch, tmp_path, binary_signal=True)
    run_update_checks(home=tmp_path)
    assert len(input_calls) == 1


def test_single_prompt_when_only_hooks_fires(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    printed, input_calls = _setup_run_checks(monkeypatch, tmp_path, hooks_signal=True)
    run_update_checks(home=tmp_path)
    assert len(input_calls) == 1


def test_single_prompt_when_only_source_drift_fires(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    printed, input_calls = _setup_run_checks(monkeypatch, tmp_path, source_drift_signal=True)
    run_update_checks(home=tmp_path)
    assert len(input_calls) == 1


def test_consolidated_prompt_when_binary_plus_hooks_fire(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    printed, input_calls = _setup_run_checks(
        monkeypatch, tmp_path, binary_signal=True, hooks_signal=True
    )
    run_update_checks(home=tmp_path)
    assert len(input_calls) == 1
    combined = " ".join(printed)
    # Should contain 2 bullet lines
    assert combined.count("  - ") == 2


def test_consolidated_prompt_when_all_three_fire(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    printed, input_calls = _setup_run_checks(
        monkeypatch, tmp_path, binary_signal=True, hooks_signal=True, source_drift_signal=True
    )
    run_update_checks(home=tmp_path)
    assert len(input_calls) == 1
    combined = " ".join(printed)
    assert combined.count("  - ") == 3


def test_prompt_never_contains_phrase_source_drift(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    printed, _ = _setup_run_checks(monkeypatch, tmp_path, source_drift_signal=True)
    run_update_checks(home=tmp_path)
    combined = " ".join(printed)
    assert "source drift" not in combined.lower()


def test_prompt_uses_friendly_branch_language(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    printed, _ = _setup_run_checks(monkeypatch, tmp_path, source_drift_signal=True)
    run_update_checks(home=tmp_path)
    combined = " ".join(printed)
    assert "newer version is available on the stable branch" in combined


# ---------------------------------------------------------------------------
# UC-4 Yes path
# ---------------------------------------------------------------------------


def test_yes_runs_upgrade_command_from_install_info_not_hardcoded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from autoskillit.cli._install_info import upgrade_command

    info = _make_stable_info()
    printed, input_calls = _setup_run_checks(
        monkeypatch, tmp_path, binary_signal=True, answer="y", info=info
    )
    run_calls: list[list[str]] = []
    monkeypatch.setattr(
        "autoskillit.cli._update_checks.subprocess.run",
        lambda cmd, **kw: run_calls.append(list(cmd)) or subprocess.CompletedProcess(cmd, 0),
    )
    monkeypatch.setattr("autoskillit.cli._update_checks.terminal_guard", MagicMock())
    monkeypatch.setattr(
        "autoskillit.cli._update_checks._verify_update_result", lambda *a, **kw: True
    )
    monkeypatch.setattr(
        "autoskillit.cli._update_checks._fetch_latest_version", lambda *a, **kw: "0.9.0"
    )
    monkeypatch.setattr("autoskillit.cli._update_checks.perform_restart", lambda: None)
    expected_cmd = upgrade_command(info)
    run_update_checks(home=tmp_path)
    assert expected_cmd in run_calls, (
        f"Expected upgrade command {expected_cmd!r} from upgrade_command(info); got {run_calls!r}"
    )


def test_yes_runs_autoskillit_install_after_upgrade_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    printed, input_calls = _setup_run_checks(monkeypatch, tmp_path, binary_signal=True, answer="y")
    run_calls: list[list[str]] = []

    class FakeTG:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr("autoskillit.cli._update_checks.terminal_guard", FakeTG)
    monkeypatch.setattr(
        "autoskillit.cli._update_checks.subprocess.run",
        lambda cmd, **kw: run_calls.append(list(cmd)) or subprocess.CompletedProcess(cmd, 0),
    )
    monkeypatch.setattr(
        "autoskillit.cli._update_checks._verify_update_result", lambda *a, **kw: True
    )
    monkeypatch.setattr("autoskillit.cli._update_checks.perform_restart", lambda: None)
    run_update_checks(home=tmp_path)
    # ["autoskillit", "install"] must be among the calls
    assert any(cmd[:2] == ["autoskillit", "install"] for cmd in run_calls)


def test_yes_passes_skip_env_to_subprocess(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    printed, input_calls = _setup_run_checks(monkeypatch, tmp_path, binary_signal=True, answer="y")
    env_passed: list[dict] = []

    class FakeTG:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr("autoskillit.cli._update_checks.terminal_guard", FakeTG)
    monkeypatch.setattr(
        "autoskillit.cli._update_checks.subprocess.run",
        lambda cmd, **kw: (
            env_passed.append(kw.get("env", {})) or subprocess.CompletedProcess(cmd, 0)
        ),
    )
    monkeypatch.setattr(
        "autoskillit.cli._update_checks._verify_update_result", lambda *a, **kw: True
    )
    monkeypatch.setattr("autoskillit.cli._update_checks.perform_restart", lambda: None)
    run_update_checks(home=tmp_path)
    for env in env_passed:
        assert env.get("AUTOSKILLIT_SKIP_STALE_CHECK") == "1"
        assert env.get("AUTOSKILLIT_SKIP_UPDATE_CHECK") == "1"
        assert env.get("AUTOSKILLIT_SKIP_SOURCE_DRIFT_CHECK") == "1"


def test_yes_single_invocation_exits_without_any_other_prompt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    printed, input_calls = _setup_run_checks(
        monkeypatch, tmp_path, binary_signal=True, hooks_signal=True, answer="y"
    )

    class FakeTG:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr("autoskillit.cli._update_checks.terminal_guard", FakeTG)
    monkeypatch.setattr(
        "autoskillit.cli._update_checks.subprocess.run",
        lambda *a, **kw: subprocess.CompletedProcess([], 0),
    )
    monkeypatch.setattr(
        "autoskillit.cli._update_checks._verify_update_result", lambda *a, **kw: True
    )
    monkeypatch.setattr("autoskillit.cli._update_checks.perform_restart", lambda: None)
    run_update_checks(home=tmp_path)
    assert len(input_calls) == 1


# ---------------------------------------------------------------------------
# UC-5 No path
# ---------------------------------------------------------------------------


def test_no_writes_single_unified_dismissal_entry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    printed, input_calls = _setup_run_checks(monkeypatch, tmp_path, binary_signal=True, answer="n")
    run_update_checks(home=tmp_path)
    state = _read_dismiss_state(tmp_path)
    assert "update_prompt" in state
    # No legacy sub-keys
    assert "binary" not in state
    assert "hooks" not in state
    assert "source_drift" not in state


def test_no_records_conditions_list_in_dismissal_entry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    printed, input_calls = _setup_run_checks(
        monkeypatch, tmp_path, binary_signal=True, hooks_signal=True, answer="n"
    )
    run_update_checks(home=tmp_path)
    state = _read_dismiss_state(tmp_path)
    entry = state["update_prompt"]
    assert isinstance(entry, dict)
    conditions = entry["conditions"]
    assert "binary" in conditions
    assert "hooks" in conditions


def test_no_prints_expiry_date_line_with_correct_date(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    printed, input_calls = _setup_run_checks(monkeypatch, tmp_path, binary_signal=True, answer="n")
    run_update_checks(home=tmp_path)
    combined = " ".join(printed)
    # Should mention "Dismissed until"
    assert "Dismissed until" in combined
    # And an escape hatch hint
    assert "autoskillit update" in combined or "AUTOSKILLIT_SKIP_STALE_CHECK" in combined


def test_no_prints_escape_hatch_hint(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    printed, input_calls = _setup_run_checks(monkeypatch, tmp_path, binary_signal=True, answer="n")
    run_update_checks(home=tmp_path)
    combined = " ".join(printed)
    assert "autoskillit update" in combined
    assert "AUTOSKILLIT_SKIP_STALE_CHECK=1" in combined


# ---------------------------------------------------------------------------
# UC-6 Branch-aware dismissal windows
# ---------------------------------------------------------------------------


def _dismissed_state(
    ago: timedelta,
    version: str = "0.7.77",
    conditions: list[str] | None = None,
) -> dict:
    return {
        "update_prompt": {
            "dismissed_at": (datetime.now(UTC) - ago).isoformat(),
            "dismissed_version": version,
            "conditions": conditions or ["binary"],
        }
    }


def test_stable_install_dismissal_silent_within_six_days(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = _dismissed_state(ago=timedelta(days=6))
    printed, input_calls = _setup_run_checks(
        monkeypatch, tmp_path, binary_signal=True, state=state
    )
    run_update_checks(home=tmp_path)
    assert not input_calls


def test_stable_install_dismissal_reprompts_after_eight_days(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = _dismissed_state(ago=timedelta(days=8))
    printed, input_calls = _setup_run_checks(
        monkeypatch, tmp_path, binary_signal=True, state=state
    )
    run_update_checks(home=tmp_path)
    assert len(input_calls) == 1


def test_integration_install_dismissal_silent_within_eleven_hours(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = _dismissed_state(ago=timedelta(hours=11))
    printed, input_calls = _setup_run_checks(
        monkeypatch,
        tmp_path,
        binary_signal=True,
        info=_make_integration_info(),
        state=state,
    )
    run_update_checks(home=tmp_path)
    assert not input_calls


def test_integration_install_dismissal_reprompts_after_thirteen_hours(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = _dismissed_state(ago=timedelta(hours=13))
    printed, input_calls = _setup_run_checks(
        monkeypatch,
        tmp_path,
        binary_signal=True,
        info=_make_integration_info(),
        state=state,
    )
    run_update_checks(home=tmp_path)
    assert len(input_calls) == 1


def test_dismissal_window_chosen_from_current_install_not_stored(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """User dismisses on stable (7d window), then migrates to integration (12h window).
    13 hours later the prompt should re-appear under the integration window."""
    state = _dismissed_state(ago=timedelta(hours=13))
    # Info is now integration — 12h window applies
    printed, input_calls = _setup_run_checks(
        monkeypatch,
        tmp_path,
        binary_signal=True,
        info=_make_integration_info(),
        state=state,
    )
    run_update_checks(home=tmp_path)
    assert len(input_calls) == 1


# ---------------------------------------------------------------------------
# UC-7 Time-windowed source-drift dismissal (no SHA keying)
# ---------------------------------------------------------------------------


def test_source_drift_dismissal_survives_new_upstream_commit_within_window(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Dismissed with ref=B; new check sees ref=C within window → still silent."""
    state = _dismissed_state(ago=timedelta(hours=1), conditions=["source_drift"])
    printed, input_calls = _setup_run_checks(
        monkeypatch, tmp_path, source_drift_signal=True, state=state
    )
    run_update_checks(home=tmp_path)
    assert not input_calls


def test_source_drift_dismissal_expires_on_window_boundary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = _dismissed_state(ago=timedelta(days=8), conditions=["source_drift"])
    printed, input_calls = _setup_run_checks(
        monkeypatch, tmp_path, source_drift_signal=True, state=state
    )
    run_update_checks(home=tmp_path)
    assert len(input_calls) == 1


def test_source_drift_dismissal_expires_on_version_delta(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Version advanced past dismissed_version → re-prompts regardless of time."""
    state = _dismissed_state(ago=timedelta(hours=1), version="0.7.77", conditions=["source_drift"])
    printed, input_calls = _setup_run_checks(
        monkeypatch, tmp_path, source_drift_signal=True, state=state, current_version="0.7.78"
    )
    run_update_checks(home=tmp_path)
    assert len(input_calls) == 1


# ---------------------------------------------------------------------------
# UC-8 Hook dismissal with version-delta
# ---------------------------------------------------------------------------


def test_hook_dismissal_expires_when_version_advances(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = _dismissed_state(ago=timedelta(hours=1), version="0.7.77", conditions=["hooks"])
    printed, input_calls = _setup_run_checks(
        monkeypatch, tmp_path, hooks_signal=True, state=state, current_version="0.7.78"
    )
    run_update_checks(home=tmp_path)
    assert len(input_calls) == 1


def test_hook_dismissal_holds_within_window_at_same_version(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = _dismissed_state(ago=timedelta(hours=1), version="0.7.77", conditions=["hooks"])
    printed, input_calls = _setup_run_checks(
        monkeypatch, tmp_path, hooks_signal=True, state=state, current_version="0.7.77"
    )
    run_update_checks(home=tmp_path)
    assert not input_calls


# ---------------------------------------------------------------------------
# Dismiss state I/O
# ---------------------------------------------------------------------------


def test_read_dismiss_state_empty(tmp_path: Path) -> None:
    assert _read_dismiss_state(tmp_path) == {}


def test_read_dismiss_state_malformed(tmp_path: Path) -> None:
    p = tmp_path / ".autoskillit" / "update_check.json"
    p.parent.mkdir(parents=True)
    p.write_text("not-json", encoding="utf-8")
    assert _read_dismiss_state(tmp_path) == {}


def test_write_dismiss_state_roundtrip(tmp_path: Path) -> None:
    state = {"update_prompt": {"dismissed_at": "2026-01-01T00:00:00+00:00"}}
    _write_dismiss_state(tmp_path, state)
    assert _read_dismiss_state(tmp_path) == state


def test_read_dismiss_state_non_dict_json_returns_empty(tmp_path: Path) -> None:
    p = tmp_path / ".autoskillit" / "update_check.json"
    p.parent.mkdir(parents=True)
    p.write_text("[1, 2, 3]", encoding="utf-8")
    assert _read_dismiss_state(tmp_path) == {}


# ---------------------------------------------------------------------------
# _is_dismissed
# ---------------------------------------------------------------------------


def test_is_dismissed_within_window() -> None:
    state = {
        "update_prompt": {
            "dismissed_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
            "dismissed_version": "0.9.0",
            "conditions": ["binary"],
        }
    }
    assert _is_dismissed(
        state, window=timedelta(hours=12), current_version="0.7.77", condition="binary"
    )


def test_is_dismissed_expired() -> None:
    state = {
        "update_prompt": {
            "dismissed_at": (datetime.now(UTC) - timedelta(days=8)).isoformat(),
            "dismissed_version": "0.9.0",
            "conditions": ["binary"],
        }
    }
    assert not _is_dismissed(
        state, window=timedelta(days=7), current_version="0.7.77", condition="binary"
    )


def test_is_dismissed_newer_version_resets() -> None:
    state = {
        "update_prompt": {
            "dismissed_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
            "dismissed_version": "0.7.77",
            "conditions": ["binary"],
        }
    }
    assert not _is_dismissed(
        state, window=timedelta(days=7), current_version="0.7.78", condition="binary"
    )


def test_is_dismissed_condition_not_in_list() -> None:
    state = {
        "update_prompt": {
            "dismissed_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
            "dismissed_version": "0.9.0",
            "conditions": ["binary"],
        }
    }
    # hooks was NOT dismissed — should still fire
    assert not _is_dismissed(
        state, window=timedelta(days=7), current_version="0.7.77", condition="hooks"
    )


def test_is_dismissed_empty_state() -> None:
    assert not _is_dismissed(
        {}, window=timedelta(days=7), current_version="0.7.77", condition="binary"
    )


# ---------------------------------------------------------------------------
# timed_prompt primitive tests
# ---------------------------------------------------------------------------


def test_timed_prompt_returns_default_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """timed_prompt returns the default value when select.select times out."""
    import select as _select_mod

    from autoskillit.cli._timed_input import timed_prompt

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.delenv("NO_COLOR", raising=False)

    # select.select returns empty list = timeout
    monkeypatch.setattr(
        _select_mod, "select", lambda rlist, wlist, xlist, timeout=None: ([], [], [])
    )

    printed: list[str] = []
    monkeypatch.setattr("builtins.print", lambda *args, **kw: printed.append(str(args)))

    result = timed_prompt("Test prompt?", default="n", timeout=30, label="test")
    assert result == "n"
    assert any("timed out" in p for p in printed)


def test_timed_prompt_applies_ansi_formatting(monkeypatch: pytest.MonkeyPatch) -> None:
    """timed_prompt output includes ANSI escape sequences when color is supported."""
    import select as _select_mod

    from autoskillit.cli._timed_input import timed_prompt

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("TERM", raising=False)

    monkeypatch.setattr(
        _select_mod, "select", lambda rlist, wlist, xlist, timeout=None: (rlist, [], [])
    )
    monkeypatch.setattr("builtins.input", lambda _="": "y")

    raw_output: list[str] = []
    monkeypatch.setattr(
        "builtins.print",
        lambda *args, **kw: raw_output.append(" ".join(str(a) for a in args)),
    )

    timed_prompt("Update now? [Y/n]", default="n", timeout=30, label="test")
    combined = " ".join(raw_output)
    assert "\x1b[" in combined  # ANSI escape present


def test_timed_prompt_respects_no_color(monkeypatch: pytest.MonkeyPatch) -> None:
    """timed_prompt output has no ANSI sequences when NO_COLOR is set."""
    import select as _select_mod

    from autoskillit.cli._timed_input import timed_prompt

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setenv("NO_COLOR", "1")

    monkeypatch.setattr(
        _select_mod, "select", lambda rlist, wlist, xlist, timeout=None: (rlist, [], [])
    )
    monkeypatch.setattr("builtins.input", lambda _="": "y")

    raw_output: list[str] = []
    monkeypatch.setattr(
        "builtins.print",
        lambda *args, **kw: raw_output.append(" ".join(str(a) for a in args)),
    )

    timed_prompt("Update now? [Y/n]", default="n", timeout=30, label="test")
    combined = " ".join(raw_output)
    assert "\x1b[" not in combined  # No ANSI escapes


# ---------------------------------------------------------------------------
# AUTOSKILLIT_FORCE_UPDATE_CHECK override
# ---------------------------------------------------------------------------


def test_force_update_check_env_overrides_local_editable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """AUTOSKILLIT_FORCE_UPDATE_CHECK=1 bypasses the LOCAL_EDITABLE early return."""
    info = InstallInfo(
        install_type=InstallType.LOCAL_EDITABLE,
        commit_id=None,
        requested_revision=None,
        url=None,
        editable_source=Path(tmp_path),
    )
    printed, input_calls = _setup_run_checks(
        monkeypatch, tmp_path, info=info, binary_signal=True, answer="n"
    )
    monkeypatch.setenv("AUTOSKILLIT_FORCE_UPDATE_CHECK", "1")
    run_update_checks(home=tmp_path)
    # The prompt should have been reached (not early-returned)
    assert len(input_calls) == 1


# ---------------------------------------------------------------------------
# UC-10 Passive notification for dismissed signals (REQ-UX-002–006, REQ-FLOW-001–004)
# ---------------------------------------------------------------------------


def test_dismissed_signal_prints_passive_notification(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = _dismissed_state(ago=timedelta(hours=1), conditions=["binary"])
    printed, input_calls = _setup_run_checks(
        monkeypatch, tmp_path, binary_signal=True, state=state
    )
    run_update_checks(home=tmp_path)
    assert not input_calls, "Dismissed signal must not trigger interactive prompt"
    combined = " ".join(printed)
    assert "autoskillit update" in combined, (
        "Dismissed signal must produce passive notification containing 'autoskillit update'"
    )


def test_passive_notification_contains_version_info(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = _dismissed_state(ago=timedelta(hours=1), conditions=["binary"])
    printed, _ = _setup_run_checks(monkeypatch, tmp_path, binary_signal=True, state=state)
    run_update_checks(home=tmp_path)
    combined = " ".join(printed)
    # The binary signal message is "New release: 0.9.0 (you have 0.7.77)"
    assert "0.9.0" in combined or "0.7.77" in combined, (
        "Passive notification must include version info"
    )


def test_passive_notification_contains_expiry_date(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    dismissed_ago = timedelta(hours=1)
    state = _dismissed_state(ago=dismissed_ago, conditions=["binary"])
    # Derive expiry from state to avoid date-boundary races between setup and assertion
    dismissed_at = datetime.fromisoformat(state["update_prompt"]["dismissed_at"])
    printed, _ = _setup_run_checks(monkeypatch, tmp_path, binary_signal=True, state=state)
    run_update_checks(home=tmp_path)
    combined = " ".join(printed)
    # Stable install: 7-day window.
    expected_expiry = (dismissed_at + timedelta(days=7)).strftime("%Y-%m-%d")
    assert expected_expiry in combined, (
        f"Passive notification must include expiry date {expected_expiry!r}; got: {combined!r}"
    )


def test_passive_notification_contains_update_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = _dismissed_state(ago=timedelta(hours=1), conditions=["binary"])
    printed, _ = _setup_run_checks(monkeypatch, tmp_path, binary_signal=True, state=state)
    run_update_checks(home=tmp_path)
    combined = " ".join(printed)
    assert "autoskillit update" in combined, (
        "Passive notification must contain 'autoskillit update'"
    )


def test_undismissed_signal_still_gets_interactive_prompt_when_dismissed_also_present(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # binary is dismissed; hooks is NOT dismissed (not in conditions list)
    state = _dismissed_state(ago=timedelta(hours=1), conditions=["binary"])
    printed, input_calls = _setup_run_checks(
        monkeypatch,
        tmp_path,
        binary_signal=True,
        hooks_signal=True,
        state=state,
    )
    run_update_checks(home=tmp_path)
    assert len(input_calls) == 1, "Undismissed hooks signal must trigger interactive prompt"


def test_all_dismissed_signals_produce_no_interactive_prompt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state = _dismissed_state(ago=timedelta(hours=1), conditions=["binary", "hooks"])
    printed, input_calls = _setup_run_checks(
        monkeypatch,
        tmp_path,
        binary_signal=True,
        hooks_signal=True,
        state=state,
    )
    run_update_checks(home=tmp_path)
    assert not input_calls, "All-dismissed signals must not trigger interactive prompt"
    combined = " ".join(printed)
    assert "autoskillit update" in combined, (
        "All-dismissed signals must produce passive notification containing 'autoskillit update'"
    )


def test_run_update_checks_shows_notification_when_kitchen_open(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    printed, input_calls = _setup_run_checks(monkeypatch, tmp_path, binary_signal=True)
    monkeypatch.setattr("autoskillit.core.any_kitchen_open", lambda **kw: True)

    binary_signal_calls: list[bool] = []
    monkeypatch.setattr(
        "autoskillit.cli._update_checks._binary_signal",
        lambda *a, **kw: binary_signal_calls.append(True) or None,
    )

    run_update_checks(home=tmp_path, command="update")

    combined = " ".join(printed)
    assert "kitchen" in combined.lower(), (
        "run_update_checks must print a notification mentioning 'kitchen' when kitchen is open"
    )
    assert not input_calls, "run_update_checks must not prompt interactively when kitchen is open"
    assert not binary_signal_calls, (
        "run_update_checks must not proceed to signal checks when kitchen is open"
    )


def test_run_update_checks_kitchen_open_bypassed_for_non_mutation_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """run_update_checks with command='order' proceeds past kitchen guard even when open."""
    printed, input_calls = _setup_run_checks(monkeypatch, tmp_path, binary_signal=True)

    binary_signal_calls: list[bool] = []
    monkeypatch.setattr(
        "autoskillit.cli._update_checks._binary_signal",
        lambda *a, **kw: binary_signal_calls.append(True) or None,
    )

    run_update_checks(home=tmp_path, command="order")

    combined = " ".join(printed)
    assert "kitchen" not in combined.lower(), (
        "run_update_checks must NOT print the kitchen-suppression message for 'order'"
    )
    assert binary_signal_calls, (
        "run_update_checks must call _binary_signal (signal gathering proceeds) for 'order'"
    )


def test_run_update_checks_kitchen_guard_active_for_update_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """run_update_checks with command='update' retains kitchen guard suppression."""
    printed, input_calls = _setup_run_checks(monkeypatch, tmp_path, binary_signal=True)
    monkeypatch.setattr("autoskillit.core.any_kitchen_open", lambda **kw: True)

    binary_signal_calls: list[bool] = []
    monkeypatch.setattr(
        "autoskillit.cli._update_checks._binary_signal",
        lambda *a, **kw: binary_signal_calls.append(True) or None,
    )

    run_update_checks(home=tmp_path, command="update")

    combined = " ".join(printed)
    assert "kitchen" in combined.lower(), (
        "run_update_checks must suppress via kitchen guard for 'update'"
    )
    assert not input_calls, (
        "run_update_checks must not prompt interactively for 'update' + kitchen"
    )
    assert not binary_signal_calls, (
        "run_update_checks must not reach signal gathering for 'update' + kitchen"
    )


def test_kitchen_guarded_commands_registry() -> None:
    """KITCHEN_GUARDED_COMMANDS must contain exactly the mutation commands."""
    from autoskillit.cli._update_checks import KITCHEN_GUARDED_COMMANDS

    assert KITCHEN_GUARDED_COMMANDS == frozenset({"update", "install", "init"}), (
        f"KITCHEN_GUARDED_COMMANDS must be exactly {{'update', 'install', 'init'}}, "
        f"got {KITCHEN_GUARDED_COMMANDS!r}"
    )
