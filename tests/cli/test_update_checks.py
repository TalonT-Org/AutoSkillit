"""Tests for cli/_update_checks.py — unified update check orchestration.

Absorbs and extends the behavioral coverage from the deleted
test_stale_check.py and test_source_drift.py files.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from autoskillit.cli._install_info import InstallInfo, InstallType
from autoskillit.cli._update_checks import (
    _fetch_with_cache,
    _is_dismissed,
    _read_dismiss_state,
    _write_dismiss_state,
    run_update_checks,
)

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_stable_info(commit_id: str = "abc123", revision: str = "stable") -> InstallInfo:
    return InstallInfo(
        install_type=InstallType.GIT_VCS,
        commit_id=commit_id,
        requested_revision=revision,
        url="https://github.com/TalonT-Org/AutoSkillit.git",
        editable_source=None,
    )


def _make_integration_info(commit_id: str = "def456") -> InstallInfo:
    return InstallInfo(
        install_type=InstallType.GIT_VCS,
        commit_id=commit_id,
        requested_revision="integration",
        url="https://github.com/TalonT-Org/AutoSkillit.git",
        editable_source=None,
    )


def _make_mock_client(
    status_code: int = 200,
    json_body: dict[str, Any] | None = None,
    etag: str | None = None,
    raise_exc: Exception | None = None,
) -> MagicMock:
    """Build a MagicMock httpx.Client context manager for fetch cache tests."""
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = json_body or {}
    response.headers = {"ETag": etag} if etag else {}

    client_instance = MagicMock()
    if raise_exc is not None:
        client_instance.get.side_effect = raise_exc
    else:
        client_instance.get.return_value = response

    ctx_manager = MagicMock()
    ctx_manager.__enter__.return_value = client_instance
    ctx_manager.__exit__.return_value = False
    return ctx_manager


# ---------------------------------------------------------------------------
# UC-1 Guards
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "env_var,value",
    [
        ("CLAUDECODE", "1"),
        ("CI", "1"),
        ("AUTOSKILLIT_SKIP_STALE_CHECK", "1"),
        ("AUTOSKILLIT_SKIP_UPDATE_CHECK", "1"),
        ("AUTOSKILLIT_SKIP_SOURCE_DRIFT_CHECK", "1"),
    ],
)
def test_run_update_checks_skips_on_guard_env_var(
    monkeypatch: pytest.MonkeyPatch,
    env_var: str,
    value: str,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(env_var, value)
    # Ensure no other guard vars are set
    for other in [
        "CLAUDECODE",
        "CI",
        "AUTOSKILLIT_SKIP_STALE_CHECK",
        "AUTOSKILLIT_SKIP_UPDATE_CHECK",
        "AUTOSKILLIT_SKIP_SOURCE_DRIFT_CHECK",
    ]:
        if other != env_var:
            monkeypatch.delenv(other, raising=False)
    fetched: list[str] = []
    monkeypatch.setattr(
        "autoskillit.cli._update_checks._fetch_with_cache",
        lambda url, **kw: fetched.append(url) or None,
    )
    prompted: list[str] = []
    monkeypatch.setattr("builtins.input", lambda _: prompted.append("called") or "n")
    run_update_checks(home=tmp_path)
    assert not fetched, "No network fetch should occur under guard env"
    assert not prompted, "No input() call under guard env"


def test_run_update_checks_skips_non_tty_stdin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_SKIP_STALE_CHECK", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_SKIP_UPDATE_CHECK", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_SKIP_SOURCE_DRIFT_CHECK", raising=False)
    fake_stdin = io.StringIO()
    fake_stdout = io.StringIO()
    monkeypatch.setattr(sys, "stdin", fake_stdin)
    monkeypatch.setattr(sys, "stdout", fake_stdout)
    fetched: list[str] = []
    monkeypatch.setattr(
        "autoskillit.cli._update_checks._fetch_with_cache",
        lambda url, **kw: fetched.append(url) or None,
    )
    run_update_checks(home=tmp_path)
    assert not fetched


def test_run_update_checks_skips_non_tty_stdout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_SKIP_STALE_CHECK", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_SKIP_UPDATE_CHECK", raising=False)
    monkeypatch.delenv("AUTOSKILLIT_SKIP_SOURCE_DRIFT_CHECK", raising=False)

    fake_stdin = MagicMock()
    fake_stdin.isatty.return_value = True
    fake_stdout = io.StringIO()
    monkeypatch.setattr(sys, "stdin", fake_stdin)
    monkeypatch.setattr(sys, "stdout", fake_stdout)
    fetched: list[str] = []
    monkeypatch.setattr(
        "autoskillit.cli._update_checks._fetch_with_cache",
        lambda url, **kw: fetched.append(url) or None,
    )
    run_update_checks(home=tmp_path)
    assert not fetched


@pytest.mark.parametrize(
    "install_type",
    [InstallType.LOCAL_EDITABLE, InstallType.LOCAL_PATH, InstallType.UNKNOWN],
)
def test_run_update_checks_skips_local_and_unknown_install_types(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, install_type: InstallType
) -> None:
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

    info = InstallInfo(
        install_type=install_type,
        commit_id=None,
        requested_revision=None,
        url=None,
        editable_source=Path(tmp_path) if install_type == InstallType.LOCAL_EDITABLE else None,
    )
    monkeypatch.setattr("autoskillit.cli._update_checks.detect_install", lambda: info)
    fetched: list[str] = []
    monkeypatch.setattr(
        "autoskillit.cli._update_checks._fetch_with_cache",
        lambda url, **kw: fetched.append(url) or None,
    )
    prompted: list[str] = []
    monkeypatch.setattr("builtins.input", lambda _: prompted.append("called") or "n")
    run_update_checks(home=tmp_path)
    assert not fetched
    assert not prompted


# ---------------------------------------------------------------------------
# UC-2 Signal gathering
# ---------------------------------------------------------------------------


def test_binary_signal_fires_when_newer_version_available(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from autoskillit.cli._update_checks import _binary_signal

    info = _make_stable_info()
    monkeypatch.setattr(
        "autoskillit.cli._update_checks._fetch_latest_version",
        lambda target, home: "0.9.0",
    )
    sig = _binary_signal(info, tmp_path, "0.7.77")
    assert sig is not None
    assert sig.kind == "binary"
    assert "0.9.0" in sig.message
    assert "0.7.77" in sig.message


def test_binary_signal_silent_when_same_version(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from autoskillit.cli._update_checks import _binary_signal

    info = _make_stable_info()
    monkeypatch.setattr(
        "autoskillit.cli._update_checks._fetch_latest_version",
        lambda target, home: "0.7.77",
    )
    assert _binary_signal(info, tmp_path, "0.7.77") is None


def test_binary_signal_silent_when_fetch_returns_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from autoskillit.cli._update_checks import _binary_signal

    info = _make_stable_info()
    monkeypatch.setattr(
        "autoskillit.cli._update_checks._fetch_latest_version",
        lambda target, home: None,
    )
    assert _binary_signal(info, tmp_path, "0.7.77") is None


def test_binary_signal_uses_releases_url_for_stable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from autoskillit.cli._update_checks import _binary_signal

    info = _make_stable_info()
    targets: list[str] = []
    monkeypatch.setattr(
        "autoskillit.cli._update_checks._fetch_latest_version",
        lambda target, home: targets.append(target) or "0.9.0",
    )
    _binary_signal(info, tmp_path, "0.7.77")
    assert targets == ["releases/latest"]


def test_binary_signal_uses_integration_url_for_integration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from autoskillit.cli._update_checks import _binary_signal

    info = _make_integration_info()
    targets: list[str] = []
    monkeypatch.setattr(
        "autoskillit.cli._update_checks._fetch_latest_version",
        lambda target, home: targets.append(target) or "0.9.0",
    )
    _binary_signal(info, tmp_path, "0.7.77")
    assert targets == ["integration"]


def test_hooks_signal_fires_on_missing_hooks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from autoskillit.cli._update_checks import _hooks_signal
    from autoskillit.hook_registry import HookDriftResult

    monkeypatch.setattr(
        "autoskillit.cli._update_checks._count_hook_registry_drift",
        lambda path: HookDriftResult(missing=3, orphaned=0),
    )
    sig = _hooks_signal(tmp_path / "settings.json")
    assert sig is not None
    assert sig.kind == "hooks"
    assert "3" in sig.message


def test_hooks_signal_fires_on_orphaned_hooks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from autoskillit.cli._update_checks import _hooks_signal
    from autoskillit.hook_registry import HookDriftResult

    monkeypatch.setattr(
        "autoskillit.cli._update_checks._count_hook_registry_drift",
        lambda path: HookDriftResult(missing=0, orphaned=2),
    )
    sig = _hooks_signal(tmp_path / "settings.json")
    assert sig is not None
    assert sig.kind == "hooks"
    assert "2" in sig.message


def test_hooks_signal_silent_when_no_drift(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from autoskillit.cli._update_checks import _hooks_signal
    from autoskillit.hook_registry import HookDriftResult

    monkeypatch.setattr(
        "autoskillit.cli._update_checks._count_hook_registry_drift",
        lambda path: HookDriftResult(missing=0, orphaned=0),
    )
    assert _hooks_signal(tmp_path / "settings.json") is None


def test_source_drift_signal_fires_when_commit_lags_ref(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from autoskillit.cli._update_checks import _source_drift_signal

    info = _make_stable_info(commit_id="aaaaaa")
    monkeypatch.setattr(
        "autoskillit.cli._update_checks.resolve_reference_sha",
        lambda info, home, **kw: "bbbbbb",
    )
    sig = _source_drift_signal(info, tmp_path)
    assert sig is not None
    assert sig.kind == "source_drift"
    # "source drift" must NOT appear in user-visible text (per plan verification item 11)
    assert "source drift" not in sig.message.lower()


def test_source_drift_signal_silent_when_sha_matches(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from autoskillit.cli._update_checks import _source_drift_signal

    info = _make_stable_info(commit_id="aaaaaa")
    monkeypatch.setattr(
        "autoskillit.cli._update_checks.resolve_reference_sha",
        lambda info, home, **kw: "aaaaaa",
    )
    assert _source_drift_signal(info, tmp_path) is None


def test_source_drift_signal_silent_when_ref_sha_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from autoskillit.cli._update_checks import _source_drift_signal

    info = _make_stable_info(commit_id="aaaaaa")
    monkeypatch.setattr(
        "autoskillit.cli._update_checks.resolve_reference_sha",
        lambda info, home, **kw: None,
    )
    assert _source_drift_signal(info, tmp_path) is None


def test_dual_mcp_signal_fires_when_both_registered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autoskillit.cli._update_checks import _dual_mcp_signal

    monkeypatch.setattr(
        "autoskillit.cli._update_checks._is_dual_mcp_registered",
        lambda home: True,
    )
    sig = _dual_mcp_signal(tmp_path)
    assert sig is not None
    assert sig.kind == "dual_mcp"
    assert "autoskillit install" in sig.message


def test_dual_mcp_signal_silent_when_only_one_registered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autoskillit.cli._update_checks import _dual_mcp_signal

    monkeypatch.setattr(
        "autoskillit.cli._update_checks._is_dual_mcp_registered",
        lambda home: False,
    )
    sig = _dual_mcp_signal(tmp_path)
    assert sig is None


def test_dual_mcp_signal_silent_on_corrupted_files(
    tmp_path: Path,
) -> None:
    # _check_dual_mcp_files is fail-open: corrupted JSON → False → no signal
    from autoskillit.cli._update_checks import _dual_mcp_signal

    claude_json = tmp_path / ".claude.json"
    claude_json.write_text("{invalid json")
    sig = _dual_mcp_signal(tmp_path)
    assert sig is None


# ---------------------------------------------------------------------------
# find_source_repo behavioral tests
# ---------------------------------------------------------------------------


def test_find_source_repo_env_var_override_valid(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AUTOSKILLIT_SOURCE_REPO env var pointing to a valid repo root is returned."""
    from autoskillit.cli._update_checks import find_source_repo

    src_dir = tmp_path / "src" / "autoskillit"
    src_dir.mkdir(parents=True)
    monkeypatch.setenv("AUTOSKILLIT_SOURCE_REPO", str(tmp_path))
    result = find_source_repo()
    assert result == tmp_path


