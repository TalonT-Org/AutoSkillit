"""Tests for cli/_update_checks.py — UC-1 early-return guards, UC-2 signal gatherers,
and find_source_repo behavioral coverage."""

from __future__ import annotations

import io
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from autoskillit.cli._install_info import InstallInfo, InstallType
from autoskillit.cli._update_checks import run_update_checks

from ._update_checks_helpers import _make_integration_info, _make_stable_info

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]

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
