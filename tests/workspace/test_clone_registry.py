"""Tests for autoskillit.workspace.clone_registry module."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from autoskillit.workspace.clone_registry import (
    batch_delete,
    cleanup_candidates,
    read_registry,
    register_clone,
)


class TestRegisterClone:
    """register_clone contract tests."""

    def test_register_clone_creates_registry(self, tmp_path: Path) -> None:
        """register_clone with new path creates registry file with one entry."""
        registry = str(tmp_path / "registry.json")
        result = register_clone("/tmp/clone-a", "success", "test-owner", registry_path=registry)

        assert result["registered"] == "true"
        assert result["registry_path"] == registry
        assert Path(registry).exists()

        data = json.loads(Path(registry).read_text())
        assert data["clones"] == [
            {"path": "/tmp/clone-a", "status": "success", "owner": "test-owner"}
        ]

    def test_register_clone_appends_to_existing(self, tmp_path: Path) -> None:
        """Second register_clone appends without overwriting first entry."""
        registry = str(tmp_path / "registry.json")
        register_clone("/tmp/clone-a", "success", "test-owner", registry_path=registry)
        register_clone("/tmp/clone-b", "error", "test-owner", registry_path=registry)

        data = json.loads(Path(registry).read_text())
        assert len(data["clones"]) == 2
        assert data["clones"][0] == {
            "path": "/tmp/clone-a",
            "status": "success",
            "owner": "test-owner",
        }
        assert data["clones"][1] == {
            "path": "/tmp/clone-b",
            "status": "error",
            "owner": "test-owner",
        }

    def test_register_clone_success_and_error_statuses(self, tmp_path: Path) -> None:
        """Both 'success' and 'error' statuses are accepted and stored."""
        registry = str(tmp_path / "registry.json")
        register_clone("/tmp/ok-clone", "success", "test-owner", registry_path=registry)
        register_clone("/tmp/bad-clone", "error", "test-owner", registry_path=registry)

        entries = json.loads(Path(registry).read_text())["clones"]
        statuses = {e["path"]: e["status"] for e in entries}
        assert statuses["/tmp/ok-clone"] == "success"
        assert statuses["/tmp/bad-clone"] == "error"

    def test_register_clone_custom_path(self, tmp_path: Path) -> None:
        """registry_path parameter writes to the specified file, not the default."""
        custom = str(tmp_path / "custom-registry.json")
        default_candidate = tmp_path / ".autoskillit" / "temp" / "clone-cleanup-registry.json"

        register_clone("/tmp/clone-x", "success", "test-owner", registry_path=custom)

        assert Path(custom).exists()
        assert not default_candidate.exists()


class TestReadRegistry:
    """read_registry contract tests."""

    def test_read_registry_returns_empty_for_missing_file(self, tmp_path: Path) -> None:
        """read_registry returns [] when registry file does not exist."""
        registry = str(tmp_path / "nonexistent.json")
        result = read_registry(registry_path=registry)
        assert result == []


class TestCleanupCandidates:
    """cleanup_candidates contract tests."""

    def test_cleanup_candidates_partitions_by_status(self, tmp_path: Path) -> None:
        """cleanup_candidates returns (success_paths, error_paths) correctly separated."""
        registry = str(tmp_path / "registry.json")
        register_clone("/tmp/success-1", "success", "test-owner", registry_path=registry)
        register_clone("/tmp/error-1", "error", "test-owner", registry_path=registry)
        register_clone("/tmp/success-2", "success", "test-owner", registry_path=registry)

        to_delete, to_preserve = cleanup_candidates(registry_path=registry)

        assert sorted(to_delete) == ["/tmp/success-1", "/tmp/success-2"]
        assert to_preserve == ["/tmp/error-1"]

    def test_cleanup_candidates_empty_registry(self, tmp_path: Path) -> None:
        """cleanup_candidates on empty/missing registry returns ([], [])."""
        registry = str(tmp_path / "no-registry.json")
        to_delete, to_preserve = cleanup_candidates(registry_path=registry)
        assert to_delete == []
        assert to_preserve == []


# ---------------------------------------------------------------------------
# New tests: T1–T11 (owner-scoping feature)
# ---------------------------------------------------------------------------


class TestRegisterClonePersistsOwner:
    """T1: register_clone stores the owner field in the registry entry."""

    def test_register_clone_persists_owner_field(self, tmp_path: Path) -> None:
        """T1 — register_clone writes the owner field to the registry JSON."""
        reg = str(tmp_path / "registry.json")
        register_clone("/tmp/a", "success", owner="kit-abc", registry_path=reg)

        data = json.loads(Path(reg).read_text())
        assert len(data["clones"]) == 1
        entry = data["clones"][0]
        assert set(entry.keys()) == {"path", "status", "owner"}
        assert entry["owner"] == "kit-abc"


class TestRegisterCloneRejectsEmptyOwner:
    """T2: register_clone raises ValueError when owner is empty string."""

    def test_register_clone_rejects_empty_owner(self, tmp_path: Path) -> None:
        """T2 — register_clone raises ValueError and does not write registry when owner=''."""
        reg = str(tmp_path / "registry.json")
        with pytest.raises(ValueError, match="owner is required"):
            register_clone("/tmp/a", "success", owner="", registry_path=reg)
        assert not Path(reg).exists()


class TestCleanupCandidatesOwnerFilter:
    """T3–T7: cleanup_candidates owner-scoping behaviour."""

    def test_cleanup_candidates_without_owner_returns_all_entries(self, tmp_path: Path) -> None:
        """T3 — cleanup_candidates(owner=None) returns all success entries regardless of owner."""
        reg = str(tmp_path / "registry.json")
        register_clone("/tmp/a", "success", "kit-1", registry_path=reg)
        register_clone("/tmp/b", "success", "kit-2", registry_path=reg)
        register_clone("/tmp/c", "success", "kit-3", registry_path=reg)

        to_delete, _ = cleanup_candidates(registry_path=reg, owner=None)

        assert sorted(to_delete) == ["/tmp/a", "/tmp/b", "/tmp/c"]

    def test_cleanup_candidates_with_owner_filters_to_matching_only(self, tmp_path: Path) -> None:
        """T4 — cleanup_candidates(owner='kit-1') returns only kit-1's success entry."""
        reg = str(tmp_path / "registry.json")
        register_clone("/tmp/a", "success", "kit-1", registry_path=reg)
        register_clone("/tmp/b", "success", "kit-2", registry_path=reg)

        to_delete, to_preserve = cleanup_candidates(registry_path=reg, owner="kit-1")

        assert to_delete == ["/tmp/a"]
        assert to_preserve == []

    def test_cleanup_candidates_owner_filter_ignores_other_owners_error_entries(
        self, tmp_path: Path
    ) -> None:
        """T5 — kit-1's scoped call does not see kit-2's error entry."""
        reg = str(tmp_path / "registry.json")
        register_clone("/tmp/a", "success", "kit-1", registry_path=reg)
        register_clone("/tmp/b", "error", "kit-2", registry_path=reg)

        to_delete, to_preserve = cleanup_candidates(registry_path=reg, owner="kit-1")

        assert to_delete == ["/tmp/a"]
        assert to_preserve == []

    def test_cleanup_candidates_owner_none_includes_legacy_entries(self, tmp_path: Path) -> None:
        """T6 — owner=None (all-owners mode) includes legacy entries without owner field."""
        reg_path = tmp_path / "registry.json"
        reg_path.write_text(json.dumps({"clones": [{"path": "/tmp/legacy", "status": "success"}]}))

        to_delete, _ = cleanup_candidates(registry_path=str(reg_path), owner=None)

        assert "/tmp/legacy" in to_delete

    def test_cleanup_candidates_owner_scoped_hides_legacy_entries(self, tmp_path: Path) -> None:
        """T7 — owner-scoped call does not see legacy orphan entries (no owner field)."""
        reg_path = tmp_path / "registry.json"
        reg_path.write_text(json.dumps({"clones": [{"path": "/tmp/legacy", "status": "success"}]}))

        to_delete, _ = cleanup_candidates(registry_path=str(reg_path), owner="kit-1")

        assert to_delete == []


