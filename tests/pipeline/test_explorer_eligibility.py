"""T9/T10/T48: explorer binding eligibility and session-scoped authority."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from autoskillit.core import (
    CompletenessReport,
    EvidencePage,
    ExplorationQuerySpec,
    RepositorySnapshot,
    SessionType,
)
from autoskillit.pipeline.exploration_context import (
    ExplorationContext,
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

    @pytest.mark.parametrize("session_type", [SessionType.ORCHESTRATOR, SessionType.FLEET])
    def test_orchestrator_fleet_never_eligible(self, session_type: SessionType) -> None:
        assert not is_explorer_binding_eligible(
            has_identity=True,
            has_backend=True,
            terminal_explorer_capable=True,
            session_scoped_explorer_capable=True,
            parent_sandbox_mode="read-only",
            session_type=session_type,
        )

    def test_skill_session_type_eligible(self) -> None:
        assert is_explorer_binding_eligible(
            has_identity=True,
            has_backend=True,
            terminal_explorer_capable=True,
            session_scoped_explorer_capable=False,
            parent_sandbox_mode="read-only",
            session_type=SessionType.SKILL,
        )

    def test_no_capabilities_not_eligible(self) -> None:
        assert not is_explorer_binding_eligible(
            has_identity=True,
            has_backend=True,
            terminal_explorer_capable=False,
            session_scoped_explorer_capable=False,
            parent_sandbox_mode="read-only",
        )


class TestSessionScopedAuthority:
    """T10: bind_session_scoped creates a capability, session_scoped_capability finds it."""

    def test_bind_session_scoped_returns_capability(
        self, tmp_path: Path, exploration_snapshot_service: MagicMock
    ) -> None:
        store: OwnerBoundExplorationContextStore[object] = OwnerBoundExplorationContextStore(
            trusted_root=tmp_path,
            service=exploration_snapshot_service,
        )
        capability = store.bind_session_scoped(
            owner_id="uid:1000",
            session_id="test-session",
            cwd=tmp_path,
            repository_root=tmp_path,
            source_identity="interactive:test-session",
        )
        assert capability.startswith("explore_")

    def test_session_scoped_capability_finds_active(
        self, tmp_path: Path, exploration_snapshot_service: MagicMock
    ) -> None:
        store: OwnerBoundExplorationContextStore[object] = OwnerBoundExplorationContextStore(
            trusted_root=tmp_path,
            service=exploration_snapshot_service,
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

    def test_session_scoped_capability_preserves_issuance_snapshot(
        self, tmp_path: Path, exploration_snapshot_service: MagicMock
    ) -> None:
        store: OwnerBoundExplorationContextStore[object] = OwnerBoundExplorationContextStore(
            trusted_root=tmp_path,
            service=exploration_snapshot_service,
        )
        capability = store.bind_session_scoped(
            owner_id="uid:1000",
            session_id="test-session",
            cwd=tmp_path,
            repository_root=tmp_path,
            source_identity="interactive:test-session",
        )
        query = ExplorationQuerySpec(query="needle", max_results=1)
        issuance_snapshot = exploration_snapshot_service.capture_snapshot(tmp_path)
        changed_snapshot = RepositorySnapshot(
            identity=issuance_snapshot.identity,
            tree_digest="changed-tree",
            collector_manifest_digest=issuance_snapshot.collector_manifest_digest,
        )
        changed_context = ExplorationContext(
            query=query,
            snapshot=changed_snapshot,
            evidence=(),
            completeness=CompletenessReport((), (), True),
        )
        page = EvidencePage(
            evidence=(),
            result_digest="result",
            completeness=CompletenessReport((), (), True),
        )
        exploration_snapshot_service.collect.return_value = changed_context
        exploration_snapshot_service.page.return_value = page

        with pytest.raises(ValueError, match="snapshot changed"):
            store.submit_for_capability(
                capability=capability,
                query=query,
                page_size=1,
            )

        matching_context = ExplorationContext(
            query=query,
            snapshot=issuance_snapshot,
            evidence=(),
            completeness=CompletenessReport((), (), True),
        )
        exploration_snapshot_service.collect.return_value = matching_context
        replacement, result = store.submit_for_capability(
            capability=capability,
            query=query,
            page_size=1,
        )

        assert replacement == capability
        assert result is page

    def test_session_scoped_capability_returns_none_without_bind(
        self, tmp_path: Path, exploration_snapshot_service: MagicMock
    ) -> None:
        store: OwnerBoundExplorationContextStore[object] = OwnerBoundExplorationContextStore(
            trusted_root=tmp_path,
            service=exploration_snapshot_service,
        )
        assert store.session_scoped_capability("nonexistent-session") is None

    def test_session_scoped_capability_returns_none_after_close(
        self, tmp_path: Path, exploration_snapshot_service: MagicMock
    ) -> None:
        store: OwnerBoundExplorationContextStore[object] = OwnerBoundExplorationContextStore(
            trusted_root=tmp_path,
            service=exploration_snapshot_service,
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

    def test_wrong_repository_root_raises(
        self, tmp_path: Path, exploration_snapshot_service: MagicMock
    ) -> None:
        store: OwnerBoundExplorationContextStore[object] = OwnerBoundExplorationContextStore(
            trusted_root=tmp_path / "real-root",
            service=exploration_snapshot_service,
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
