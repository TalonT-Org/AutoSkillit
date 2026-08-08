"""Typed install transaction and sealed-context tests.

``install()`` (Phase 4.4 of #4480) publishes a generation-keyed plugin
artifact: it stages a fresh generation under the AutoSkillit-owned
generation store and flips an atomic ``current`` selector to publish it.
There is no more Claude-CLI subprocess boundary, no pre-mutation snapshot,
and no rollback/compensation step — ``publish_generation`` itself is the
sole commit point and is safe by construction (fresh-path staging). A
failure anywhere in ``install()`` simply returns ``InstallOutcome.FAILED``
with a diagnostic finding; the trailing ``_verify_cleanup`` postcondition
check is the last stage before success is reported.
"""

from __future__ import annotations

import importlib
import os
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
    from autoskillit.cli import _hooks

    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    assert _hooks._claude_settings_path(
        scope,
        cwd=tmp_path / "project",
    ) == (tmp_path / relative_path)


def _configure_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[ModuleType, Path]:
    """Configure a fully successful baseline generation-publication install.

    Individual tests override specific steps to inject failures. Since
    ``tmp_path`` starts empty, the trailing ``_verify_cleanup`` postcondition
    check passes unmocked: there is no stale direct MCP registration, hook,
    or fetch cache for it to find.
    """
    import autoskillit.workspace as workspace
    from autoskillit.cli import _marketplace

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
    monkeypatch.setattr(workspace, "reconcile_install_artifacts", lambda: ())
    monkeypatch.setattr(
        _marketplace,
        "_ensure_marketplace",
        lambda **_kwargs: tmp_path / ".autoskillit" / "marketplace",
    )

    def publish_generation(
        *,
        home: Path,
        plugin_ref: str,
        version: str,
        semantic_key: str,
        source_root: Path,
    ) -> SimpleNamespace:
        assert home == tmp_path
        assert plugin_ref == _PLUGIN_REF
        assert semantic_key == f"{plugin_ref}:{version}"
        assert source_root == (
            tmp_path / ".autoskillit" / "marketplace" / "plugins" / "autoskillit"
        )
        return SimpleNamespace(
            semantic_key=semantic_key,
            incarnation_id="0" * 32,
        )

    monkeypatch.setattr(workspace, "publish_generation", publish_generation)
    monkeypatch.setattr(_marketplace, "evict_direct_mcp_entry", lambda _path: False)
    monkeypatch.setattr(
        _marketplace._hooks_mod,
        "_evict_stale_autoskillit_hooks",
        lambda _path: None,
    )
    monkeypatch.setattr(update_checks, "invalidate_fetch_cache", lambda _home: None)
    return _marketplace, neutral_cwd


def _sealed_env() -> dict[str, str]:
    return {"PATH": "/usr/bin", "SEALED": "yes"}


@pytest.mark.parametrize("control_flow", [KeyboardInterrupt, SystemExit])
def test_control_flow_exceptions_propagate_with_lock_released(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    control_flow: type[BaseException],
) -> None:
    """Non-Exception BaseExceptions re-raise after the install lock unwinds.

    ``install()`` no longer stages a filesystem snapshot to compensate: the
    generic ``except BaseException`` handler re-raises anything that is not
    an ``Exception`` once it has logged it, and the surrounding
    ``with _InstallLock():`` block releases the lock as it unwinds.
    """
    marketplace, neutral_cwd = _configure_transaction(tmp_path, monkeypatch)
    import autoskillit.workspace as workspace

    events: list[str] = []
    ownership = _instrument_transaction_ownership(monkeypatch, events)

    def interrupt() -> tuple[()]:
        raise control_flow("stop")

    monkeypatch.setattr(workspace, "reconcile_install_artifacts", interrupt)

    with pytest.raises(control_flow, match="stop"):
        marketplace.install(
            request=_maintenance_request(),
            child_env=_sealed_env(),
            child_cwd=neutral_cwd,
        )

    assert events == ["install_lock_acquired", "install_lock_released"]
    assert ownership == {"install_lock": False}


def _filesystem_state(
    root: Path,
    *,
    excluded: tuple[Path, ...] = (),
) -> tuple[tuple[str, str, bytes | str | None], ...]:
    state: list[tuple[str, str, bytes | str | None]] = []
    for path in sorted(root.rglob("*")):
        if path in excluded:
            continue
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
    """Wrap ``_InstallLock`` to observe acquire/release ordering.

    ``install()`` no longer takes an exclusive ``ArtifactLease`` on the
    target root — generation publication never contends with readers, so
    the install lock is the only transaction guard left to observe.
    """
    import autoskillit.core as core

    ownership = {"install_lock": False}

    class ObservedInstallLock(core._InstallLock):
        def __enter__(self) -> ObservedInstallLock:
            assert not ownership["install_lock"]
            acquired = super().__enter__()
            assert acquired is self
            ownership["install_lock"] = True
            events.append("install_lock_acquired")
            return self

        def __exit__(self, *_args: object) -> None:
            assert ownership["install_lock"]
            super().__exit__(*_args)
            events.append("install_lock_released")
            ownership["install_lock"] = False

    monkeypatch.setattr(core, "_InstallLock", ObservedInstallLock)
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
    assert "not required" in result.findings[0]
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