def test_find_source_repo_env_var_override_invalid_falls_through(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AUTOSKILLIT_SOURCE_REPO pointing to a path without src/autoskillit/ is ignored."""
    from autoskillit.cli._update_checks import find_source_repo

    monkeypatch.setenv("AUTOSKILLIT_SOURCE_REPO", str(tmp_path / "nonexistent"))
    monkeypatch.setattr("autoskillit.cli._update_checks.Path.cwd", lambda: tmp_path)
    result = find_source_repo()
    assert result is None


def test_find_source_repo_cwd_walk_finds_pyproject(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """CWD walk finds the project root when pyproject.toml has name=autoskillit."""
    from autoskillit.cli._update_checks import find_source_repo

    project_root = tmp_path / "project"
    src_dir = project_root / "src" / "autoskillit"
    src_dir.mkdir(parents=True)
    pyproject = project_root / "pyproject.toml"
    pyproject.write_text('[project]\nname = "autoskillit"\n', encoding="utf-8")

    nested_cwd = project_root / "src" / "autoskillit" / "cli"
    nested_cwd.mkdir(parents=True)
    monkeypatch.delenv("AUTOSKILLIT_SOURCE_REPO", raising=False)
    monkeypatch.setattr("autoskillit.cli._update_checks.Path.cwd", lambda: nested_cwd)
    result = find_source_repo()
    assert result == project_root


def test_find_source_repo_cwd_walk_no_match_returns_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """CWD walk returns None when no autoskillit project is found."""
    from autoskillit.cli._update_checks import find_source_repo

    monkeypatch.delenv("AUTOSKILLIT_SOURCE_REPO", raising=False)
    monkeypatch.setattr("autoskillit.cli._update_checks.Path.cwd", lambda: tmp_path)
    result = find_source_repo()
    assert result is None


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
# UC-9 Fetch-cache regression coverage
# ---------------------------------------------------------------------------


def test_fetch_latest_version_uses_cache_within_ttl(tmp_path: Path) -> None:
    # Seed a cache entry that is fresh (1 second old, TTL = 30 min)
    import time

    from autoskillit.cli._update_checks import _fetch_latest_version

    cache_data = {
        "https://api.github.com/repos/TalonT-Org/AutoSkillit/releases/latest": {
            "body": {"tag_name": "v0.9.0"},
            "etag": '"test-etag"',
            "cached_at": time.time() - 1,
        }
    }
    (tmp_path / ".autoskillit").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".autoskillit" / "github_fetch_cache.json").write_text(
        json.dumps(cache_data), encoding="utf-8"
    )
    call_count = [0]

    class CountingClient:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            call_count[0] += 1
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, **kw):
            raise AssertionError("Should not hit network when cache is fresh")

    with patch("httpx.Client", CountingClient):
        result = _fetch_latest_version("releases/latest", tmp_path)

    assert result == "0.9.0"
    assert call_count[0] == 0


def test_fetch_cache_expires_after_ttl(tmp_path: Path) -> None:
    import time

    from autoskillit.cli._update_checks import _fetch_latest_version

    cache_data = {
        "https://api.github.com/repos/TalonT-Org/AutoSkillit/releases/latest": {
            "body": {"tag_name": "v0.8.0"},
            "etag": '"stale-etag"',
            "cached_at": time.time() - 3601,  # 1 hour + 1 second old
        }
    }
    (tmp_path / ".autoskillit").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".autoskillit" / "github_fetch_cache.json").write_text(
        json.dumps(cache_data), encoding="utf-8"
    )

    mock_client = _make_mock_client(
        status_code=200,
        json_body={"tag_name": "v0.9.0"},
        etag='"new-etag"',
    )
    with patch("httpx.Client", return_value=mock_client):
        result = _fetch_latest_version("releases/latest", tmp_path)

    assert result == "0.9.0"


def test_fetch_cache_respects_env_var_ttl(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import time

    from autoskillit.cli._update_checks import _fetch_latest_version

    # Entry is 61 seconds old — older than the custom 60s TTL
    cache_data = {
        "https://api.github.com/repos/TalonT-Org/AutoSkillit/releases/latest": {
            "body": {"tag_name": "v0.8.0"},
            "etag": '"stale-etag"',
            "cached_at": time.time() - 61,
        }
    }
    (tmp_path / ".autoskillit").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".autoskillit" / "github_fetch_cache.json").write_text(
        json.dumps(cache_data), encoding="utf-8"
    )
    monkeypatch.setenv("AUTOSKILLIT_FETCH_CACHE_TTL_SECONDS", "60")

    mock_client = _make_mock_client(
        status_code=200,
        json_body={"tag_name": "v0.9.0"},
    )
    with patch("httpx.Client", return_value=mock_client):
        result = _fetch_latest_version("releases/latest", tmp_path)

    assert result == "0.9.0"


def test_fetch_sends_github_token_auth_header(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    monkeypatch.setenv("GITHUB_TOKEN", "my-secret-token")
    (tmp_path / ".autoskillit").mkdir(parents=True, exist_ok=True)

    received_headers: dict = {}

    class CapturingClient:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, headers=None, **kw):
            received_headers.update(headers or {})
            r = MagicMock()
            r.status_code = 200
            r.json.return_value = {"tag_name": "v0.9.0"}
            r.headers = {}
            return r

    with patch("httpx.Client", CapturingClient):
        _fetch_with_cache(
            "https://api.github.com/repos/TalonT-Org/AutoSkillit/releases/latest",
            home=tmp_path,
        )

    assert "Authorization" in received_headers
    assert received_headers["Authorization"] == "Bearer my-secret-token"


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


def test_fetch_sends_if_none_match_when_cached_etag(tmp_path: Path) -> None:
    import time

    cache_data = {
        "https://api.github.com/repos/TalonT-Org/AutoSkillit/releases/latest": {
            "body": {"tag_name": "v0.8.0"},
            "etag": '"cached-etag"',
            "cached_at": time.time() - 3601,  # stale, so will hit network
        }
    }
    (tmp_path / ".autoskillit").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".autoskillit" / "github_fetch_cache.json").write_text(
        json.dumps(cache_data), encoding="utf-8"
    )

    received_headers: dict = {}

    class CapturingClient:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, headers=None, **kw):
            received_headers.update(headers or {})
            r = MagicMock()
            r.status_code = 200
            r.json.return_value = {"tag_name": "v0.9.0"}
            r.headers = {}
            return r

    with patch("httpx.Client", CapturingClient):
        _fetch_with_cache(
            "https://api.github.com/repos/TalonT-Org/AutoSkillit/releases/latest",
            home=tmp_path,
        )

    assert received_headers.get("If-None-Match") == '"cached-etag"'


def test_fetch_304_response_returns_cached_payload(tmp_path: Path) -> None:
    import time

    url = "https://api.github.com/repos/TalonT-Org/AutoSkillit/releases/latest"
    cache_data = {
        url: {
            "body": {"tag_name": "v0.8.5"},
            "etag": '"my-etag"',
            "cached_at": time.time() - 3601,  # stale
        }
    }
    (tmp_path / ".autoskillit").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".autoskillit" / "github_fetch_cache.json").write_text(
        json.dumps(cache_data), encoding="utf-8"
    )

    mock_client = _make_mock_client(status_code=304)
    with patch("httpx.Client", return_value=mock_client):
        result = _fetch_with_cache(url, home=tmp_path)

    assert result == {"tag_name": "v0.8.5"}


def test_fetch_uses_correct_timeout(tmp_path: Path) -> None:
    from autoskillit.cli._update_checks import _HTTP_TIMEOUT

    assert _HTTP_TIMEOUT.connect == 2.0
    assert _HTTP_TIMEOUT.read == 1.0
    assert _HTTP_TIMEOUT.write == 5.0
    assert _HTTP_TIMEOUT.pool == 1.0


def test_fetch_sends_modern_github_api_version_header(tmp_path: Path) -> None:
    (tmp_path / ".autoskillit").mkdir(parents=True, exist_ok=True)

    received_headers: dict = {}

    class CapturingClient:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, headers=None, **kw):
            received_headers.update(headers or {})
            r = MagicMock()
            r.status_code = 200
            r.json.return_value = {"tag_name": "v0.9.0"}
            r.headers = {}
            return r

    with patch("httpx.Client", CapturingClient):
        _fetch_with_cache(
            "https://api.github.com/repos/TalonT-Org/AutoSkillit/releases/latest",
            home=tmp_path,
        )

    assert received_headers.get("X-GitHub-Api-Version") == "2022-11-28"
    assert received_headers.get("Accept") == "application/vnd.github+json"


def test_fetch_sends_user_agent_with_package_version(tmp_path: Path) -> None:
    (tmp_path / ".autoskillit").mkdir(parents=True, exist_ok=True)

    received_headers: dict = {}

    class CapturingClient:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, headers=None, **kw):
            received_headers.update(headers or {})
            r = MagicMock()
            r.status_code = 200
            r.json.return_value = {}
            r.headers = {}
            return r

    with patch("httpx.Client", CapturingClient):
        _fetch_with_cache(
            "https://api.github.com/repos/TalonT-Org/AutoSkillit/releases/latest",
            home=tmp_path,
        )

    assert received_headers.get("User-Agent", "").startswith("autoskillit/")


def test_fetch_scrubs_authorization_header_from_logged_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    monkeypatch.setenv("GITHUB_TOKEN", "super-secret-token-xyz")
    (tmp_path / ".autoskillit").mkdir(parents=True, exist_ok=True)

    import httpx as _httpx

    class FailingClient:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, headers=None, **kw):
            raise _httpx.ConnectError("Connection refused [super-secret-token-xyz]")

    with caplog.at_level(logging.DEBUG, logger="autoskillit"):
        with patch("httpx.Client", FailingClient):
            result = _fetch_with_cache(
                "https://api.github.com/repos/TalonT-Org/AutoSkillit/releases/latest",
                home=tmp_path,
            )

    assert result is None
    # The token must not appear in any log record
    for record in caplog.records:
        assert "super-secret-token-xyz" not in record.getMessage()


def test_fetch_fails_fast_offline(tmp_path: Path) -> None:
    import httpx as _httpx

    from autoskillit.cli._update_checks import _fetch_latest_version

    (tmp_path / ".autoskillit").mkdir(parents=True, exist_ok=True)

    class OfflineClient:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, **kw):
            raise _httpx.ConnectError("Network unreachable")

    with patch("httpx.Client", OfflineClient):
        result = _fetch_latest_version("releases/latest", tmp_path)

    assert result is None


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


# ---------------------------------------------------------------------------
# T1 — _verify_update_result uses install-type-aware upgrade command
# ---------------------------------------------------------------------------


def test_verify_update_result_prints_git_vcs_stable_command(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    import importlib.metadata

    from autoskillit.cli._update_checks import _verify_update_result

    info = _make_stable_info()
    with patch.object(importlib.metadata, "version", return_value="0.9.0"):
        result = _verify_update_result(info, "0.9.0", "0.9.1", tmp_path, {})
    assert result is False
    out = capsys.readouterr().out
    assert "uv tool upgrade autoskillit" in out
    assert "autoskillit update" in out


def test_verify_update_result_prints_git_vcs_integration_command(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    import importlib.metadata

    from autoskillit.cli._update_checks import _verify_update_result

    info = _make_integration_info()
    with patch.object(importlib.metadata, "version", return_value="0.9.0"):
        result = _verify_update_result(info, "0.9.0", "0.9.1", tmp_path, {})
    assert result is False
    out = capsys.readouterr().out
    assert "git+" in out
    assert "uv tool upgrade autoskillit" not in out


# ---------------------------------------------------------------------------
# T2 — _run_update_sequence warns when autoskillit install exits non-zero
# ---------------------------------------------------------------------------


def test_run_update_sequence_warns_on_install_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    from autoskillit.cli._update_checks import _run_update_sequence

    class FakeTG:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    info = _make_stable_info()
    upgrade_ok = subprocess.CompletedProcess([], returncode=0)
    install_fail = subprocess.CompletedProcess([], returncode=1)
    calls = iter([upgrade_ok, install_fail])
    monkeypatch.setattr(
        "autoskillit.cli._update_checks.subprocess.run", lambda *a, **kw: next(calls)
    )
    monkeypatch.setattr("autoskillit.cli._update_checks.terminal_guard", FakeTG)
    monkeypatch.setattr(
        "autoskillit.cli._update_checks._fetch_latest_version", lambda *a, **kw: "0.9.1"
    )
    monkeypatch.setattr(
        "autoskillit.cli._update_checks._verify_update_result", lambda *a, **kw: True
    )
    _run_update_sequence(info, "0.9.0", tmp_path, {}, {})
    out = capsys.readouterr().out
    assert "install" in out.lower()
    assert "stale" in out.lower() or "autoskillit install" in out


# ---------------------------------------------------------------------------
# T6 — binary_snoozed is never written by _verify_update_result
# ---------------------------------------------------------------------------


def test_verify_update_result_does_not_write_binary_snoozed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import importlib.metadata

    from autoskillit.cli._update_checks import _verify_update_result

    info = _make_stable_info(commit_id="abc")
    state: dict = {}
    with patch.object(importlib.metadata, "version", return_value="0.9.0"):
        _verify_update_result(info, "0.9.0", "0.9.1", tmp_path, state)
    assert "binary_snoozed" not in state
