"""T9/T10/T48: explorer binding eligibility and session-scoped authority."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from autoskillit.core import RepositoryIdentity, RepositorySnapshot
from autoskillit.pipeline.exploration_context import (
    OwnerBoundExplorationContextStore,
    is_explorer_binding_eligible,
)

pytestmark = [pytest.mark.layer("pipeline"), pytest.mark.small]


class TestExplorerBindingEligibility:
    """T9: is_explorer_binding_eligible pure predicate tests."""

    def test_terminal_codex_eligible_with_read_only(self) -> None:
        assert is_explorer_binding_eligible(
            has_identity=True,
            has_backend=True,
            terminal_explorer_capable=True,
            session_scoped_explorer_capable=False,
            parent_sandbox_mode="read-only",
        )

    def test_session_scoped_claude_eligible_with_read_only(self) -> None:
        assert is_explorer_binding_eligible(
            has_identity=True,
            has_backend=True,
            terminal_explorer_capable=False,
            session_scoped_explorer_capable=True,
            parent_sandbox_mode="read-only",
        )

    def test_workspace_write_not_eligible(self) -> None:
        assert not is_explorer_binding_eligible(
            has_identity=True,
            has_backend=True,
            terminal_explorer_capable=True,
            session_scoped_explorer_capable=False,
            parent_sandbox_mode="workspace-write",
        )

    def test_no_identity_not_eligible(self) -> None:
        assert not is_explorer_binding_eligible(
            has_identity=False,
            has_backend=True,
            terminal_explorer_capable=True,
            session_scoped_explorer_capable=False,
            parent_sandbox_mode="read-only",
        )

    def test_no_backend_not_eligible(self) -> None:
        assert not is_explorer_binding_eligible(
            has_identity=True,
            has_backend=False,
            terminal_explorer_capable=True,
            session_scoped_explorer_capable=False,
            parent_sandbox_mode="read-only",
        )

    @pytest.mark.parametrize("session_type_name", ["ORCHESTRATOR", "FLEET"])
    def test_orchestrator_fleet_never_eligible(self, session_type_name: str) -> None:
        assert not is_explorer_binding_eligible(
            has_identity=True,
            has_backend=True,
            terminal_explorer_capable=True,
            session_scoped_explorer_capable=True,
            parent_sandbox_mode="read-only",
            session_type_name=session_type_name,
        )

    def test_skill_session_type_eligible(self) -> None:
        assert is_explorer_binding_eligible(
            has_identity=True,
            has_backend=True,
            terminal_explorer_capable=True,
            session_scoped_explorer_capable=False,
            parent_sandbox_mode="read-only",
            session_type_name="SKILL",
        )

    def test_no_capabilities_not_eligible(self) -> None:
        assert not is_explorer_binding_eligible(
            has_identity=True,
            has_backend=True,
            terminal_explorer_capable=False,
            session_scoped_explorer_capable=False,
            parent_sandbox_mode="read-only",
        )


def _snapshot_service() -> MagicMock:
    service = MagicMock()
    service.capture_snapshot.side_effect = lambda root: RepositorySnapshot(
        RepositoryIdentity("test-repo", "test-rev", worktree_path=str(root.resolve())),
        tree_digest="test-tree",
        collector_manifest_digest="test-manifest",
    )
    return service


class TestSessionScopedAuthority:
    """T10: bind_session_scoped creates a capability, session_scoped_capability finds it."""

    def test_bind_session_scoped_returns_capability(self, tmp_path: Path) -> None:
        store: OwnerBoundExplorationContextStore[object] = OwnerBoundExplorationContextStore(
            trusted_root=tmp_path,
            service=_snapshot_service(),
        )
        capability = store.bind_session_scoped(
            owner_id="uid:1000",
            session_id="test-session",
            cwd=tmp_path,
            repository_root=tmp_path,
            source_identity="interactive:test-session",
        )
        assert capability.startswith("explore_")

    def test_session_scoped_capability_finds_active(self, tmp_path: Path) -> None:
        store: OwnerBoundExplorationContextStore[object] = OwnerBoundExplorationContextStore(
            trusted_root=tmp_path,
            service=_snapshot_service(),
        )
        capability = store.bind_session_scoped(
            owner_id="uid:1000",
            session_id="test-session",
            cwd=tmp_path,
            repository_root=tmp_path,
            source_identity="interactive:test-session",
        )
        found = store.session_scoped_capability("test-session")
        assert found == capability

    def test_session_scoped_capability_returns_none_without_bind(self, tmp_path: Path) -> None:
        store: OwnerBoundExplorationContextStore[object] = OwnerBoundExplorationContextStore(
            trusted_root=tmp_path,
            service=_snapshot_service(),
        )
        assert store.session_scoped_capability("nonexistent-session") is None

    def test_session_scoped_capability_returns_none_after_close(self, tmp_path: Path) -> None:
        store: OwnerBoundExplorationContextStore[object] = OwnerBoundExplorationContextStore(
            trusted_root=tmp_path,
            service=_snapshot_service(),
        )
        store.bind_session_scoped(
            owner_id="uid:1000",
            session_id="test-session",
            cwd=tmp_path,
            repository_root=tmp_path,
            source_identity="interactive:test-session",
        )
        store.close()
        assert store.session_scoped_capability("test-session") is None

    def test_wrong_repository_root_raises(self, tmp_path: Path) -> None:
        store: OwnerBoundExplorationContextStore[object] = OwnerBoundExplorationContextStore(
            trusted_root=tmp_path / "real-root",
            service=_snapshot_service(),
        )
        (tmp_path / "real-root").mkdir()
        with pytest.raises(ValueError, match="does not match the trusted project root"):
            store.bind_session_scoped(
                owner_id="uid:1000",
                session_id="test-session",
                cwd=tmp_path,
                repository_root=tmp_path / "wrong-root",
                source_identity="interactive:test-session",
            )
