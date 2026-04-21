"""workspace/ L1 package: directory cleanup, skill resolution, and clone isolation.

Re-exports the full public surface of cleanup.py, skills.py, and clone.py.
All sub-modules depend only on autoskillit.core.*.
"""

from autoskillit.core import SkillResolver
from autoskillit.workspace.cleanup import (
    CleanupResult,
    DefaultWorkspaceManager,
    _delete_directory_contents,
)
from autoskillit.workspace.clone import (
    RUNS_DIR,
    DefaultCloneManager,
    classify_remote_url,
    clone_repo,
    detect_branch,
    detect_source_dir,
    detect_uncommitted_changes,
    detect_unpublished_branch,
    push_to_remote,
    remove_clone,
)
from autoskillit.workspace.clone_registry import (
    batch_delete as batch_delete,
)
from autoskillit.workspace.clone_registry import (
    cleanup_candidates as cleanup_candidates,
)
from autoskillit.workspace.clone_registry import (
    read_registry as read_registry,
)
from autoskillit.workspace.clone_registry import (
    register_clone as register_clone,
)
from autoskillit.workspace.session_skills import (
    DefaultSessionSkillManager,
    SkillsDirectoryProvider,
    resolve_ephemeral_root,
)
from autoskillit.workspace.skills import (
    DefaultSkillResolver,
    bundled_skills_dir,
    bundled_skills_extended_dir,
    detect_project_local_overrides,
)
from autoskillit.workspace.worktree import (
    WORKTREES_DIR,
    list_git_worktrees,
    remove_git_worktree,
    remove_worktree_sidecar,
)

delete_directory_contents = _delete_directory_contents

__all__ = [
    "batch_delete",
    "CleanupResult",
    "cleanup_candidates",
    "delete_directory_contents",
    "classify_remote_url",
    "DefaultCloneManager",
    "DefaultWorkspaceManager",
    "DefaultSessionSkillManager",
    "list_git_worktrees",
    "read_registry",
    "register_clone",
    "remove_git_worktree",
    "remove_worktree_sidecar",
    "RUNS_DIR",
    "DefaultSkillResolver",
    "SkillResolver",
    "SkillsDirectoryProvider",
    "bundled_skills_dir",
    "bundled_skills_extended_dir",
    "detect_project_local_overrides",
    "clone_repo",
    "detect_branch",
    "detect_source_dir",
    "detect_uncommitted_changes",
    "detect_unpublished_branch",
    "push_to_remote",
    "remove_clone",
    "resolve_ephemeral_root",
    "WORKTREES_DIR",
]
