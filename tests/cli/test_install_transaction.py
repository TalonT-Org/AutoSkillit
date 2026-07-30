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


def _direct_request(*, scope: str = "user") -> InstallRequest:
    from autoskillit import __version__

    return InstallRequest(
        scope=scope,
        mode=InstallMode.DIRECT,
        require_registered_plugin=True,
        expected_version=__version__,
    )


@pytest.mark.parametrize(
    ("scope", "relative_path"),
    [
        ("user", Path(".claude/settings.json")),
        ("project", Path("project/.claude/settings.json")),
        ("local", Path("project/.claude/settings.local.json")),
    ],
)
def test_settings_path_preserves_claude_scope_distinctions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scope: str,
    relative_path: Path,
) -> None:
    from autoskillit.cli import _marketplace

    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    assert _marketplace._settings_path(scope, tmp_path / "project") == (tmp_path / relative_path)


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

    def successful_claude_admin(argv, **_kwargs):
        if tuple(argv)[:3] == ("claude", "plugin", "install"):
            target = _marketplace._installed_plugin_root(_VERSION)
            assert _marketplace._InstallSnapshot._shape(target) == "missing"
            target.mkdir(parents=True)
            (target / "fresh.txt").write_text("fresh", encoding="utf-8")
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout="",
            stderr="",
        )

    monkeypatch.setattr(
        _marketplace,
        "_run_claude_admin",
        successful_claude_admin,
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


@pytest.mark.parametrize("unsafe_kind", ["symlink", "non_directory", "escape"])
def test_unsafe_install_targets_are_rejected_before_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    unsafe_kind: str,
) -> None:
    marketplace, neutral_cwd = _configure_transaction(tmp_path, monkeypatch)
    target = marketplace._installed_plugin_root(_VERSION)
    external = tmp_path / "external-sentinel"
    external.mkdir()
    sentinel = external / "keep.txt"
    sentinel.write_text("untouched", encoding="utf-8")

    if unsafe_kind == "symlink":
        target.parent.mkdir(parents=True)
        target.symlink_to(external, target_is_directory=True)
    elif unsafe_kind == "non_directory":
        target.parent.mkdir(parents=True)
        target.write_text("not a directory", encoding="utf-8")
    else:
        monkeypatch.setattr(
            marketplace,
            "_installed_plugin_root",
            lambda _version: external,
        )

    child_calls: list[tuple[str, ...]] = []

    def record_child(argv, **_kwargs):
        child_calls.append(tuple(argv))
        raise AssertionError("unsafe target reached a mutating child command")

    monkeypatch.setattr(marketplace, "_run_claude_admin", record_child)

    result = marketplace.install(
        request=_maintenance_request(),
        child_env=_sealed_env(),
        child_cwd=neutral_cwd,
    )

    assert result.outcome is InstallOutcome.FAILED
    assert result.failure_kind is InstallFailureKind.PREFLIGHT
    assert "Unsafe installed plugin target" in result.findings[0]
    assert child_calls == []
    assert sentinel.read_text(encoding="utf-8") == "untouched"


@pytest.mark.parametrize("control_flow", [KeyboardInterrupt, SystemExit])
def test_control_flow_exceptions_compensate_before_propagation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    control_flow: type[BaseException],
) -> None:
    marketplace, neutral_cwd = _configure_transaction(tmp_path, monkeypatch)
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text("before", encoding="utf-8")

    def interrupt_after_mutation(**_kwargs) -> tuple[()]:
        settings.write_text("mutated", encoding="utf-8")
        raise control_flow("stop")

    monkeypatch.setattr(marketplace, "_clear_plugin_cache", interrupt_after_mutation)

    with pytest.raises(control_flow, match="stop"):
        marketplace.install(
            request=_maintenance_request(),
            child_env=_sealed_env(),
            child_cwd=neutral_cwd,
        )

    assert settings.read_text(encoding="utf-8") == "before"


