"""Tests for the shared success-gated update transaction."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from autoskillit.cli._install_contract import InstallProcessStatus
from autoskillit.cli._install_info import InstallInfo, InstallType
from autoskillit.cli.update._transaction import (
    UpdateTransactionOutcome,
    run_update_transaction,
)
from autoskillit.core import Severity

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]

_PLUGIN_REF = "autoskillit@autoskillit-local"


def _info() -> InstallInfo:
    return InstallInfo(
        install_type=InstallType.GIT_VCS,
        commit_id="abc123",
        requested_revision="stable",
        url="https://github.com/TalonT-Org/AutoSkillit.git",
        editable_source=None,
    )


def _register_plugin(home: Path) -> Path:
    install_path = (
        home / ".claude" / "plugins" / "cache" / "autoskillit-local" / "autoskillit" / "1.0.0"
    )
    registry = home / ".claude" / "plugins" / "installed_plugins.json"
    registry.parent.mkdir(parents=True)
    registry.write_text(
        json.dumps(
            {
                "version": 2,
                "plugins": {
                    _PLUGIN_REF: [{"installPath": str(install_path)}],
                },
            }
        ),
        encoding="utf-8",
    )
    return registry


def _prepare(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("autoskillit.cli.update._transaction.detect_install", _info)
    monkeypatch.setattr(
        "autoskillit.cli.update._transaction.upgrade_command",
        lambda _info: ["uv", "tool", "upgrade", "autoskillit"],
    )
    monkeypatch.setattr(
        "autoskillit.cli.update._transaction.is_git_worktree",
        lambda _path: False,
    )
    monkeypatch.setattr(
        "autoskillit.cli.update._transaction.is_git_main_checkout",
        lambda _path: False,
    )


def test_claudecode_with_existing_registration_defers_before_mutation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _prepare(monkeypatch)
    _register_plugin(tmp_path)
    calls: list[list[str]] = []

    result = run_update_transaction(
        home=tmp_path,
        base_env={"CLAUDECODE": "1", "PATH": "/bin"},
        version_reader=lambda _name: "1.0.0",
        process_runner=lambda cmd, **kwargs: (
            calls.append(list(cmd)) or subprocess.CompletedProcess(cmd, 0)
        ),
    )

    assert result.outcome is UpdateTransactionOutcome.DEFERRED
    assert not calls
    assert not list((tmp_path / ".autoskillit").glob("update-maintenance-*"))


def test_upgrade_failure_gates_install_and_cleans_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _prepare(monkeypatch)
    calls: list[list[str]] = []

    def runner(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 7)

    result = run_update_transaction(
        home=tmp_path,
        base_env={"PATH": "/bin"},
        version_reader=lambda _name: "1.0.0",
        process_runner=runner,
    )

    assert result.outcome is UpdateTransactionOutcome.FAILED_UPGRADE
    assert calls == [["uv", "tool", "upgrade", "autoskillit"]]
    assert not list((tmp_path / ".autoskillit").glob("update-maintenance-*"))


def test_metadata_must_advance_before_install(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _prepare(monkeypatch)
    calls: list[list[str]] = []

    result = run_update_transaction(
        home=tmp_path,
        base_env={"PATH": "/bin"},
        version_reader=lambda _name: "1.0.0",
        process_runner=lambda cmd, **kwargs: (
            calls.append(list(cmd)) or subprocess.CompletedProcess(cmd, 0)
        ),
    )

    assert result.outcome is UpdateTransactionOutcome.FAILED_UPGRADE
    assert len(calls) == 1


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (InstallProcessStatus.DECLINED, UpdateTransactionOutcome.DECLINED),
        (InstallProcessStatus.DEFERRED, UpdateTransactionOutcome.DEFERRED),
        (InstallProcessStatus.FAILED_PREFLIGHT, UpdateTransactionOutcome.FAILED_INSTALL),
        (InstallProcessStatus.FAILED_CHILD, UpdateTransactionOutcome.FAILED_INSTALL),
        (
            InstallProcessStatus.FAILED_POSTCONDITION,
            UpdateTransactionOutcome.FAILED_POSTCONDITION,
        ),
        (
            InstallProcessStatus.RECOVERY_REQUIRED,
            UpdateTransactionOutcome.RECOVERY_REQUIRED,
        ),
        (InstallProcessStatus.INDETERMINATE, UpdateTransactionOutcome.INDETERMINATE),
        (99, UpdateTransactionOutcome.INDETERMINATE),
        (-15, UpdateTransactionOutcome.INDETERMINATE),
    ],
)
def test_install_process_statuses_map_to_distinct_update_outcomes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    status: int,
    expected: UpdateTransactionOutcome,
) -> None:
    _prepare(monkeypatch)
    versions = iter(["1.0.0", "1.1.0"])
    statuses = iter([0, int(status)])

    result = run_update_transaction(
        home=tmp_path,
        base_env={"PATH": "/bin"},
        version_reader=lambda _name: next(versions),
        process_runner=lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, next(statuses)),
    )

    assert result.outcome is expected
    assert result.expected_version == "1.1.0"
    assert result.install_result is not None


def test_success_uses_sealed_env_explicit_cwd_and_maintenance_flags(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _prepare(monkeypatch)
    versions = iter(["1.0.0", "1.1.0"])
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def runner(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        calls.append((list(cmd), kwargs))
        return subprocess.CompletedProcess(cmd, 0)

    result = run_update_transaction(
        home=tmp_path,
        base_env={
            "PATH": "/bin",
            "CLAUDECODE": "1",
            "AUTOSKILLIT_AGENT_BACKEND__BACKEND": "codex",
        },
        version_reader=lambda _name: next(versions),
        process_runner=runner,
    )

    assert result.outcome is UpdateTransactionOutcome.COMPLETED
    assert len(calls) == 2
    assert calls[0][1]["cwd"] == calls[1][1]["cwd"]
    assert str(calls[0][1]["cwd"]).startswith(str(tmp_path / ".autoskillit"))
    assert not list((tmp_path / ".autoskillit").glob("update-maintenance-*"))
    for _, kwargs in calls:
        env = kwargs["env"]
        assert env["AUTOSKILLIT_SKIP_STALE_CHECK"] == "1"
        assert env["AUTOSKILLIT_SKIP_UPDATE_CHECK"] == "1"
        assert "CLAUDECODE" not in env
        assert "AUTOSKILLIT_AGENT_BACKEND__BACKEND" not in env
    install_command = calls[1][0]
    assert install_command[:2] == ["autoskillit", "install"]
    assert "--maintenance-update" in install_command
    assert install_command[install_command.index("--expected-version") + 1] == "1.1.0"
    assert "--require-registered-plugin" not in install_command


def test_registered_obligation_is_immutable_and_exact_verification_gates_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _prepare(monkeypatch)
    registry = _register_plugin(tmp_path)
    versions = iter(["1.0.0", "1.1.0"])
    calls: list[list[str]] = []
    lease = SimpleNamespace(closed=False)
    lease.close = lambda: setattr(lease, "closed", True)

    def runner(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        calls.append(list(cmd))
        if len(calls) == 1:
            registry.unlink()
        return subprocess.CompletedProcess(cmd, 0)

    captured_specs: list[Any] = []

    def verify(spec: Any) -> Any:
        captured_specs.append(spec)
        return SimpleNamespace(
            identity=SimpleNamespace(semantic_key=f"{_PLUGIN_REF}:1.1.0"),
            findings=(),
            lease=lease,
        )

    monkeypatch.setattr(
        "autoskillit.cli.update._transaction.verify_installed_plugin_artifact",
        verify,
    )
    result = run_update_transaction(
        home=tmp_path,
        base_env={"PATH": "/bin"},
        version_reader=lambda _name: next(versions),
        process_runner=runner,
    )

    assert result.outcome is UpdateTransactionOutcome.COMPLETED
    assert "--require-registered-plugin" in calls[1]
    assert captured_specs[0].require_registered_plugin is True
    assert captured_specs[0].require_shared_lease is True
    assert result.verified_identity == f"{_PLUGIN_REF}:1.1.0"
    assert lease.closed is True


def test_verification_error_is_failed_postcondition(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _prepare(monkeypatch)
    _register_plugin(tmp_path)
    versions = iter(["1.0.0", "1.1.0"])
    finding = SimpleNamespace(
        severity=Severity.ERROR,
        check="installed_plugin_registry_missing",
        message="missing exact registration",
    )
    monkeypatch.setattr(
        "autoskillit.cli.update._transaction.verify_installed_plugin_artifact",
        lambda _spec: SimpleNamespace(identity=None, findings=(finding,), lease=None),
    )

    result = run_update_transaction(
        home=tmp_path,
        base_env={"PATH": "/bin"},
        version_reader=lambda _name: next(versions),
        process_runner=lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 0),
    )

    assert result.outcome is UpdateTransactionOutcome.FAILED_POSTCONDITION
    assert result.install_result is not None
    assert any("missing exact registration" in item for item in result.findings)


def test_git_contained_maintenance_cwd_fails_before_upgrade_and_is_removed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _prepare(monkeypatch)
    monkeypatch.setattr(
        "autoskillit.cli.update._transaction.is_git_main_checkout",
        lambda _path: True,
    )
    calls: list[list[str]] = []

    result = run_update_transaction(
        home=tmp_path,
        base_env={"PATH": "/bin"},
        version_reader=lambda _name: "1.0.0",
        process_runner=lambda cmd, **kwargs: (
            calls.append(list(cmd)) or subprocess.CompletedProcess(cmd, 0)
        ),
    )

    assert result.outcome is UpdateTransactionOutcome.FAILED_UPGRADE
    assert not calls
    assert not list((tmp_path / ".autoskillit").glob("update-maintenance-*"))