class TestBatchDeleteOwnerFilter:
    """T8–T10: batch_delete owner-scoping behaviour."""

    def test_batch_delete_with_owner_only_invokes_remove_fn_for_matching_entries(
        self, tmp_path: Path
    ) -> None:
        """T8 — batch_delete(owner='kit-1') only removes kit-1's clone."""
        reg = str(tmp_path / "registry.json")
        register_clone("/tmp/a", "success", "kit-1", registry_path=reg)
        register_clone("/tmp/b", "success", "kit-2", registry_path=reg)

        mock_remove = MagicMock(return_value={"removed": "true"})
        result = batch_delete(reg, mock_remove, owner="kit-1")

        mock_remove.assert_called_once_with("/tmp/a", "false")
        assert result["deleted"] == ["/tmp/a"]
        assert "/tmp/b" not in result["deleted"]

    def test_batch_delete_owner_none_invokes_remove_fn_for_all_success_entries(
        self, tmp_path: Path
    ) -> None:
        """T9 — batch_delete(owner=None) removes all success entries including legacy."""
        reg_path = tmp_path / "registry.json"
        reg_path.write_text(
            json.dumps(
                {
                    "clones": [
                        {"path": "/tmp/a", "status": "success", "owner": "kit-1"},
                        {"path": "/tmp/b", "status": "success", "owner": "kit-2"},
                        {"path": "/tmp/legacy", "status": "success"},
                    ]
                }
            )
        )

        mock_remove = MagicMock(return_value={"removed": "true"})
        result = batch_delete(str(reg_path), mock_remove, owner=None)

        called_paths = [call.args[0] for call in mock_remove.call_args_list]
        assert sorted(called_paths) == ["/tmp/a", "/tmp/b", "/tmp/legacy"]
        assert sorted(result["deleted"]) == ["/tmp/a", "/tmp/b", "/tmp/legacy"]

    def test_batch_delete_preserves_other_owners_error_entries(self, tmp_path: Path) -> None:
        """T10 — kit-1's scoped batch_delete does not report kit-2's error as preserved."""
        reg = str(tmp_path / "registry.json")
        register_clone("/tmp/a", "success", "kit-1", registry_path=reg)
        register_clone("/tmp/b", "error", "kit-2", registry_path=reg)

        mock_remove = MagicMock(return_value={"removed": "true"})
        result = batch_delete(reg, mock_remove, owner="kit-1")

        assert result["preserved"] == []


