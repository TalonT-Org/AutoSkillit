"""Typed install transaction, compensation, and sealed-context tests."""

from __future__ import annotations

import importlib
import os
import subprocess
from pathlib import Path
from types import ModuleType, SimpleNamespace

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
) -> tuple[ModuleType, Path]:
    import autoskillit.workspace as workspace
    from autoskillit.cli import _marketplace, _plugin_artifact

    update_checks = importlib.import_module("autoskillit.cli.update._update_checks")
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
    monkeypatch.setattr(
        _marketplace,
        "_run_claude_admin",
        lambda argv, **_kwargs: subprocess.CompletedProcess(
            argv,
            0,
            stdout="",
            stderr="",
        ),
    )
    monkeypatch.setattr(update_checks, "invalidate_fetch_cache", lambda _home: None)
    identity = SimpleNamespace(incarnation_id="0" * 32)
    monkeypatch.setattr(
        workspace,
        "verify_installed_plugin_artifact",
        lambda _spec: SimpleNamespace(identity=identity, findings=()),
    )
    return _marketplace, neutral_cwd


def _sealed_env() -> dict[str, str]:
    return {"PATH": "/usr/bin", "SEALED": "yes"}


def test_claude_lookup_uses_sealed_path_once_without_ambient_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoskillit.cli import _marketplace

    calls: list[tuple[str, str | None]] = []

    def which(_cmd: str, *, path: str | None = None) -> str | None:
        calls.append((_cmd, path))
        if path is not None:
            raise TypeError("sealed lookup failure")
        return "/ambient/bin/claude"

    monkeypatch.setattr(_marketplace.shutil, "which", which)

    with pytest.raises(TypeError, match="sealed lookup failure"):
        _marketplace._claude_on_path({"PATH": "/sealed/bin"})

    assert calls == [("claude", "/sealed/bin")]


def test_optional_maintenance_obligation_returns_before_any_preflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from autoskillit.cli import _marketplace

    monkeypatch.setattr(
        _marketplace,
        "_assert_not_worktree",
        lambda: pytest.fail("no-obligation maintenance reached preflight"),
    )
    result = _marketplace.install(request=_maintenance_request(required=False))
    assert result.outcome is InstallOutcome.NOT_REQUIRED
    assert "not required" in capsys.readouterr().out
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