def test_success_path_holds_install_lock_through_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marketplace, neutral_cwd = _configure_transaction(tmp_path, monkeypatch)
    import autoskillit.workspace as workspace
    from autoskillit.cli.update import _update_checks

    events: list[str] = []
    ownership = _instrument_transaction_ownership(monkeypatch, events)

    def record_owned(event: str) -> None:
        assert ownership == {"install_lock": True}, f"{event} ran outside the install lock"
        events.append(event)

    def reconcile() -> tuple[()]:
        record_owned("reconciliation")
        return ()

    def ensure_marketplace(**_kwargs) -> Path:
        record_owned("marketplace_projection")
        return tmp_path / ".autoskillit" / "marketplace"

    published_identity = SimpleNamespace(
        semantic_key=f"{_PLUGIN_REF}:{_VERSION}",
        incarnation_id="0" * 32,
    )

    def publish(**_kwargs):
        record_owned("generation_publication")
        return published_identity

    def evict_direct(_path: Path) -> bool:
        record_owned("direct_registration_cleanup")
        return False

    def evict_hooks(_path: Path) -> None:
        record_owned("settings_hook_cleanup")

    def invalidate(_home: Path) -> None:
        record_owned("fetch_cache_invalidation")

    def verify_cleanup(_settings_path: Path, _fetch_cache_path: Path) -> None:
        record_owned("cleanup_verification")

    monkeypatch.setattr(workspace, "reconcile_install_artifacts", reconcile)
    monkeypatch.setattr(marketplace, "_ensure_marketplace", ensure_marketplace)
    monkeypatch.setattr(workspace, "publish_generation", publish)
    monkeypatch.setattr(marketplace, "evict_direct_mcp_entry", evict_direct)
    monkeypatch.setattr(marketplace._hooks_mod, "_evict_stale_autoskillit_hooks", evict_hooks)
    monkeypatch.setattr(_update_checks, "invalidate_fetch_cache", invalidate)
    monkeypatch.setattr(marketplace, "_verify_cleanup", verify_cleanup)

    result = marketplace.install(
        request=_maintenance_request(),
        child_env=_sealed_env(),
        child_cwd=neutral_cwd,
    )

    assert result.outcome is InstallOutcome.COMPLETED
    assert result.verified_identity == f"{_PLUGIN_REF}:{_VERSION}"
    success_message = f"Plugin published: {_PLUGIN_REF} (scope: user)"
    assert result.findings == (success_message,)
    assert events == [
        "install_lock_acquired",
        "reconciliation",
        "marketplace_projection",
        "generation_publication",
        "direct_registration_cleanup",
        "settings_hook_cleanup",
        "fetch_cache_invalidation",
        "cleanup_verification",
        "install_lock_released",
    ]
    assert ownership == {"install_lock": False}


def test_failure_path_releases_install_lock_without_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marketplace, neutral_cwd = _configure_transaction(tmp_path, monkeypatch)
    import autoskillit.workspace as workspace

    events: list[str] = []
    ownership = _instrument_transaction_ownership(monkeypatch, events)

    def fail_reconciliation() -> tuple[()]:
        assert ownership == {"install_lock": True}
        events.append("reconciliation")
        raise marketplace._InstallFailed(
            InstallFailureKind.POSTCONDITION,
            "injected reconciliation failure",
        )

    monkeypatch.setattr(workspace, "reconcile_install_artifacts", fail_reconciliation)

    result = marketplace.install(
        request=_maintenance_request(),
        child_env=_sealed_env(),
        child_cwd=neutral_cwd,
    )

    assert result.outcome is InstallOutcome.FAILED
    assert result.failure_kind is InstallFailureKind.POSTCONDITION
    assert result.findings == ("postcondition failure: injected reconciliation failure",)
    assert events == [
        "install_lock_acquired",
        "reconciliation",
        "install_lock_released",
    ]
    assert ownership == {"install_lock": False}


