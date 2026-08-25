"""Real-condition failure coverage for OwnerBoundExplorationContextStore.bind_session_scoped.

Upgrades the injection-shaped matrix (tests/server/test_enable_exploration_failure_codes.py,
which mocks bind_session_scoped itself) with tests that drive the named exceptions from the
real store's own logic — no MagicMock(side_effect=...) on bind_session_scoped. TrustedRootMismatch
already has a real-condition test (tests/pipeline/test_explorer_eligibility.py::
test_wrong_repository_root_raises) and stale/truncated real-condition coverage is #4756's T-C6;
both are left untouched.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import RepositoryIdentity, RepositorySnapshot
from autoskillit.pipeline.exploration_context import OwnerBoundExplorationContextStore

pytestmark = [pytest.mark.layer("pipeline"), pytest.mark.small]


def _snapshot_service():
    from unittest.mock import MagicMock

    service = MagicMock()
    service.capture_snapshot.side_effect = lambda root: RepositorySnapshot(
        RepositoryIdentity("test-repository", "test-revision", worktree_path=str(root.resolve())),
        tree_digest="test-tree",
        collector_manifest_digest="test-manifest",
    )
    return service


def test_service_not_configured_raises_from_the_real_store(tmp_path: Path) -> None:
    """service=None is a real store constructor argument, not an injected mock."""
    store: OwnerBoundExplorationContextStore[object] = OwnerBoundExplorationContextStore(
        trusted_root=tmp_path, service=None
    )

    with pytest.raises(OwnerBoundExplorationContextStore.ServiceNotConfigured):
        store.bind_session_scoped(
            owner_id="uid:test",
            session_id="session-a",
            cwd=tmp_path,
            repository_root=tmp_path,
            source_identity="interactive:session-a",
        )


def test_store_closed_raises_from_the_real_store(tmp_path: Path) -> None:
    """close() is real store state, not an injected mock."""
    store: OwnerBoundExplorationContextStore[object] = OwnerBoundExplorationContextStore(
        trusted_root=tmp_path, service=_snapshot_service()
    )
    store.close()

    with pytest.raises(OwnerBoundExplorationContextStore.StoreClosed):
        store.bind_session_scoped(
            owner_id="uid:test",
            session_id="session-a",
            cwd=tmp_path,
            repository_root=tmp_path,
            source_identity="interactive:session-a",
        )


def test_capacity_exceeded_raises_from_the_real_store(tmp_path: Path) -> None:
    """A real store filled to its own configured max_active_leases, not a mocked capacity check."""
    store: OwnerBoundExplorationContextStore[object] = OwnerBoundExplorationContextStore(
        trusted_root=tmp_path, service=_snapshot_service(), max_active_leases=1
    )
    store.bind_session_scoped(
        owner_id="uid:test",
        session_id="session-a",
        cwd=tmp_path,
        repository_root=tmp_path,
        source_identity="interactive:session-a",
    )

    with pytest.raises(OwnerBoundExplorationContextStore.CapacityExceeded):
        store.bind_session_scoped(
            owner_id="uid:test",
            session_id="session-b",
            cwd=tmp_path,
            repository_root=tmp_path,
            source_identity="interactive:session-b",
        )


@pytest.mark.parametrize("source_identity", ["", "x" * 1025], ids=["empty", "too_long"])
def test_invalid_source_identity_raises_from_the_real_store(
    tmp_path: Path, source_identity: str
) -> None:
    """Empty and over-length source_identity through the real store's own bound check —
    upgrading #4756's injection-shaped 2.8 test to a real-condition one at the store layer."""
    store: OwnerBoundExplorationContextStore[object] = OwnerBoundExplorationContextStore(
        trusted_root=tmp_path, service=_snapshot_service()
    )

    with pytest.raises(OwnerBoundExplorationContextStore.InvalidSourceIdentity):
        store.bind_session_scoped(
            owner_id="uid:test",
            session_id="session-a",
            cwd=tmp_path,
            repository_root=tmp_path,
            source_identity=source_identity,
        )


@pytest.mark.parametrize("session_id", ["", "x" * 129], ids=["empty", "too_long"])
def test_invalid_session_binding_raises_from_the_real_store(
    tmp_path: Path, session_id: str
) -> None:
    """The _validate_binding promotion (Step 3): an invalid session_id surfaces as its own
    named exception, not a bare ValueError indistinguishable from any other bug."""
    store: OwnerBoundExplorationContextStore[object] = OwnerBoundExplorationContextStore(
        trusted_root=tmp_path, service=_snapshot_service()
    )

    with pytest.raises(
        OwnerBoundExplorationContextStore.InvalidSessionBinding, match="session_id"
    ):
        store.bind_session_scoped(
            owner_id="uid:test",
            session_id=session_id,
            cwd=tmp_path,
            repository_root=tmp_path,
            source_identity="interactive:session-a",
        )


def test_invalid_session_binding_promotion_leaves_the_other_five_call_sites_unchanged(
    tmp_path: Path,
) -> None:
    """Blast-radius regression: _validate_binding backs issue, bind_launch,
    _bind_launches (via bind_launches), bind_session_scoped, resolve, and
    cleanup_session. InvalidSessionBinding is pinned to ValueError, so every
    pre-existing `except ValueError`/`pytest.raises(ValueError)` consumer at
    these other five call sites remains behavior-compatible after the
    promotion from a bare ValueError."""
    store: OwnerBoundExplorationContextStore[object] = OwnerBoundExplorationContextStore(
        trusted_root=tmp_path, service=_snapshot_service()
    )
    authority_home = tmp_path / "authority"
    authority_home.mkdir()

    with pytest.raises(OwnerBoundExplorationContextStore.InvalidSessionBinding):
        store.issue(
            owner_id="uid:test",
            role="semantic-code-navigator",
            session_id="",
            value="v",
            origin="session",
        )
    assert issubclass(OwnerBoundExplorationContextStore.InvalidSessionBinding, ValueError)

    with pytest.raises(OwnerBoundExplorationContextStore.InvalidSessionBinding):
        store.bind_launch(
            owner_id="uid:test",
            role="semantic-code-navigator",
            session_id="",
            cwd=tmp_path,
            repository_root=tmp_path,
            source_identity="src",
            authority_home=authority_home,
        )

    with pytest.raises(OwnerBoundExplorationContextStore.InvalidSessionBinding):
        store.bind_launches(
            owner_id="uid:test",
            session_id="",
            cwd=tmp_path,
            repository_root=tmp_path,
            source_identities={"semantic-code-navigator": "s", "repository-impact-profiler": "s"},
            authority_home=authority_home,
        )

    with pytest.raises(OwnerBoundExplorationContextStore.InvalidSessionBinding):
        store.resolve(
            capability="explore_x",
            owner_id="uid:test",
            role="semantic-code-navigator",
            session_id="",
        )

    with pytest.raises(OwnerBoundExplorationContextStore.InvalidSessionBinding):
        store.cleanup_session("")
