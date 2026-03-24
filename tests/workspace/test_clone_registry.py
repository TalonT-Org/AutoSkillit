"""Tests for autoskillit.workspace.clone_registry module."""

from __future__ import annotations

import json
from pathlib import Path

from autoskillit.workspace.clone_registry import (
    cleanup_candidates,
    read_registry,
    register_clone,
)


class TestRegisterClone:
    """register_clone contract tests."""

    def test_register_clone_creates_registry(self, tmp_path: Path) -> None:
        """register_clone with new path creates registry file with one entry."""
        registry = str(tmp_path / "registry.json")
        result = register_clone("/tmp/clone-a", "success", registry_path=registry)

        assert result["registered"] == "true"
        assert result["registry_path"] == registry
        assert Path(registry).exists()

        data = json.loads(Path(registry).read_text())
        assert data["clones"] == [{"path": "/tmp/clone-a", "status": "success"}]

    def test_register_clone_appends_to_existing(self, tmp_path: Path) -> None:
        """Second register_clone appends without overwriting first entry."""
        registry = str(tmp_path / "registry.json")
        register_clone("/tmp/clone-a", "success", registry_path=registry)
        register_clone("/tmp/clone-b", "error", registry_path=registry)

        data = json.loads(Path(registry).read_text())
        assert len(data["clones"]) == 2
        assert data["clones"][0] == {"path": "/tmp/clone-a", "status": "success"}
        assert data["clones"][1] == {"path": "/tmp/clone-b", "status": "error"}

    def test_register_clone_success_and_error_statuses(self, tmp_path: Path) -> None:
        """Both 'success' and 'error' statuses are accepted and stored."""
        registry = str(tmp_path / "registry.json")
        register_clone("/tmp/ok-clone", "success", registry_path=registry)
        register_clone("/tmp/bad-clone", "error", registry_path=registry)

        entries = json.loads(Path(registry).read_text())["clones"]
        statuses = {e["path"]: e["status"] for e in entries}
        assert statuses["/tmp/ok-clone"] == "success"
        assert statuses["/tmp/bad-clone"] == "error"

    def test_register_clone_custom_path(self, tmp_path: Path) -> None:
        """registry_path parameter writes to the specified file, not the default."""
        custom = str(tmp_path / "custom-registry.json")
        default_candidate = tmp_path / ".autoskillit" / "temp" / "clone-cleanup-registry.json"

        register_clone("/tmp/clone-x", "success", registry_path=custom)

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
        register_clone("/tmp/success-1", "success", registry_path=registry)
        register_clone("/tmp/error-1", "error", registry_path=registry)
        register_clone("/tmp/success-2", "success", registry_path=registry)

        to_delete, to_preserve = cleanup_candidates(registry_path=registry)

        assert sorted(to_delete) == ["/tmp/success-1", "/tmp/success-2"]
        assert to_preserve == ["/tmp/error-1"]

    def test_cleanup_candidates_empty_registry(self, tmp_path: Path) -> None:
        """cleanup_candidates on empty/missing registry returns ([], [])."""
        registry = str(tmp_path / "no-registry.json")
        to_delete, to_preserve = cleanup_candidates(registry_path=registry)
        assert to_delete == []
        assert to_preserve == []
