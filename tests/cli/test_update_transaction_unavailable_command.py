"""Typed transaction diagnostics for unavailable and missing upgrade commands."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

import autoskillit.cli.update._transaction as _patch_update__transaction
from autoskillit.cli.install._install_info import InstallInfo, InstallType
from autoskillit.cli.update._transaction import (
    UpdateProcessStatus,
    UpdateTransactionOutcome,
    run_update_transaction,
)
from tests.cli.test_update_transaction import _register_plugin, _stub_generation_verification

pytestmark = [pytest.mark.layer("cli"), pytest.mark.medium]

_PYTHON_PIN = f"{sys.version_info.major}.{sys.version_info.minor}"


def _unknown_info() -> InstallInfo:
    return InstallInfo(
        install_type=InstallType.UNKNOWN,
        commit_id=None,
        requested_revision=None,
        url=None,
        editable_source=None,
    )


def _local_path_info(local_source: Path | None) -> InstallInfo:
    return InstallInfo(
        install_type=InstallType.LOCAL_PATH,
        commit_id=None,
        requested_revision=None,
        url=local_source.as_uri() if local_source is not None else None,
        editable_source=None,
        local_source=local_source,
    )


def _unreachable_runner(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
    raise AssertionError(f"subprocess must not launch: {cmd}")


def test_unknown_install_names_install_type(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(_patch_update__transaction, "detect_install", _unknown_info)

    result = run_update_transaction(
        home=tmp_path,
        base_env={"PATH": "/bin"},
        version_reader=lambda _name: "1.0.0",
        process_runner=_unreachable_runner,
    )

    assert result.outcome is UpdateTransactionOutcome.FAILED_UPGRADE
    assert result.findings
    finding = result.findings[0]
    assert "unknown" in finding
    assert "install.sh" in finding
    assert "task install-dev" in finding
    assert "Unknown install type." not in finding


def test_local_path_missing_source_names_path_and_remedy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    missing_source = tmp_path / "gone"
    monkeypatch.setattr(
        _patch_update__transaction,
        "detect_install",
        lambda: _local_path_info(missing_source),
    )

    result = run_update_transaction(
        home=tmp_path,
        base_env={"PATH": "/bin"},
        version_reader=lambda _name: "1.0.0",
        process_runner=_unreachable_runner,
    )

    assert result.outcome is UpdateTransactionOutcome.FAILED_UPGRADE
    assert result.findings
    finding = result.findings[0]
    assert "local-path" in finding
    assert str(missing_source) in finding
    assert "uv tool install --force --reinstall" in finding


def test_local_path_missing_source_still_defers_inside_claudecode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    missing_source = tmp_path / "gone"
    monkeypatch.setattr(
        _patch_update__transaction,
        "detect_install",
        lambda: _local_path_info(missing_source),
    )
    _register_plugin(tmp_path)

    result = run_update_transaction(
        home=tmp_path,
        base_env={"PATH": "/bin", "CLAUDECODE": "1"},
        version_reader=lambda _name: "1.0.0",
        process_runner=_unreachable_runner,
    )

    assert result.outcome is UpdateTransactionOutcome.DEFERRED


def test_local_path_runs_staged_reinstall(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    local_source = tmp_path / "src"
    local_source.mkdir()
    monkeypatch.setattr(
        _patch_update__transaction,
        "detect_install",
        lambda: _local_path_info(local_source),
    )
    monkeypatch.setattr(_patch_update__transaction, "is_git_worktree", lambda _path: False)
    monkeypatch.setattr(_patch_update__transaction, "is_git_main_checkout", lambda _path: False)
    _stub_generation_verification(monkeypatch)

    calls: list[tuple[list[str], dict[str, Any]]] = []

    def runner(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        calls.append((list(cmd), kwargs))
        return subprocess.CompletedProcess(cmd, 0)

    run_update_transaction(
        home=tmp_path,
        base_env={"PATH": "/bin"},
        version_reader=lambda _name: "1.0.0",
        process_runner=runner,
    )

    assert calls
    first_argv, first_kwargs = calls[0]
    assert first_argv == [
        "uv",
        "tool",
        "install",
        "--force",
        "--reinstall",
        str(local_source),
        "--python",
        _PYTHON_PIN,
    ]
    assert "UV_TOOL_DIR" in first_kwargs["env"]


def test_process_status_for_failed_upgrade_is_twenty() -> None:
    assert int(UpdateProcessStatus.FAILED_UPGRADE) == 20