@pytest.mark.parametrize(
    "stage",
    [
        "reconciliation",
        "marketplace_projection",
        "workspace_readiness",
        "generation_publication",
        "direct_registration_cleanup",
        "settings_hook_cleanup",
        "fetch_cache_invalidation",
        "cleanup_verification",
    ],
)
def test_failure_at_every_stage_returns_failed_postcondition(
    stage: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every step inside the install lock funnels a failure to POSTCONDITION.

    ``publish_generation`` failures are wrapped explicitly in
    ``_InstallFailed(POSTCONDITION, ...)``; every other step's exception
    (including the trailing ``_verify_cleanup`` postcondition check) falls
    through to the generic ``except BaseException`` handler, which also
    reports POSTCONDITION. Neither path restores any prestate — there is
    nothing to compensate.
    """
    marketplace, neutral_cwd = _configure_transaction(tmp_path, monkeypatch)
    import autoskillit.workspace as workspace
    from autoskillit.cli.update import _update_checks

    reached: list[str] = []
    message = f"injected {stage} failure"

    def _raise(*_args, **_kwargs):
        reached.append(stage)
        raise RuntimeError(message)

    if stage == "reconciliation":
        monkeypatch.setattr(workspace, "reconcile_install_artifacts", _raise)
    elif stage == "marketplace_projection":
        monkeypatch.setattr(marketplace, "_ensure_marketplace", _raise)
    elif stage == "workspace_readiness":
        monkeypatch.setattr(marketplace, "_ensure_workspace_ready", _raise)
        _configure_direct_backend(monkeypatch)
    elif stage == "generation_publication":
        monkeypatch.setattr(workspace, "publish_generation", _raise)
    elif stage == "direct_registration_cleanup":
        monkeypatch.setattr(marketplace, "evict_direct_mcp_entry", _raise)
    elif stage == "settings_hook_cleanup":
        monkeypatch.setattr(marketplace._hooks_mod, "_evict_stale_autoskillit_hooks", _raise)
    elif stage == "fetch_cache_invalidation":
        monkeypatch.setattr(_update_checks, "invalidate_fetch_cache", _raise)
    else:
        monkeypatch.setattr(marketplace, "_verify_cleanup", _raise)

    if stage == "workspace_readiness":
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
    assert result.failure_kind is InstallFailureKind.POSTCONDITION
    assert message in result.findings[0]


def test_verify_cleanup_detects_residual_direct_mcp_registration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_verify_cleanup`` fails the install if eviction did not stick."""
    marketplace, neutral_cwd = _configure_transaction(tmp_path, monkeypatch)
    direct_registration = tmp_path / ".claude.json"
    monkeypatch.setattr(marketplace, "_user_claude_json_path", lambda: direct_registration)
    direct_registration.write_text(
        '{"mcpServers": {"autoskillit": {}}}',
        encoding="utf-8",
    )

    result = marketplace.install(
        request=_maintenance_request(),
        child_env=_sealed_env(),
        child_cwd=neutral_cwd,
    )

    assert result.outcome is InstallOutcome.FAILED
    assert result.failure_kind is InstallFailureKind.POSTCONDITION
    assert "Stale direct MCP registration remains after eviction" in result.findings[0]


def test_maintenance_ignores_ambient_cwd_and_backend_config(
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
    calls: list[Path] = []

    def ensure_marketplace(*, cwd: Path, version: str) -> Path:
        calls.append(Path(cwd))
        return tmp_path / ".autoskillit" / "marketplace"

    monkeypatch.setattr(marketplace, "_ensure_marketplace", ensure_marketplace)

    result = marketplace.install(
        request=_maintenance_request(),
        child_env=_sealed_env(),
        child_cwd=neutral_cwd,
    )

    assert result.outcome is InstallOutcome.COMPLETED
    assert calls == [neutral_cwd]
    assert not (neutral_cwd / ".autoskillit").exists()


def test_direct_mode_defaults_to_ambient_cwd_when_unspecified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marketplace, _neutral_cwd = _configure_transaction(tmp_path, monkeypatch)

    caller_cwd = tmp_path / "caller"
    caller_cwd.mkdir()
    monkeypatch.chdir(caller_cwd)
    # DIRECT mode without an explicit child_cwd snapshots the caller's
    # ambient os.getcwd(). A host Claude Code session running this suite
    # sets CLAUDECODE, which would otherwise defer the install.
    monkeypatch.delenv("CLAUDECODE", raising=False)
    _configure_direct_backend(monkeypatch)
    calls: list[tuple[str, Path]] = []

    def ensure_marketplace(*, cwd: Path, version: str) -> Path:
        calls.append(("marketplace", Path(cwd)))
        return tmp_path / ".autoskillit" / "marketplace"

    def ensure_workspace_ready(*, cwd: Path) -> None:
        calls.append(("workspace", Path(cwd)))

    monkeypatch.setattr(marketplace, "_ensure_marketplace", ensure_marketplace)
    monkeypatch.setattr(marketplace, "_ensure_workspace_ready", ensure_workspace_ready)

    result = marketplace.install(request=_direct_request())

    assert result.outcome is InstallOutcome.COMPLETED
    assert calls == [("marketplace", caller_cwd), ("workspace", caller_cwd)]
