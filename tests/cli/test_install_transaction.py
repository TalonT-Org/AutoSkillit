"""Typed install transaction, compensation, and sealed-context tests."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from autoskillit.cli._install_contract import (
    InstallFailureKind,
    InstallMode,
    InstallOutcome,
    InstallRequest,
)

pytestmark = [pytest.mark.layer("cli"), pytest.mark.medium]

_PLUGIN_REF = "autoskillit@autoskillit-local"
_VERSION = "1.2.3"


def _maintenance_request(*, required: bool = True) -> InstallRequest:
    return InstallRequest(
        scope="user",
        mode=InstallMode.MAINTENANCE_UPDATE,
        require_registered_plugin=required,
        expected_version=_VERSION,
    )


def _configure_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[object, Path]:
    import autoskillit.workspace as workspace
    from autoskillit.cli import _marketplace, _plugin_artifact

    neutral_cwd = tmp_path / "neutral"
    neutral_cwd.mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(_marketplace, "is_git_worktree", lambda _path: False)
    monkeypatch.setattr(
        _marketplace.importlib.metadata,
        "version",
        lambda _name: _VERSION,
    )
    monkeypatch.setattr(
        _marketplace.shutil,
        "which",
        lambda _cmd, *, path=None: "/usr/bin/claude",
    )
    monkeypatch.setattr(workspace, "reconcile_install_artifacts", lambda: ())
    monkeypatch.setattr(
        _marketplace,
        "_ensure_marketplace",
        lambda **_kwargs: tmp_path / ".autoskillit" / "marketplace",
    )
    monkeypatch.setattr(_marketplace, "_clear_plugin_cache", lambda **_kwargs: ())
    monkeypatch.setattr(
        _plugin_artifact,
        "publish_installed_plugin_artifact",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(_marketplace, "evict_direct_mcp_entry", lambda _path: False)
    monkeypatch.setattr(
        _marketplace._hooks_mod,
        "_evict_stale_autoskillit_hooks",
        lambda _path: None,
    )
    identity = SimpleNamespace(incarnation_id="0" * 32)
    monkeypatch.setattr(
        workspace,
        "verify_installed_plugin_artifact",
        lambda _spec: SimpleNamespace(identity=identity, findings=()),
    )
    return _marketplace, neutral_cwd


def _sealed_env() -> dict[str, str]:
    return {"PATH": "/usr/bin", "SEALED": "yes"}


def test_optional_maintenance_obligation_returns_before_any_preflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoskillit.cli import _marketplace

    monkeypatch.setattr(
        _marketplace,
        "_assert_not_worktree",
        lambda: pytest.fail("no-obligation maintenance reached preflight"),
    )
    result = _marketplace.install(request=_maintenance_request(required=False))
    assert result.outcome is InstallOutcome.NOT_REQUIRED
    assert not (tmp_path / ".autoskillit").exists()


def test_maintenance_distribution_mismatch_is_preflight_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marketplace, neutral_cwd = _configure_transaction(tmp_path, monkeypatch)
    monkeypatch.setattr(
        marketplace.importlib.metadata,
        "version",
        lambda _name: "9.9.9",
    )

    result = marketplace.install(
        request=_maintenance_request(),
        child_env=_sealed_env(),
        child_cwd=neutral_cwd,
    )

    assert result.outcome is InstallOutcome.FAILED
    assert result.failure_kind is InstallFailureKind.PREFLIGHT
    assert "expected distribution version" in result.findings[0]
    assert not (tmp_path / ".autoskillit" / "marketplace").exists()


def test_child_failure_restores_every_staged_shared_surface(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marketplace, neutral_cwd = _configure_transaction(tmp_path, monkeypatch)
    from autoskillit.cli import _plugin_artifact

    projection = tmp_path / ".autoskillit" / "marketplace"
    projection.mkdir(parents=True)
    (projection / "old.txt").write_text("old projection")
    known = tmp_path / ".claude" / "plugins" / "known_marketplaces.json"
    known.parent.mkdir(parents=True)
    known.write_text('{"old": true}')
    installed = marketplace._installed_plugins_json_path()
    installed.write_text('{"version": 2, "plugins": {"old": {}}}')
    retiring = tmp_path / ".autoskillit" / "retiring_cache.json"
    retiring.write_text('{"schema_version": 2, "records": []}')
    target = marketplace._installed_plugin_root(_VERSION)
    target.mkdir(parents=True)
    (target / "old.txt").write_text("old target")
    manifest = _plugin_artifact.installed_artifact_manifest_path(target)
    manifest.write_text('{"old": true}')
    before = {
        projection / "old.txt": "old projection",
        known: '{"old": true}',
        installed: '{"version": 2, "plugins": {"old": {}}}',
        retiring: '{"schema_version": 2, "records": []}',
        target / "old.txt": "old target",
        manifest: '{"old": true}',
    }

    def mutate_projection(**_kwargs) -> Path:
        (projection / "old.txt").unlink()
        (projection / "new.txt").write_text("new projection")
        return projection

    def fail_child(argv, **_kwargs):
        known.write_text('{"new": true}')
        installed.write_text('{"version": 2, "plugins": {"new": {}}}')
        retiring.write_text('{"schema_version": 2, "records": [{"new": true}]}')
        (target / "old.txt").write_text("new target")
        manifest.write_text('{"new": true}')
        return subprocess.CompletedProcess(argv, 7, stdout="", stderr="boom")

    monkeypatch.setattr(marketplace, "_ensure_marketplace", mutate_projection)
    monkeypatch.setattr(marketplace, "_run_claude_admin", fail_child)

    result = marketplace.install(
        request=_maintenance_request(),
        child_env=_sealed_env(),
        child_cwd=neutral_cwd,
    )

    assert result.outcome is InstallOutcome.FAILED
    assert result.failure_kind is InstallFailureKind.CHILD
    assert result.findings[-1] == "compensation completed"
    assert not (projection / "new.txt").exists()
    for path, content in before.items():
        assert path.read_text() == content


def test_incomplete_compensation_returns_recovery_required_with_both_diagnostics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marketplace, neutral_cwd = _configure_transaction(tmp_path, monkeypatch)
    real_rollback = marketplace._InstallSnapshot.rollback

    def incomplete_rollback(snapshot):
        return (*real_rollback(snapshot), "rollback failed for injected surface")

    monkeypatch.setattr(
        marketplace._InstallSnapshot,
        "rollback",
        incomplete_rollback,
    )
    monkeypatch.setattr(
        marketplace,
        "_run_claude_admin",
        lambda argv, **_kwargs: subprocess.CompletedProcess(
            argv,
            2,
            stdout="",
            stderr="primary child failure",
        ),
    )

    result = marketplace.install(
        request=_maintenance_request(),
        child_env=_sealed_env(),
        child_cwd=neutral_cwd,
    )

    assert result.outcome is InstallOutcome.RECOVERY_REQUIRED
    assert result.failure_kind is InstallFailureKind.ROLLBACK
    assert "primary child failure" in result.findings[0]
    assert "rollback failed" in result.findings[1]


def test_maintenance_uses_only_passed_env_and_non_project_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marketplace, neutral_cwd = _configure_transaction(tmp_path, monkeypatch)
    project_cwd = tmp_path / "ambient-project"
    project_cwd.mkdir()
    monkeypatch.chdir(project_cwd)
    monkeypatch.setenv("AMBIENT_ONLY", "must-not-leak")
    monkeypatch.setattr(
        "autoskillit.config.load_config",
        lambda _path: pytest.fail("maintenance read ambient backend config"),
    )
    calls: list[tuple[tuple[str, ...], dict[str, str], Path]] = []

    def capture(argv, *, env, cwd):
        calls.append((tuple(argv), dict(env), cwd))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(marketplace, "_run_claude_admin", capture)
    result = marketplace.install(
        request=_maintenance_request(),
        child_env=_sealed_env(),
        child_cwd=neutral_cwd,
    )

    assert result.outcome is InstallOutcome.COMPLETED
    assert len(calls) == 2
    assert all(env == _sealed_env() for _, env, _ in calls)
    assert all(cwd == neutral_cwd for _, _, cwd in calls)
    assert not (neutral_cwd / ".autoskillit").exists()


def test_direct_mode_snapshots_caller_env_and_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marketplace, _neutral_cwd = _configure_transaction(tmp_path, monkeypatch)
    from unittest.mock import MagicMock

    caller_cwd = tmp_path / "caller"
    changed_cwd = tmp_path / "changed"
    caller_cwd.mkdir()
    changed_cwd.mkdir()
    monkeypatch.chdir(caller_cwd)
    monkeypatch.setenv("DIRECT_SNAPSHOT", "original")
    cfg = MagicMock()
    cfg.agent_backend.backend = "claude-code"
    backend = MagicMock()
    backend.capabilities.plugin_install_capable = True
    monkeypatch.setattr("autoskillit.config.load_config", lambda _path: cfg)
    monkeypatch.setattr("autoskillit.execution.get_backend", lambda _name: backend)
    monkeypatch.setattr(marketplace, "_ensure_workspace_ready", lambda **_kwargs: None)
    calls: list[tuple[dict[str, str], Path]] = []

    def capture(argv, *, env, cwd):
        calls.append((dict(env), cwd))
        if len(calls) == 1:
            os.environ["DIRECT_SNAPSHOT"] = "changed"
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(marketplace, "_run_claude_admin", capture)
    result = marketplace.install(scope="user")

    assert result.outcome is InstallOutcome.COMPLETED
    assert [env["DIRECT_SNAPSHOT"] for env, _ in calls] == ["original", "original"]
    assert [cwd for _, cwd in calls] == [caller_cwd, caller_cwd]
