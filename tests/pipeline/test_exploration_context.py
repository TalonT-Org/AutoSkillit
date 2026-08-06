"""Security tests for brokered exploration capability lifecycle."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import autoskillit.pipeline as pipeline_module
import autoskillit.pipeline.exploration_context as exploration_context_module
from autoskillit.core import ExplorationQuerySpec, RepositoryIdentity, RepositorySnapshot
from autoskillit.pipeline.exploration_context import (
    EXPLORATION_AUTHORITY_PATH_ENV,
    EXPLORATION_CAPABILITY_ENV,
    EXPLORATION_PRINCIPAL_ROLE,
    EXPLORATION_ROLE_ENV,
    EXPLORATION_SESSION_ENV,
    CapabilityResolutionStatus,
    OwnerBoundExplorationContextStore,
)

pytestmark = [pytest.mark.layer("pipeline"), pytest.mark.small]


def test_exploration_environment_names_are_public_pipeline_contracts() -> None:
    environment_names = {
        "EXPLORATION_AUTHORITY_PATH_ENV",
        "EXPLORATION_CAPABILITY_ENV",
        "EXPLORATION_ROLE_ENV",
        "EXPLORATION_SESSION_ENV",
    }

    assert environment_names <= set(exploration_context_module.__all__)
    assert environment_names <= set(pipeline_module.__all__)


def _snapshot_service() -> MagicMock:
    service = MagicMock()
    service.capture_snapshot.side_effect = lambda root: RepositorySnapshot(
        RepositoryIdentity("test-repository", "test-revision", worktree_path=str(root.resolve())),
        tree_digest="test-tree",
        collector_manifest_digest="test-manifest",
    )
    return service


def test_capability_is_opaque_and_bound_to_owner_role_and_session() -> None:
    store: OwnerBoundExplorationContextStore[str] = OwnerBoundExplorationContextStore()
    capability = store.issue(
        owner_id="owner-a",
        role="semantic-code-navigator",
        session_id="session-a",
        value="trusted-state",
    )

    assert capability.startswith("explore_")
    assert len(capability) <= 128
    assert (
        store.resolve(
            capability=capability,
            owner_id="owner-a",
            role="semantic-code-navigator",
            session_id="session-a",
        ).value
        == "trusted-state"
    )
    assert (
        store.resolve(
            capability=capability,
            owner_id="owner-b",
            role="semantic-code-navigator",
            session_id="session-a",
        ).status
        is CapabilityResolutionStatus.OWNER_MISMATCH
    )
    assert (
        store.resolve(
            capability=capability,
            owner_id="owner-a",
            role="repository-impact-profiler",
            session_id="session-a",
        ).status
        is CapabilityResolutionStatus.ROLE_MISMATCH
    )
    assert (
        store.resolve(
            capability=capability,
            owner_id="owner-a",
            role="semantic-code-navigator",
            session_id="session-b",
        ).status
        is CapabilityResolutionStatus.SESSION_MISMATCH
    )


def test_expired_or_discarded_capability_cannot_be_reused() -> None:
    now = 10.0
    store: OwnerBoundExplorationContextStore[str] = OwnerBoundExplorationContextStore(
        clock=lambda: now,
    )
    capability = store.issue(
        owner_id="owner-a",
        role="semantic-code-navigator",
        session_id="session-a",
        value="trusted-state",
        ttl_seconds=1,
    )

    now = 11.0
    assert (
        store.resolve(
            capability=capability,
            owner_id="owner-a",
            role="semantic-code-navigator",
            session_id="session-a",
        ).status
        is CapabilityResolutionStatus.EXPIRED
    )
    assert store.cleanup_expired() == 0

    fresh = store.issue(
        owner_id="owner-a",
        role="semantic-code-navigator",
        session_id="session-a",
        value="replacement-state",
    )
    store.discard(fresh)
    assert (
        store.resolve(
            capability=fresh,
            owner_id="owner-a",
            role="semantic-code-navigator",
            session_id="session-a",
        ).status
        is CapabilityResolutionStatus.INVALID
    )


def test_close_removes_all_capabilities_and_rejects_future_issuance() -> None:
    store: OwnerBoundExplorationContextStore[str] = OwnerBoundExplorationContextStore()
    capability = store.issue(
        owner_id="owner-a",
        role="semantic-code-navigator",
        session_id="session-a",
        value="trusted-state",
    )

    store.close()

    assert (
        store.resolve(
            capability=capability,
            owner_id="owner-a",
            role="semantic-code-navigator",
            session_id="session-a",
        ).status
        is CapabilityResolutionStatus.INVALID
    )
    with pytest.raises(RuntimeError, match="closed"):
        store.issue(
            owner_id="owner-a",
            role="semantic-code-navigator",
            session_id="session-a",
            value="new-state",
        )


def test_launch_binding_uses_shared_principal_and_canonical_server_identity(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "project"
    cwd = project_dir / "worktree"
    authority_home = tmp_path / "generated-session"
    authority_home.mkdir()
    service = _snapshot_service()
    store: OwnerBoundExplorationContextStore[object] = OwnerBoundExplorationContextStore(
        trusted_root=project_dir,
        service=service,
    )

    binding = store.bind_launch(
        owner_id="uid:1000",
        role="semantic-code-navigator",
        session_id="session-a",
        cwd=cwd,
        repository_root=project_dir,
        source_identity="bundled:semantic-code-navigator:definition-digest",
        authority_home=authority_home,
    )

    assert binding.provider_extras() == {
        EXPLORATION_CAPABILITY_ENV: binding.capability,
        EXPLORATION_ROLE_ENV: EXPLORATION_PRINCIPAL_ROLE,
        EXPLORATION_SESSION_ENV: "session-a",
        EXPLORATION_AUTHORITY_PATH_ENV: str(binding.authority_path),
    }
    assert binding.authority_path.parent == authority_home
    assert binding.authority_path.stat().st_mode & 0o777 == 0o600
    assert binding.capability not in binding.authority_path.read_text(encoding="utf-8")
    assert store._leases[binding.capability].cwd == cwd.resolve()
    assert store._leases[binding.capability].repository_root == project_dir.resolve()
    service.capture_snapshot.assert_called_once_with(project_dir.resolve())
    assert (
        store.resolve(
            capability=binding.capability,
            owner_id="uid:1000",
            role="repository-impact-profiler",
            session_id="session-a",
        ).status
        is CapabilityResolutionStatus.OK
    )


def test_both_roles_receive_byte_identical_signed_principal(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "project"
    authority_home = tmp_path / "generated-session"
    authority_home.mkdir()
    store: OwnerBoundExplorationContextStore[object] = OwnerBoundExplorationContextStore(
        trusted_root=project_dir,
        service=_snapshot_service(),
    )

    bindings = store.bind_launches(
        owner_id="uid:1000",
        session_id="session-a",
        cwd=project_dir / "worktree",
        repository_root=project_dir,
        source_identities={
            "repository-impact-profiler": "profiler-definition-a:parent-source",
            "semantic-code-navigator": "navigator-definition-a:parent-source",
        },
        authority_home=authority_home,
    )

    assert set(bindings) == {
        "semantic-code-navigator",
        "repository-impact-profiler",
    }
    assert bindings["semantic-code-navigator"] == bindings["repository-impact-profiler"]
    assert bindings["semantic-code-navigator"][EXPLORATION_ROLE_ENV] == EXPLORATION_PRINCIPAL_ROLE
    payload = json.loads(
        Path(bindings["semantic-code-navigator"][EXPLORATION_AUTHORITY_PATH_ENV]).read_text(
            encoding="utf-8"
        )
    )
    assert set(payload) == {"principal", "schema_version", "signature"}
    assert len(payload["signature"]) == 64
    assert isinstance(payload["principal"]["expires_at"], int)
    assert len(payload["principal"]["generation"]) == 32
    assert payload["principal"]["source_identity"].startswith("sha256:")
    capability = bindings["semantic-code-navigator"][EXPLORATION_CAPABILITY_ENV]
    assert payload["principal"]["snapshot_digest"] == store._leases[capability].snapshot_digest
    assert "roles" not in payload


def test_shared_source_identity_is_order_stable_and_binds_both_definitions(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "project"
    store: OwnerBoundExplorationContextStore[object] = OwnerBoundExplorationContextStore(
        trusted_root=project_dir,
        service=_snapshot_service(),
    )
    base = {
        "semantic-code-navigator": "navigator-a",
        "repository-impact-profiler": "profiler-a",
    }

    first_home = tmp_path / "first"
    first_home.mkdir()
    first = store.bind_launches(
        owner_id="uid:1000",
        session_id="first",
        cwd=project_dir,
        repository_root=project_dir,
        source_identities=base,
        authority_home=first_home,
    )
    first_capability = first["semantic-code-navigator"][EXPLORATION_CAPABILITY_ENV]
    first_identity = store._leases[first_capability].source_identity

    reordered_home = tmp_path / "reordered"
    reordered_home.mkdir()
    reordered = store.bind_launches(
        owner_id="uid:1000",
        session_id="reordered",
        cwd=project_dir,
        repository_root=project_dir,
        source_identities=dict(reversed(tuple(base.items()))),
        authority_home=reordered_home,
    )
    reordered_capability = reordered["semantic-code-navigator"][EXPLORATION_CAPABILITY_ENV]
    assert store._leases[reordered_capability].source_identity == first_identity

    changed_home = tmp_path / "changed"
    changed_home.mkdir()
    changed = store.bind_launches(
        owner_id="uid:1000",
        session_id="changed",
        cwd=project_dir,
        repository_root=project_dir,
        source_identities={**base, "repository-impact-profiler": "profiler-b"},
        authority_home=changed_home,
    )
    changed_capability = changed["semantic-code-navigator"][EXPLORATION_CAPABILITY_ENV]
    assert store._leases[changed_capability].source_identity != first_identity


def test_launch_replacement_and_cleanup_invalidate_capabilities_atomically(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "project"
    authority_home = tmp_path / "generated-session"
    authority_home.mkdir()
    store: OwnerBoundExplorationContextStore[object] = OwnerBoundExplorationContextStore(
        trusted_root=project_dir,
        service=_snapshot_service(),
    )
    kwargs = {
        "owner_id": "uid:1000",
        "role": "semantic-code-navigator",
        "session_id": "session-a",
        "cwd": project_dir,
        "repository_root": project_dir,
        "source_identity": "bundled:semantic-code-navigator:definition-digest",
        "authority_home": authority_home,
    }

    first = store.bind_launch(**kwargs)
    second = store.bind_launch(**kwargs)

    assert (
        store.resolve(
            capability=first.capability,
            owner_id="uid:1000",
            role="semantic-code-navigator",
            session_id="session-a",
        ).status
        is CapabilityResolutionStatus.INVALID
    )
    store.cleanup_session("session-a")
    assert (
        store.resolve(
            capability=second.capability,
            owner_id="uid:1000",
            role="semantic-code-navigator",
            session_id="session-a",
        ).status
        is CapabilityResolutionStatus.INVALID
    )


def test_launch_replacement_removes_authority_from_prior_home(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    first_home = tmp_path / "first-session"
    second_home = tmp_path / "second-session"
    first_home.mkdir()
    second_home.mkdir()
    store: OwnerBoundExplorationContextStore[object] = OwnerBoundExplorationContextStore(
        trusted_root=project_dir,
        service=_snapshot_service(),
    )
    kwargs = {
        "owner_id": "uid:1000",
        "role": "semantic-code-navigator",
        "session_id": "session-a",
        "cwd": project_dir,
        "repository_root": project_dir,
        "source_identity": "bundled:semantic-code-navigator:definition-digest",
    }

    first = store.bind_launch(**kwargs, authority_home=first_home)
    second = store.bind_launch(**kwargs, authority_home=second_home)

    assert not first.authority_path.exists()
    assert second.authority_path.exists()


def test_discarding_last_session_capability_removes_authority(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    authority_home = tmp_path / "generated-session"
    authority_home.mkdir()
    store: OwnerBoundExplorationContextStore[object] = OwnerBoundExplorationContextStore(
        trusted_root=project_dir,
        service=_snapshot_service(),
    )
    binding = store.bind_launch(
        owner_id="uid:1000",
        role="semantic-code-navigator",
        session_id="session-a",
        cwd=project_dir,
        repository_root=project_dir,
        source_identity="bundled:semantic-code-navigator:definition-digest",
        authority_home=authority_home,
    )

    store.discard(binding.capability)

    assert not binding.authority_path.exists()


def test_launch_rejects_untrusted_repository_root(tmp_path: Path) -> None:
    store: OwnerBoundExplorationContextStore[object] = OwnerBoundExplorationContextStore(
        trusted_root=tmp_path / "project",
    )

    with pytest.raises(ValueError, match="trusted project root"):
        store.bind_launch(
            owner_id="uid:1000",
            role="semantic-code-navigator",
            session_id="session-a",
            cwd=tmp_path / "worktree",
            repository_root=tmp_path / "untrusted-project",
            source_identity="bundled:semantic-code-navigator:definition-digest",
            authority_home=tmp_path,
        )


def test_cleanup_session_revokes_launch_capability(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    authority_home = tmp_path / "generated-session"
    authority_home.mkdir()
    store: OwnerBoundExplorationContextStore[object] = OwnerBoundExplorationContextStore(
        trusted_root=project_dir,
        service=_snapshot_service(),
    )
    binding = store.bind_launch(
        owner_id="uid:1000",
        role="semantic-code-navigator",
        session_id="session-a",
        cwd=project_dir,
        repository_root=project_dir,
        source_identity="bundled:semantic-code-navigator:definition-digest",
        authority_home=authority_home,
    )

    store.cleanup_session("session-a")

    assert (
        store.resolve(
            capability=binding.capability,
            owner_id="uid:1000",
            role="semantic-code-navigator",
            session_id="session-a",
        ).status
        is CapabilityResolutionStatus.INVALID
    )
    assert not binding.authority_path.exists()


def test_child_process_reopens_only_current_verifier_matched_authority(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "project"
    cwd = project_dir / "worktree"
    authority_home = tmp_path / "generated-session"
    authority_home.mkdir()
    cwd.mkdir(parents=True)
    parent: OwnerBoundExplorationContextStore[object] = OwnerBoundExplorationContextStore(
        trusted_root=project_dir,
        service=_snapshot_service(),
    )
    binding = parent.bind_launch(
        owner_id="uid:1000",
        role="semantic-code-navigator",
        session_id="session-a",
        cwd=cwd,
        repository_root=project_dir,
        source_identity="bundled:semantic-code-navigator:definition-digest",
        authority_home=authority_home,
    )
    monkeypatch.setenv(EXPLORATION_CAPABILITY_ENV, binding.capability)
    monkeypatch.setenv(EXPLORATION_ROLE_ENV, binding.role)
    monkeypatch.setenv(EXPLORATION_SESSION_ENV, binding.session_id)
    monkeypatch.setenv(EXPLORATION_AUTHORITY_PATH_ENV, str(binding.authority_path))
    monkeypatch.chdir(cwd)

    child: OwnerBoundExplorationContextStore[object] = OwnerBoundExplorationContextStore(
        trusted_root=project_dir,
    )
    reopened = child._reopen_launch_environment()

    assert reopened is not None
    assert child._leases[binding.capability].cwd == cwd.resolve()

    parent.bind_launch(
        owner_id="uid:1000",
        role="semantic-code-navigator",
        session_id="session-a",
        cwd=cwd,
        repository_root=project_dir,
        source_identity="bundled:semantic-code-navigator:definition-digest",
        authority_home=authority_home,
    )

    assert child._reopen_launch_environment() is None


def test_launch_authority_denies_a_different_process_cwd(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "project"
    execution_cwd = tmp_path / "sterile-agent-cwd"
    wrong_cwd = tmp_path / "wrong-cwd"
    authority_home = tmp_path / "generated-session"
    for path in (execution_cwd, wrong_cwd, authority_home):
        path.mkdir(parents=True)
    parent: OwnerBoundExplorationContextStore[object] = OwnerBoundExplorationContextStore(
        trusted_root=project_dir,
        service=_snapshot_service(),
    )
    binding = parent.bind_launch(
        owner_id="uid:1000",
        role="semantic-code-navigator",
        session_id="session-a",
        cwd=execution_cwd,
        repository_root=project_dir,
        source_identity="bundled:definition-digest",
        authority_home=authority_home,
    )
    for key, value in binding.provider_extras().items():
        monkeypatch.setenv(key, value)
    child: OwnerBoundExplorationContextStore[object] = OwnerBoundExplorationContextStore(
        trusted_root=project_dir,
    )

    monkeypatch.chdir(wrong_cwd)
    assert child.validate_launch_environment() is False

    monkeypatch.chdir(execution_cwd)
    assert child.validate_launch_environment() is True


def test_submit_failure_is_fail_closed_and_logs_only_bounded_safe_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "project"
    execution_cwd = tmp_path / "sterile-agent-cwd"
    authority_home = tmp_path / "generated-session"
    for path in (project_dir, execution_cwd, authority_home):
        path.mkdir()
    service = _snapshot_service()
    store: OwnerBoundExplorationContextStore[object] = OwnerBoundExplorationContextStore(
        trusted_root=project_dir,
        service=service,
    )
    binding = store.bind_launch(
        owner_id="uid:1000",
        role="semantic-code-navigator",
        session_id="session-a",
        cwd=execution_cwd,
        repository_root=project_dir,
        source_identity="bundled:definition-digest",
        authority_home=authority_home,
    )
    for key, value in binding.provider_extras().items():
        monkeypatch.setenv(key, value)
    monkeypatch.chdir(execution_cwd)
    failure_reason = (
        f"collector rejected capability {binding.capability} at {binding.authority_path}: "
        + "x" * 1_024
    )
    service.collect.side_effect = ValueError(failure_reason)
    logger = MagicMock()
    monkeypatch.setattr(exploration_context_module, "logger", logger)

    status, page = store.submit_from_launch_environment(
        query=ExplorationQuerySpec("needle"),
        page_size=10,
    )

    assert status is CapabilityResolutionStatus.INVALID
    assert page is None
    logger.warning.assert_called_once()
    (event,) = logger.warning.call_args.args
    fields = logger.warning.call_args.kwargs
    assert event == "exploration_submit_failed"
    assert fields["exception_type"] == "ValueError"
    assert len(fields["reason"]) < 600
    assert binding.capability not in fields["reason"]
    assert str(binding.authority_path) not in fields["reason"]
    assert set(fields) == {"exception_type", "reason"}


@pytest.mark.parametrize("snapshot_field", ("tampered", "missing"))
def test_tampered_or_missing_signed_snapshot_binding_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    snapshot_field: str,
) -> None:
    project_dir = tmp_path / "project"
    execution_cwd = tmp_path / "sterile-agent-cwd"
    authority_home = tmp_path / "generated-session"
    authority_home.mkdir()
    execution_cwd.mkdir()
    parent: OwnerBoundExplorationContextStore[object] = OwnerBoundExplorationContextStore(
        trusted_root=project_dir,
        service=_snapshot_service(),
    )
    binding = parent.bind_launch(
        owner_id="uid:1000",
        role="semantic-code-navigator",
        session_id="session-a",
        cwd=execution_cwd,
        repository_root=project_dir,
        source_identity="bundled:definition-digest",
        authority_home=authority_home,
    )
    for key, value in binding.provider_extras().items():
        monkeypatch.setenv(key, value)
    payload = json.loads(binding.authority_path.read_text(encoding="utf-8"))
    if snapshot_field == "tampered":
        payload["principal"]["snapshot_digest"] = "0" * 64
    else:
        del payload["principal"]["snapshot_digest"]
    binding.authority_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.chdir(execution_cwd)

    child: OwnerBoundExplorationContextStore[object] = OwnerBoundExplorationContextStore(
        trusted_root=project_dir,
    )

    assert child.validate_launch_environment() is False
