"""End-to-end agreement between update signals and transaction advancement."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from autoskillit.cli.install._install_info import (
    InstallInfo,
    InstallType,
    release_identity,
)
from autoskillit.cli.update._transaction import (
    UpdateTransactionOutcome,
    UpdateTransactionResult,
    run_update_transaction,
)
from autoskillit.cli.update._update_checks import _binary_signal, _source_drift_signal
from autoskillit.cli.update._update_checks_source import resolve_target_identity
from autoskillit.core import ReleaseIdentity, update_available

pytestmark = [pytest.mark.layer("cli"), pytest.mark.medium]

_VERSION = "0.10.1013"
_NEW_VERSION = "0.10.1014"
_OLD_SHA = "a" * 40
_TARGET_SHA = "b" * 40
_REPOSITORY = "https://github.com/TalonT-Org/AutoSkillit.git"


def _info(home: Path, revision: str) -> InstallInfo:
    entrypoint = home / "bin" / "autoskillit"
    _write_entrypoint(entrypoint, _VERSION)
    return InstallInfo(
        install_type=InstallType.GIT_VCS,
        commit_id=_OLD_SHA,
        requested_revision=revision,
        url=_REPOSITORY,
        editable_source=None,
        entrypoint=entrypoint,
    )


def _write_entrypoint(entrypoint: Path, version: str) -> None:
    entrypoint.parent.mkdir(parents=True, exist_ok=True)
    entrypoint.write_text(f"#!/bin/sh\nprintf '%s\\n' '{version}'\n", encoding="utf-8")
    entrypoint.chmod(0o755)


def _materialize_uv_tool_root(
    root: Path,
    *,
    revision: str,
    commit: str,
    version: str,
) -> None:
    python_pin = f"python{sys.version_info.major}.{sys.version_info.minor}"
    dist_info = (
        root
        / "autoskillit"
        / "lib"
        / python_pin
        / "site-packages"
        / f"autoskillit-{version}.dist-info"
    )
    dist_info.mkdir(parents=True, exist_ok=True)
    (dist_info / "direct_url.json").write_text(
        json.dumps(
            {
                "url": _REPOSITORY,
                "vcs_info": {
                    "vcs": "git",
                    "commit_id": commit,
                    "requested_revision": revision,
                },
            }
        ),
        encoding="utf-8",
    )
    _write_entrypoint(root / "autoskillit" / "bin" / "autoskillit", version)


def _transaction_runner(
    info: InstallInfo,
    *,
    installed_version: str,
    installed_commit: str,
) -> Callable[..., subprocess.CompletedProcess[Any]]:
    def runner(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        if cmd[0] == "uv":
            environment = kwargs["env"]
            destination = environment.get("UV_TOOL_DIR")
            if destination is not None:
                Path(environment["UV_TOOL_BIN_DIR"]).mkdir(parents=True, exist_ok=True)
                _materialize_uv_tool_root(
                    Path(destination),
                    revision=info.requested_revision or "develop",
                    commit=installed_commit,
                    version=installed_version,
                )
            else:
                assert info.entrypoint is not None
                _write_entrypoint(info.entrypoint, installed_version)
            return subprocess.CompletedProcess(cmd, 0)
        if "--version" in cmd:
            return subprocess.run(cmd, **kwargs)
        return subprocess.CompletedProcess(cmd, 0)

    return runner


def _resolve_target(
    monkeypatch: pytest.MonkeyPatch,
    info: InstallInfo,
    home: Path,
    *,
    version: str,
    commit: str = _TARGET_SHA,
) -> ReleaseIdentity:
    import autoskillit.cli.update._update_checks_source as source

    monkeypatch.setattr(source, "_fetch_latest_version", lambda _ref, _home: version)
    monkeypatch.setattr(
        source,
        "resolve_reference_sha",
        lambda _info, _home, *, network=True: commit,
    )
    target = resolve_target_identity(info, home)
    assert target is not None
    return target


def _run_transaction(
    monkeypatch: pytest.MonkeyPatch,
    home: Path,
    info: InstallInfo,
    *,
    installed_version: str,
    installed_commit: str,
    target: ReleaseIdentity,
) -> UpdateTransactionResult:
    import autoskillit.cli.update._transaction as transaction

    monkeypatch.setattr(transaction, "detect_install", lambda: info)
    monkeypatch.setattr(transaction, "is_git_worktree", lambda _path: False)
    monkeypatch.setattr(transaction, "is_git_main_checkout", lambda _path: False)
    return run_update_transaction(
        home=home,
        base_env={"PATH": str(info.entrypoint.parent) if info.entrypoint else "/bin"},
        version_reader=lambda _name: _VERSION,
        target_identity=target,
        process_runner=_transaction_runner(
            info,
            installed_version=installed_version,
            installed_commit=installed_commit,
        ),
    )


def test_dev_track_unbumped_commit_completes_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    info = _info(tmp_path, "develop")
    target = _resolve_target(monkeypatch, info, tmp_path, version=_VERSION)
    installed = release_identity(info, version=_VERSION)
    available = update_available(installed, target)

    signal = _source_drift_signal(installed, target, available)

    assert signal is not None
    assert signal.target is target
    result = _run_transaction(
        monkeypatch,
        tmp_path,
        info,
        installed_version=_VERSION,
        installed_commit=_TARGET_SHA,
        target=signal.target,
    )
    assert result.outcome is UpdateTransactionOutcome.COMPLETED, result.findings


def test_stable_track_equal_version_still_fails_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    info = _info(tmp_path, "stable")
    target = _resolve_target(monkeypatch, info, tmp_path, version=_VERSION)
    installed = release_identity(info, version=_VERSION)

    assert not update_available(installed, target)
    assert _binary_signal(installed, target, False) is None
    assert _source_drift_signal(installed, target, False) is None

    result = _run_transaction(
        monkeypatch,
        tmp_path,
        info,
        installed_version=_VERSION,
        installed_commit=_TARGET_SHA,
        target=target,
    )
    assert result.outcome is UpdateTransactionOutcome.FAILED_UPGRADE


def test_main_tracking_install_does_not_prompt_without_a_release(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    info = _info(tmp_path, "main")
    installed = release_identity(info, version=_VERSION)
    unbumped_target = _resolve_target(monkeypatch, info, tmp_path, version=_VERSION)
    unbumped_available = update_available(installed, unbumped_target)

    assert not unbumped_available
    assert _binary_signal(installed, unbumped_target, unbumped_available) is None
    assert _source_drift_signal(installed, unbumped_target, unbumped_available) is None

    bumped_target = _resolve_target(monkeypatch, info, tmp_path, version=_NEW_VERSION)
    bumped_available = update_available(installed, bumped_target)
    signal = _binary_signal(installed, bumped_target, bumped_available)

    assert signal is not None
    assert signal.target is bumped_target
    result = _run_transaction(
        monkeypatch,
        tmp_path,
        info,
        installed_version=_NEW_VERSION,
        installed_commit=_TARGET_SHA,
        target=signal.target,
    )
    assert result.outcome is UpdateTransactionOutcome.COMPLETED, result.findings