@pytest.mark.parametrize(
    "stage",
    [
        "reconciliation",
        "public_marketplace_projection",
        "marketplace_manifest",
        "known_marketplaces_registration_state",
        "registry_cache_mutation",
        "claude_marketplace_registration",
        "claude_plugin_installation",
        "identity_and_retirement_publication",
        "settings_hook_cleanup",
        "direct_registration_cleanup",
        "workspace_mutation",
        "fetch_cache_invalidation",
        "exact_postcondition_verification",
    ],
)
def test_failure_after_every_persistent_mutation_stage_restores_prestate(
    stage: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    marketplace, neutral_cwd = _configure_transaction(tmp_path, monkeypatch)
    import autoskillit.workspace as workspace
    from autoskillit.cli import _plugin_artifact
    from autoskillit.cli.update import _update_checks
    from autoskillit.cli.update._update_checks_fetch import _fetch_cache_path

    projection = tmp_path / ".autoskillit" / "marketplace"
    manifest = projection / ".claude-plugin" / "marketplace.json"
    known = tmp_path / ".claude" / "plugins" / "known_marketplaces.json"
    installed = marketplace._installed_plugins_json_path()
    target = marketplace._installed_plugin_root(_VERSION)
    identity = _plugin_artifact.installed_artifact_manifest_path(target)
    retiring = tmp_path / ".autoskillit" / "retiring_cache.json"
    settings = tmp_path / ".claude" / "settings.json"
    direct_registration = marketplace._user_claude_json_path()
    workspace_marker = neutral_cwd / ".autoskillit" / ".gitignore"
    fetch_cache = _fetch_cache_path(tmp_path)

    surfaces = {
        "reconciliation": retiring,
        "public_marketplace_projection": projection / "plugins" / "autoskillit" / "state.txt",
        "marketplace_manifest": manifest,
        "known_marketplaces_registration_state": known,
        "registry_cache_mutation": installed,
        "claude_marketplace_registration": known,
        "claude_plugin_installation": target / "plugin-state.txt",
        "identity_and_retirement_publication": identity,
        "settings_hook_cleanup": settings,
        "direct_registration_cleanup": direct_registration,
        "workspace_mutation": workspace_marker,
        "fetch_cache_invalidation": fetch_cache,
        "exact_postcondition_verification": target / "verified-state.txt",
    }
    surface = surfaces[stage]
    surface.parent.mkdir(parents=True, exist_ok=True)
    surface.write_text("before", encoding="utf-8")
    if stage == "identity_and_retirement_publication":
        retiring.parent.mkdir(parents=True, exist_ok=True)
        retiring.write_text("before-retirement", encoding="utf-8")

    reached: list[str] = []
    verification_events: list[str] = []

    def mutate_surface() -> None:
        surface.parent.mkdir(parents=True, exist_ok=True)
        surface.write_text("after", encoding="utf-8")

    def raise_after_stage() -> None:
        reached.append(stage)
        raise marketplace._InstallFailed(
            InstallFailureKind.POSTCONDITION,
            f"injected after {stage}",
        )

    def mutate_then_raise() -> None:
        mutate_surface()
        raise_after_stage()

    if stage == "reconciliation":
        monkeypatch.setattr(workspace, "reconcile_install_artifacts", mutate_then_raise)
    elif stage in {"public_marketplace_projection", "marketplace_manifest"}:
        monkeypatch.setattr(
            marketplace,
            "_ensure_marketplace",
            lambda **_kwargs: mutate_then_raise(),
        )
    elif stage == "known_marketplaces_registration_state":

        def fail_marketplace_child(argv, **_kwargs):
            mutate_surface()
            reached.append(stage)
            return subprocess.CompletedProcess(
                argv,
                7,
                stdout="",
                stderr="injected registration failure",
            )

        monkeypatch.setattr(marketplace, "_run_claude_admin", fail_marketplace_child)
    elif stage == "registry_cache_mutation":
        monkeypatch.setattr(
            marketplace,
            "_clear_plugin_cache",
            lambda **_kwargs: mutate_then_raise(),
        )
    elif stage == "claude_marketplace_registration":
        child_calls = 0

        def fail_after_marketplace_child(argv, **_kwargs):
            nonlocal child_calls
            child_calls += 1
            if child_calls == 1:
                mutate_surface()
                return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
            raise_after_stage()

        monkeypatch.setattr(marketplace, "_run_claude_admin", fail_after_marketplace_child)
    elif stage == "claude_plugin_installation":
        child_calls = 0

        def install_then_continue(argv, **_kwargs):
            nonlocal child_calls
            child_calls += 1
            if child_calls == 2:
                mutate_surface()
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

        monkeypatch.setattr(marketplace, "_run_claude_admin", install_then_continue)
        monkeypatch.setattr(
            _plugin_artifact,
            "publish_installed_plugin_artifact",
            lambda *_args, **_kwargs: raise_after_stage(),
        )
    elif stage == "identity_and_retirement_publication":

        def fail_identity_publication(*_args, **_kwargs):
            mutate_surface()
            retiring.write_text("after-retirement", encoding="utf-8")
            raise_after_stage()

        monkeypatch.setattr(
            _plugin_artifact,
            "publish_installed_plugin_artifact",
            fail_identity_publication,
        )
    elif stage == "settings_hook_cleanup":
        monkeypatch.setattr(
            marketplace._hooks_mod,
            "_evict_stale_autoskillit_hooks",
            lambda _path: mutate_then_raise(),
        )
    elif stage == "direct_registration_cleanup":
        monkeypatch.setattr(
            marketplace,
            "evict_direct_mcp_entry",
            lambda _path: mutate_then_raise(),
        )
    elif stage == "workspace_mutation":
        monkeypatch.setattr(
            marketplace,
            "_ensure_workspace_ready",
            lambda **_kwargs: mutate_then_raise(),
        )
        backend = SimpleNamespace(
            capabilities=SimpleNamespace(plugin_install_capable=True),
        )
        config = SimpleNamespace(
            agent_backend=SimpleNamespace(backend="claude-code"),
        )
        monkeypatch.setattr("autoskillit.config.load_config", lambda _path: config)
        monkeypatch.setattr("autoskillit.execution.get_backend", lambda _name: backend)
    elif stage == "fetch_cache_invalidation":
        monkeypatch.setattr(
            _update_checks,
            "invalidate_fetch_cache",
            lambda _home: mutate_then_raise(),
        )
    else:
        verified_identity = SimpleNamespace(incarnation_id="0" * 32)

        def record_verification(_spec):
            verification_events.append("verified")
            return SimpleNamespace(identity=verified_identity, findings=())

        def fail_final_commit(_snapshot):
            assert verification_events == ["verified"]
            mutate_then_raise()

        monkeypatch.setattr(
            workspace,
            "verify_installed_plugin_artifact",
            record_verification,
        )
        monkeypatch.setattr(
            marketplace._InstallSnapshot,
            "commit",
            fail_final_commit,
        )

    if stage == "workspace_mutation":
        request = InstallRequest(
            scope="user",
            mode=InstallMode.DIRECT,
            require_registered_plugin=True,
            expected_version=_VERSION,
        )
    else:
        request = _maintenance_request()

    result = marketplace.install(
        request=request,
        child_env=_sealed_env(),
        child_cwd=neutral_cwd,
    )

    assert reached == [stage]
    assert result.outcome is InstallOutcome.FAILED
    assert result.findings[-1] == "compensation completed"
    assert surface.read_text(encoding="utf-8") == "before"
    if stage == "identity_and_retirement_publication":
        assert retiring.read_text(encoding="utf-8") == "before-retirement"
    if stage == "exact_postcondition_verification":
        assert verification_events == ["verified"]
    assert "Plugin installed:" not in capsys.readouterr().out


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


def test_snapshot_restores_directory_and_symlink_shapes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoskillit.cli import _marketplace

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    projection = tmp_path / ".autoskillit" / "marketplace"
    projection.mkdir(parents=True)
    (projection / "old.txt").write_text("old directory", encoding="utf-8")

    original_target = tmp_path / "original-plugin"
    replacement_target = tmp_path / "replacement-plugin"
    original_target.mkdir()
    replacement_target.mkdir()
    target = _marketplace._installed_plugin_root(_VERSION)
    target.parent.mkdir(parents=True)
    target.symlink_to(original_target, target_is_directory=True)

    snapshot = _marketplace._InstallSnapshot(target_root=target)
    snapshot.stage()

    _marketplace._InstallSnapshot._remove(projection)
    projection.mkdir()
    (projection / "new.txt").write_text("new directory", encoding="utf-8")
    target.unlink()
    target.symlink_to(replacement_target, target_is_directory=True)

    assert snapshot.rollback() == ()
    assert (projection / "old.txt").read_text(encoding="utf-8") == "old directory"
    assert not (projection / "new.txt").exists()
    assert target.is_symlink()
    assert os.readlink(target) == str(original_target)
    assert not snapshot._stage_dir.exists()


def test_incomplete_compensation_returns_recovery_required_with_both_diagnostics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marketplace, neutral_cwd = _configure_transaction(tmp_path, monkeypatch)
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text("before", encoding="utf-8")
    real_restore_entry = marketplace._InstallSnapshot._restore_entry

    def fail_settings_restore(_cls, path, shape, backup):
        if path == settings:
            raise OSError("injected settings restoration failure")
        return real_restore_entry(path, shape, backup)

    monkeypatch.setattr(
        marketplace._InstallSnapshot,
        "_restore_entry",
        classmethod(fail_settings_restore),
    )

    def fail_child_after_mutation(argv, **_kwargs):
        settings.write_text("residual mutation", encoding="utf-8")
        return subprocess.CompletedProcess(
            argv,
            2,
            stdout="",
            stderr="primary child failure",
        )

    monkeypatch.setattr(marketplace, "_run_claude_admin", fail_child_after_mutation)

    result = marketplace.install(
        request=_maintenance_request(),
        child_env=_sealed_env(),
        child_cwd=neutral_cwd,
    )

    assert result.outcome is InstallOutcome.RECOVERY_REQUIRED
    assert result.failure_kind is InstallFailureKind.ROLLBACK
    assert "primary child failure" in result.findings[0]
    assert any("rollback failed" in finding for finding in result.findings)
    assert any(
        (
            f"residual state for {settings}: expected file, observed file; "
            "differs from staged prestate"
        )
        == finding
        for finding in result.findings
    )
    evidence_finding = next(
        finding
        for finding in result.findings
        if finding.startswith("recovery evidence preserved at ")
    )
    evidence_dir = Path(evidence_finding.removeprefix("recovery evidence preserved at "))
    assert evidence_dir.is_dir()
    assert any(
        backup.is_file() and backup.read_text(encoding="utf-8") == "before"
        for backup in evidence_dir.iterdir()
    )
    assert settings.read_text(encoding="utf-8") == "residual mutation"


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
