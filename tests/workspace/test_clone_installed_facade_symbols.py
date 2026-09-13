"""Facade-symbol identity pins for the workspace.clone / workspace._installed decomposition.

Each `__init__.py` re-exports symbols from several private submodules. These tests pin every
public, checkable symbol to the exact submodule that actually defines it, so a future rename or
accidental re-implementation in the wrong file is caught immediately instead of drifting silently
(the same shape as tests/execution/test_process_submodules.py for the process.py decomposition).

Plain string/int constants (RUNS_DIR, WORKTREES_DIR) have no __module__ to pin and are covered
only by the facade-completeness checks below.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.small]

_EXPECTED_CLONE_SYMBOLS: frozenset[str] = frozenset(
    {
        "CleanupResult",
        "CloneRegistry",
        "CloneSourceResolution",
        "CloneStatus",
        "DefaultCloneManager",
        "DefaultWorkspaceManager",
        "RUNS_DIR",
        "WORKTREES_DIR",
        "batch_delete",
        "classify_remote_url",
        "cleanup_candidates",
        "clone_registry",
        "clone_repo",
        "create_git_worktree",
        "delete_directory_contents",
        "detect_branch",
        "detect_source_dir",
        "detect_uncommitted_changes",
        "detect_unpublished_branch",
        "list_git_worktrees",
        "push_to_remote",
        "read_registry",
        "register_clone",
        "remove_clone",
        "remove_git_worktree",
        "remove_worktree_sidecar",
        "write_worktree_sidecar",
    }
)

_EXPECTED_INSTALLED_SYMBOLS: frozenset[str] = frozenset(
    {
        "InstallStateFinding",
        "InstallStateLeaseMode",
        "InstallStateSpec",
        "InstalledArtifactVerification",
        "PublicationObligation",
        "clear_obligation",
        "marketplace_plugin_root",
        "read_obligation",
        "reconcile_install_artifacts",
        "update_obligation_expected_version",
        "verify_install_state",
        "verify_installed_plugin_artifact",
        "write_installed_plugin_artifact_manifest_locked",
        "write_obligation",
    }
)


def test_clone_facade_reexports_exactly_the_expected_symbols():
    """workspace.clone.__all__ matches the pinned symbol set exactly."""
    from autoskillit.workspace import clone

    assert hasattr(clone, "__all__")
    assert set(clone.__all__) == _EXPECTED_CLONE_SYMBOLS, (
        f"clone.__all__ mismatch.\n"
        f"  Extra   : {set(clone.__all__) - _EXPECTED_CLONE_SYMBOLS}\n"
        f"  Missing : {_EXPECTED_CLONE_SYMBOLS - set(clone.__all__)}"
    )


def test_installed_facade_reexports_exactly_the_expected_symbols():
    """workspace._installed.__all__ matches the pinned symbol set exactly."""
    from autoskillit.workspace import _installed

    assert hasattr(_installed, "__all__")
    assert set(_installed.__all__) == _EXPECTED_INSTALLED_SYMBOLS, (
        f"_installed.__all__ mismatch.\n"
        f"  Extra   : {set(_installed.__all__) - _EXPECTED_INSTALLED_SYMBOLS}\n"
        f"  Missing : {_EXPECTED_INSTALLED_SYMBOLS - set(_installed.__all__)}"
    )


def test_clone_registry_is_the_registry_submodule():
    """clone_registry is the _registry submodule itself, not a copy or reimplementation."""
    from autoskillit.workspace.clone import _registry, clone_registry

    assert clone_registry is _registry
    assert clone_registry.__name__ == "autoskillit.workspace.clone._registry"


def test_clone_cleanup_exports():
    """DefaultWorkspaceManager and delete_directory_contents live in _cleanup.

    CleanupResult is excluded: it is a pass-through re-export of autoskillit.core's
    CleanupResult, not something _cleanup.py itself defines.
    """
    from autoskillit.workspace.clone._cleanup import (
        DefaultWorkspaceManager,
        _delete_directory_contents,
    )

    assert DefaultWorkspaceManager.__module__ == "autoskillit.workspace.clone._cleanup"
    assert callable(_delete_directory_contents)
    assert _delete_directory_contents.__module__ == "autoskillit.workspace.clone._cleanup"


def test_clone_detect_exports():
    """The detect_*/classify_remote_url helpers live in _detect."""
    from autoskillit.workspace.clone._detect import (
        classify_remote_url,
        detect_branch,
        detect_source_dir,
        detect_uncommitted_changes,
        detect_unpublished_branch,
    )

    for fn in (
        classify_remote_url,
        detect_branch,
        detect_source_dir,
        detect_uncommitted_changes,
        detect_unpublished_branch,
    ):
        assert callable(fn)
        assert fn.__module__ == "autoskillit.workspace.clone._detect"


def test_clone_registry_module_exports():
    """CloneRegistry and the registry functions live in _registry.

    CloneStatus is excluded: it is a `Literal[...]` type alias, not a class or
    function, and carries no meaningful __module__ of its own to pin.
    """
    from autoskillit.workspace.clone._registry import (
        CloneRegistry,
        batch_delete,
        cleanup_candidates,
        read_registry,
        register_clone,
    )

    assert CloneRegistry.__module__ == "autoskillit.workspace.clone._registry"
    for fn in (batch_delete, cleanup_candidates, read_registry, register_clone):
        assert callable(fn)
        assert fn.__module__ == "autoskillit.workspace.clone._registry"


def test_clone_remote_exports():
    """CloneSourceResolution lives in _remote."""
    from autoskillit.workspace.clone._remote import CloneSourceResolution

    assert CloneSourceResolution.__module__ == "autoskillit.workspace.clone._remote"


def test_clone_worktree_exports():
    """The worktree lifecycle helpers live in _worktree."""
    from autoskillit.workspace.clone._worktree import (
        create_git_worktree,
        list_git_worktrees,
        remove_git_worktree,
        remove_worktree_sidecar,
        write_worktree_sidecar,
    )

    for fn in (
        create_git_worktree,
        list_git_worktrees,
        remove_git_worktree,
        remove_worktree_sidecar,
        write_worktree_sidecar,
    ):
        assert callable(fn)
        assert fn.__module__ == "autoskillit.workspace.clone._worktree"


def test_clone_facade_local_definitions():
    """clone_repo, remove_clone, push_to_remote, and DefaultCloneManager are defined directly in
    clone/__init__.py itself (not re-exported from a private submodule) -- tracked separately by
    tests/arch/test_ast_rules.py::test_init_files_are_pure_facades, not papered over here."""
    from autoskillit.workspace.clone import (
        DefaultCloneManager,
        clone_repo,
        push_to_remote,
        remove_clone,
    )

    for obj in (clone_repo, remove_clone, push_to_remote, DefaultCloneManager):
        assert obj.__module__ == "autoskillit.workspace.clone"


def test_installed_artifact_exports():
    """Install-state/artifact-verification symbols live in _artifact."""
    from autoskillit.workspace._installed._artifact import (
        InstalledArtifactVerification,
        InstallStateFinding,
        InstallStateLeaseMode,
        InstallStateSpec,
        verify_installed_plugin_artifact,
        write_installed_plugin_artifact_manifest_locked,
    )

    for cls in (
        InstalledArtifactVerification,
        InstallStateFinding,
        InstallStateLeaseMode,
        InstallStateSpec,
    ):
        assert cls.__module__ == "autoskillit.workspace._installed._artifact"
    for fn in (verify_installed_plugin_artifact, write_installed_plugin_artifact_manifest_locked):
        assert callable(fn)
        assert fn.__module__ == "autoskillit.workspace._installed._artifact"


def test_installed_state_exports():
    """marketplace_plugin_root, reconcile_install_artifacts, verify_install_state live in
    _state."""
    from autoskillit.workspace._installed._state import (
        marketplace_plugin_root,
        reconcile_install_artifacts,
        verify_install_state,
    )

    for fn in (marketplace_plugin_root, reconcile_install_artifacts, verify_install_state):
        assert callable(fn)
        assert fn.__module__ == "autoskillit.workspace._installed._state"


def test_installed_update_obligation_exports():
    """PublicationObligation and the obligation functions live in _update_obligation."""
    from autoskillit.workspace._installed._update_obligation import (
        PublicationObligation,
        clear_obligation,
        read_obligation,
        update_obligation_expected_version,
        write_obligation,
    )

    assert (
        PublicationObligation.__module__ == "autoskillit.workspace._installed._update_obligation"
    )
    for fn in (
        clear_obligation,
        read_obligation,
        update_obligation_expected_version,
        write_obligation,
    ):
        assert callable(fn)
        assert fn.__module__ == "autoskillit.workspace._installed._update_obligation"