class TestRegisterCloneParallelWriters:
    """T11: parallel writers with distinct owners both persist correctly."""

    def test_register_clone_parallel_writers_two_owners_interleaved(self, tmp_path: Path) -> None:
        """T11 — two sequential register_clone calls with distinct owners both persist."""
        reg = str(tmp_path / "registry.json")
        register_clone("/tmp/clone-A", "success", "owner-A", registry_path=reg)
        register_clone("/tmp/clone-B", "success", "owner-B", registry_path=reg)

        data = json.loads(Path(reg).read_text())
        entries = {e["path"]: e["owner"] for e in data["clones"]}
        assert entries["/tmp/clone-A"] == "owner-A"
        assert entries["/tmp/clone-B"] == "owner-B"


class TestBatchDeleteRegistryWriteback:
    """batch_delete must persist the pruned registry to disk after successful deletions.

    These tests verify ON-DISK STATE, not just return values — only disk verification
    can distinguish a correct write-back from a missing one.
    """

    def test_batch_delete_removes_deleted_entry_from_registry_file(self, tmp_path: Path) -> None:
        """After batch_delete, successfully-deleted entries must be absent from disk."""
        reg = str(tmp_path / "registry.json")
        register_clone("/tmp/a", "success", "kit-1", registry_path=reg)
        register_clone("/tmp/b", "error", "kit-1", registry_path=reg)

        mock_remove = MagicMock(return_value={"removed": "true"})
        result = batch_delete(reg, mock_remove, owner="kit-1")

        assert result["deleted"] == ["/tmp/a"]
        assert result["preserved"] == ["/tmp/b"]

        data = json.loads(Path(reg).read_text())
        remaining = [e["path"] for e in data["clones"]]
        assert "/tmp/a" not in remaining, (
            "batch_delete must remove successfully-deleted entries from registry"
        )
        assert "/tmp/b" in remaining  # error entries preserved on disk

    def test_batch_delete_partial_failure_keeps_only_failed_entry_on_disk(
        self, tmp_path: Path
    ) -> None:
        """Succeeded deletions are pruned; failed deletions are retained on disk."""
        reg = str(tmp_path / "registry.json")
        register_clone("/tmp/a", "success", "kit-1", registry_path=reg)
        register_clone("/tmp/b", "success", "kit-1", registry_path=reg)

        def remove_side_effect(path: str, _: str) -> dict[str, str]:
            return (
                {"removed": "true"}
                if path == "/tmp/a"
                else {"removed": "false", "reason": "not_found"}
            )

        result = batch_delete(reg, remove_side_effect, owner="kit-1")

        assert "/tmp/a" in result["deleted"]
        assert any(f["path"] == "/tmp/b" for f in result["delete_failures"])

        data = json.loads(Path(reg).read_text())
        remaining = [e["path"] for e in data["clones"]]
        assert "/tmp/a" not in remaining  # succeeded: pruned
        assert "/tmp/b" in remaining  # failed: retained

    def test_batch_delete_second_call_finds_no_candidates(self, tmp_path: Path) -> None:
        """After batch_delete prunes the registry, a second call finds nothing to delete.

        Verifies idempotency — the core regression contract for issue #756.
        """
        reg = str(tmp_path / "registry.json")
        register_clone("/tmp/a", "success", "kit-1", registry_path=reg)

        mock_remove = MagicMock(return_value={"removed": "true"})
        batch_delete(reg, mock_remove, owner="kit-1")

        mock_remove2 = MagicMock(return_value={"removed": "true"})
        result2 = batch_delete(reg, mock_remove2, owner="kit-1")

        mock_remove2.assert_not_called()
        assert result2["deleted"] == []

    def test_batch_delete_all_entries_deleted_leaves_empty_clones_list(
        self, tmp_path: Path
    ) -> None:
        """If all entries are deleted, the registry file retains an empty clones list."""
        reg = str(tmp_path / "registry.json")
        register_clone("/tmp/a", "success", "kit-1", registry_path=reg)

        mock_remove = MagicMock(return_value={"removed": "true"})
        batch_delete(reg, mock_remove, owner="kit-1")

        data = json.loads(Path(reg).read_text())
        assert data["clones"] == []

    def test_batch_delete_other_owner_entries_untouched_on_disk(self, tmp_path: Path) -> None:
        """Owner-scoped batch_delete must not touch entries owned by other kitchens."""
        reg = str(tmp_path / "registry.json")
        register_clone("/tmp/mine", "success", "kit-1", registry_path=reg)
        register_clone("/tmp/theirs", "success", "kit-2", registry_path=reg)

        mock_remove = MagicMock(return_value={"removed": "true"})
        batch_delete(reg, mock_remove, owner="kit-1")

        data = json.loads(Path(reg).read_text())
        remaining = [e["path"] for e in data["clones"]]
        assert "/tmp/mine" not in remaining  # kit-1 entry pruned
        assert "/tmp/theirs" in remaining  # kit-2 entry untouched