def _filesystem_state(root: Path) -> tuple[tuple[str, str, bytes | str | None], ...]:
    state: list[tuple[str, str, bytes | str | None]] = []
    for path in sorted(root.rglob("*")):
        relative = str(path.relative_to(root))
        if path.is_symlink():
            state.append((relative, "symlink", os.readlink(path)))
        elif path.is_dir():
            state.append((relative, "directory", None))
        else:
            state.append((relative, "file", path.read_bytes()))
    return tuple(state)


def _instrument_transaction_ownership(
    monkeypatch: pytest.MonkeyPatch,
    events: list[str],
) -> dict[str, bool]:
    import autoskillit.core as core

    ownership = {"install_lock": False, "artifact_lease": False}

    class ObservedInstallLock:
        def __enter__(self) -> ObservedInstallLock:
            assert not ownership["install_lock"]
            ownership["install_lock"] = True
            events.append("install_lock_acquired")
            return self

        def __exit__(self, *_args: object) -> None:
            assert ownership["install_lock"]
            events.append("install_lock_released")
            ownership["install_lock"] = False

    class ObservedArtifactLease:
        def __init__(self, lock_path: Path) -> None:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            self._fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)

        @classmethod
        def acquire_exclusive(
            cls,
            lock_path: Path,
            *,
            blocking: bool = False,
        ) -> ObservedArtifactLease:
            assert not blocking
            assert ownership["install_lock"], (
                "exclusive artifact lease was acquired before the install lock"
            )
            assert not ownership["artifact_lease"]
            ownership["artifact_lease"] = True
            events.append("artifact_lease_acquired")
            return cls(lock_path)

        def __enter__(self) -> ObservedArtifactLease:
            assert ownership["artifact_lease"]
            events.append("artifact_lease_entered")
            return self

        def __exit__(self, *_args: object) -> None:
            assert ownership["artifact_lease"]
            events.append("artifact_lease_released")
            ownership["artifact_lease"] = False
            os.close(self._fd)

        def fileno(self) -> int:
            return self._fd

    monkeypatch.setattr(core, "_InstallLock", ObservedInstallLock)
    monkeypatch.setattr(core, "ArtifactLease", ObservedArtifactLease)
    return ownership


def _configure_direct_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    backend = SimpleNamespace(
        capabilities=SimpleNamespace(plugin_install_capable=True),
    )
    config = SimpleNamespace(
        agent_backend=SimpleNamespace(backend="claude-code"),
    )
    monkeypatch.setattr("autoskillit.config.load_config", lambda _path: config)
    monkeypatch.setattr("autoskillit.execution.get_backend", lambda _name: backend)


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


def test_install_boundary_rejects_worktree_without_persistent_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoskillit.cli import _marketplace

    home = tmp_path / "home"
    child_cwd = tmp_path / "neutral"
    unsafe_worktree = tmp_path / "unsafe-worktree"
    child_cwd.mkdir()
    unsafe_worktree.mkdir()
    marketplace_marker = home / ".autoskillit" / "marketplace" / "state.txt"
    registry_marker = home / ".claude" / "plugins" / "installed_plugins.json"
    marketplace_marker.parent.mkdir(parents=True)
    registry_marker.parent.mkdir(parents=True)
    marketplace_marker.write_text("published-prestate", encoding="utf-8")
    registry_marker.write_text('{"version": 2, "plugins": {}}', encoding="utf-8")

    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(_marketplace, "pkg_root", lambda: unsafe_worktree)
    monkeypatch.setattr(
        _marketplace,
        "is_git_worktree",
        lambda path: path == unsafe_worktree,
    )
    monkeypatch.setattr(
        _marketplace.importlib.metadata,
        "version",
        lambda _name: _VERSION,
    )
    before = _filesystem_state(tmp_path)

    result = _marketplace.install(
        request=_maintenance_request(),
        child_env=_sealed_env(),
        child_cwd=child_cwd,
    )

    assert result.outcome is InstallOutcome.FAILED
    assert result.failure_kind is InstallFailureKind.PREFLIGHT
    diagnostic = result.findings[0]
    assert "git worktree" in diagnostic
    assert f"Detected worktree path: {unsafe_worktree}" in diagnostic
    assert "run 'autoskillit install' from the main project checkout" in diagnostic
    assert _filesystem_state(tmp_path) == before


def test_success_path_holds_both_transaction_guards_through_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marketplace, neutral_cwd = _configure_transaction(tmp_path, monkeypatch)
    import autoskillit.workspace as workspace
    from autoskillit.cli.update import _update_checks

    events: list[str] = []
    ownership = _instrument_transaction_ownership(monkeypatch, events)

    def record_owned(event: str) -> None:
        assert ownership == {"install_lock": True, "artifact_lease": True}, (
            f"{event} ran outside the install lock or exclusive artifact lease"
        )
        events.append(event)

    def reconcile() -> tuple[()]:
        record_owned("reconciliation")
        return ()

    def clear_cache(**_kwargs) -> tuple[()]:
        record_owned("registry_cleanup")
        return ()

    def evict_direct(_path: Path) -> bool:
        record_owned("direct_registration_cleanup")
        return False

    def evict_hooks(_path: Path) -> None:
        record_owned("settings_hook_cleanup")

    def verify_exact(_spec):
        record_owned("exact_verification")
        return SimpleNamespace(
            identity=SimpleNamespace(incarnation_id="0" * 32),
            findings=(),
        )

    original_commit = marketplace._InstallSnapshot.commit

    def commit(snapshot) -> None:
        record_owned("commit")
        original_commit(snapshot)

    monkeypatch.setattr(workspace, "reconcile_install_artifacts", reconcile)
    monkeypatch.setattr(marketplace, "_clear_plugin_cache", clear_cache)
    monkeypatch.setattr(marketplace, "evict_direct_mcp_entry", evict_direct)
    monkeypatch.setattr(
        marketplace._hooks_mod,
        "_evict_stale_autoskillit_hooks",
        evict_hooks,
    )
    monkeypatch.setattr(
        _update_checks,
        "invalidate_fetch_cache",
        lambda _home: record_owned("fetch_cache_invalidation"),
    )
    monkeypatch.setattr(
        marketplace,
        "_verify_cleanup",
        lambda _settings, _fetch_cache: record_owned("cleanup_verification"),
    )
    monkeypatch.setattr(workspace, "verify_installed_plugin_artifact", verify_exact)
    monkeypatch.setattr(marketplace._InstallSnapshot, "commit", commit)

    result = marketplace.install(
        request=_maintenance_request(),
        child_env=_sealed_env(),
        child_cwd=neutral_cwd,
    )

    assert result.outcome is InstallOutcome.COMPLETED
    assert events == [
        "install_lock_acquired",
        "artifact_lease_acquired",
        "artifact_lease_entered",
        "reconciliation",
        "registry_cleanup",
        "direct_registration_cleanup",
        "settings_hook_cleanup",
        "fetch_cache_invalidation",
        "cleanup_verification",
        "exact_verification",
        "commit",
        "artifact_lease_released",
        "install_lock_released",
    ]
    assert ownership == {"install_lock": False, "artifact_lease": False}


def test_failure_path_holds_both_transaction_guards_through_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marketplace, neutral_cwd = _configure_transaction(tmp_path, monkeypatch)
    import autoskillit.workspace as workspace

    events: list[str] = []
    ownership = _instrument_transaction_ownership(monkeypatch, events)

    def assert_owned(event: str) -> None:
        assert ownership == {"install_lock": True, "artifact_lease": True}, (
            f"{event} ran outside the install lock or exclusive artifact lease"
        )
        events.append(event)

    def fail_reconciliation() -> tuple[()]:
        assert_owned("reconciliation")
        raise marketplace._InstallFailed(
            InstallFailureKind.POSTCONDITION,
            "injected reconciliation failure",
        )

    original_rollback = marketplace._InstallSnapshot.rollback

    def rollback(snapshot, *, owned_lease_fd: int | None = None) -> tuple[str, ...]:
        assert_owned("rollback")
        assert owned_lease_fd is not None
        os.fstat(owned_lease_fd)
        return original_rollback(snapshot, owned_lease_fd=owned_lease_fd)

    monkeypatch.setattr(
        workspace,
        "reconcile_install_artifacts",
        fail_reconciliation,
    )
    monkeypatch.setattr(marketplace._InstallSnapshot, "rollback", rollback)

    result = marketplace.install(
        request=_maintenance_request(),
        child_env=_sealed_env(),
        child_cwd=neutral_cwd,
    )

    assert result.outcome is InstallOutcome.FAILED
    assert result.failure_kind is InstallFailureKind.POSTCONDITION
    assert result.findings[-1] == "compensation completed"
    assert events == [
        "install_lock_acquired",
        "artifact_lease_acquired",
        "artifact_lease_entered",
        "reconciliation",
        "rollback",
        "artifact_lease_released",
        "install_lock_released",
    ]
    assert ownership == {"install_lock": False, "artifact_lease": False}


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
        _configure_direct_backend(monkeypatch)
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


def test_direct_install_failure_removes_transaction_created_workspace_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marketplace, project_cwd = _configure_transaction(tmp_path, monkeypatch)
    _configure_direct_backend(monkeypatch)
    project_state = project_cwd / ".autoskillit"
    project_state.mkdir()
    project_gitignore = project_state / ".gitignore"
    project_gitignore.write_text("preexisting\n", encoding="utf-8")
    project_temp = project_state / "temp"
    assert not project_temp.exists()
    workspace_prepared = False

    def fail_child_after_workspace_preparation(argv, **_kwargs):
        nonlocal workspace_prepared
        workspace_prepared = True
        assert project_temp.is_dir()
        assert (project_temp / ".gitignore").is_file()
        assert project_gitignore.read_text(encoding="utf-8") != "preexisting\n"
        return subprocess.CompletedProcess(
            argv,
            7,
            stdout="",
            stderr="injected child failure",
        )

    monkeypatch.setattr(
        marketplace,
        "_run_claude_admin",
        fail_child_after_workspace_preparation,
    )

    result = marketplace.install(
        request=InstallRequest(
            scope="user",
            mode=InstallMode.DIRECT,
            require_registered_plugin=True,
            expected_version=_VERSION,
        ),
        child_env=_sealed_env(),
        child_cwd=project_cwd,
    )

    assert workspace_prepared
    assert result.outcome is InstallOutcome.FAILED
    assert result.failure_kind is InstallFailureKind.CHILD
    assert result.findings[-1] == "compensation completed"
    assert project_gitignore.read_text(encoding="utf-8") == "preexisting\n"
    assert not project_temp.exists()


def test_direct_install_temp_shape_restore_failure_requires_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marketplace, project_cwd = _configure_transaction(tmp_path, monkeypatch)
    _configure_direct_backend(monkeypatch)
    project_state = project_cwd / ".autoskillit"
    project_state.mkdir()
    project_gitignore = project_state / ".gitignore"
    project_gitignore.write_text("preexisting\n", encoding="utf-8")
    project_temp = project_state / "temp"
    assert not project_temp.exists()
    residual = project_temp / "uncovered-residual.txt"

    def fail_child_with_uncovered_workspace_residual(argv, **_kwargs):
        assert (project_temp / ".gitignore").is_file()
        residual.write_text("preserve for recovery", encoding="utf-8")
        return subprocess.CompletedProcess(
            argv,
            7,
            stdout="",
            stderr="injected child failure",
        )

    monkeypatch.setattr(
        marketplace,
        "_run_claude_admin",
        fail_child_with_uncovered_workspace_residual,
    )

    result = marketplace.install(
        request=InstallRequest(
            scope="user",
            mode=InstallMode.DIRECT,
            require_registered_plugin=True,
            expected_version=_VERSION,
        ),
        child_env=_sealed_env(),
        child_cwd=project_cwd,
    )

    assert result.outcome is InstallOutcome.RECOVERY_REQUIRED
    assert result.failure_kind is InstallFailureKind.ROLLBACK
    assert any(
        finding.startswith(f"rollback failed for {project_temp}:") for finding in result.findings
    )
    evidence_finding = next(
        finding
        for finding in result.findings
        if finding.startswith("recovery evidence preserved at ")
    )
    evidence_dir = Path(evidence_finding.removeprefix("recovery evidence preserved at "))
    assert evidence_dir.is_dir()
    assert residual.read_text(encoding="utf-8") == "preserve for recovery"
    assert project_gitignore.read_text(encoding="utf-8") == "preexisting\n"


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


def test_same_version_target_is_quarantined_and_restored_after_child_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marketplace, neutral_cwd = _configure_transaction(tmp_path, monkeypatch)
    target = marketplace._installed_plugin_root(_VERSION)
    target.mkdir(parents=True)
    (target / "stale.txt").write_text("staged prestate", encoding="utf-8")
    saw_fresh_install_surface = False

    def fail_plugin_install(argv, **_kwargs):
        nonlocal saw_fresh_install_surface
        if tuple(argv)[:3] == ("claude", "plugin", "install"):
            assert marketplace._InstallSnapshot._shape(target) == "missing"
            saw_fresh_install_surface = True
            target.mkdir(parents=True)
            (target / "fresh.txt").write_text("failed install", encoding="utf-8")
            return subprocess.CompletedProcess(argv, 7, stdout="", stderr="boom")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(marketplace, "_run_claude_admin", fail_plugin_install)

    result = marketplace.install(
        request=_maintenance_request(),
        child_env=_sealed_env(),
        child_cwd=neutral_cwd,
    )

    assert saw_fresh_install_surface
    assert result.outcome is InstallOutcome.FAILED
    assert result.failure_kind is InstallFailureKind.CHILD
    assert result.findings[-1] == "compensation completed"
    assert (target / "stale.txt").read_text(encoding="utf-8") == "staged prestate"
    assert {entry.name for entry in target.iterdir()} == {"stale.txt"}


def test_failed_first_install_removes_new_lease_sidecar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marketplace, neutral_cwd = _configure_transaction(tmp_path, monkeypatch)
    from autoskillit.cli import _plugin_artifact

    target = marketplace._installed_plugin_root(_VERSION)
    lease_path = _plugin_artifact.installed_artifact_lock_path(target)
    assert marketplace._InstallSnapshot._shape(lease_path) == "missing"

    def fail_first_child(argv, **_kwargs):
        assert lease_path.is_file()
        return subprocess.CompletedProcess(argv, 7, stdout="", stderr="boom")

    monkeypatch.setattr(marketplace, "_run_claude_admin", fail_first_child)

    result = marketplace.install(
        request=_maintenance_request(),
        child_env=_sealed_env(),
        child_cwd=neutral_cwd,
    )

    assert result.outcome is InstallOutcome.FAILED
    assert result.failure_kind is InstallFailureKind.CHILD
    assert result.findings[-1] == "compensation completed"
    assert marketplace._InstallSnapshot._shape(lease_path) == "missing"


def test_failed_install_restores_existing_lease_sidecar_in_place(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marketplace, neutral_cwd = _configure_transaction(tmp_path, monkeypatch)
    from autoskillit.cli import _plugin_artifact

    target = marketplace._installed_plugin_root(_VERSION)
    lease_path = _plugin_artifact.installed_artifact_lock_path(target)
    lease_path.parent.mkdir(parents=True)
    lease_path.write_text("staged lease prestate", encoding="utf-8")
    lease_path.chmod(0o640)
    staged_inode = lease_path.lstat().st_ino

    def fail_first_child(argv, **_kwargs):
        acquired_stat = lease_path.lstat()
        assert acquired_stat.st_ino == staged_inode
        assert acquired_stat.st_mode & 0o777 == 0o600
        return subprocess.CompletedProcess(argv, 7, stdout="", stderr="boom")

    monkeypatch.setattr(marketplace, "_run_claude_admin", fail_first_child)

    result = marketplace.install(
        request=_maintenance_request(),
        child_env=_sealed_env(),
        child_cwd=neutral_cwd,
    )

    restored_stat = lease_path.lstat()
    assert result.outcome is InstallOutcome.FAILED
    assert result.failure_kind is InstallFailureKind.CHILD
    assert result.findings[-1] == "compensation completed"
    assert restored_stat.st_ino == staged_inode
    assert restored_stat.st_mode & 0o777 == 0o640
    assert lease_path.read_text(encoding="utf-8") == "staged lease prestate"


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


def test_snapshot_commit_is_final_before_staging_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autoskillit.cli import _marketplace

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    target = _marketplace._installed_plugin_root(_VERSION)
    target.mkdir(parents=True)
    payload = target / "payload.txt"
    payload.write_text("before", encoding="utf-8")
    snapshot = _marketplace._InstallSnapshot(target_root=target)
    snapshot.stage()
    payload.write_text("installed", encoding="utf-8")
    original_rmtree = _marketplace.shutil.rmtree

    def partially_remove_then_fail(path: Path, *args, **kwargs) -> None:
        if Path(path) == snapshot._stage_dir:
            staged_entry = next(snapshot._stage_dir.iterdir())
            _marketplace._InstallSnapshot._remove(staged_entry)
            raise OSError("injected partial staging cleanup failure")
        original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(_marketplace.shutil, "rmtree", partially_remove_then_fail)

    snapshot.commit()

    assert payload.read_text(encoding="utf-8") == "installed"
    assert snapshot.rollback() == ()
    assert snapshot._committed is True
    assert snapshot._staged is False
    assert snapshot._entries == []


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
        if tuple(argv)[:3] == ("claude", "plugin", "install"):
            target = marketplace._installed_plugin_root(_VERSION)
            assert marketplace._InstallSnapshot._shape(target) == "missing"
            target.mkdir(parents=True)
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

    from autoskillit import __version__ as installed_version

    caller_cwd = tmp_path / "caller"
    changed_cwd = tmp_path / "changed"
    caller_cwd.mkdir()
    changed_cwd.mkdir()
    monkeypatch.chdir(caller_cwd)
    monkeypatch.setenv("DIRECT_SNAPSHOT", "original")
    _configure_direct_backend(monkeypatch)
    monkeypatch.setattr(marketplace, "_ensure_workspace_ready", lambda **_kwargs: None)
    calls: list[tuple[dict[str, str], Path]] = []

    def capture(argv, *, env, cwd):
        calls.append((dict(env), cwd))
        if len(calls) == 1:
            os.environ["DIRECT_SNAPSHOT"] = "changed"
        if tuple(argv)[:3] == ("claude", "plugin", "install"):
            target = marketplace._installed_plugin_root(installed_version)
            assert marketplace._InstallSnapshot._shape(target) == "missing"
            target.mkdir(parents=True)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(marketplace, "_run_claude_admin", capture)
    result = marketplace.install(request=_direct_request())

    assert result.outcome is InstallOutcome.COMPLETED
    assert [env["DIRECT_SNAPSHOT"] for env, _ in calls] == ["original", "original"]
    assert [cwd for _, cwd in calls] == [caller_cwd, caller_cwd]
