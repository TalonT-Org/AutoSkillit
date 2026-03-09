"""workspace/ L1 package: directory cleanup, skill resolution, and clone isolation.

Re-exports the full public surface of cleanup.py, skills.py, and clone.py.
All sub-modules depend only on autoskillit.core.*.
"""

from autoskillit.workspace.cleanup import (
    CleanupResult,
    DefaultWorkspaceManager,
    _delete_directory_contents,
)
from autoskillit.workspace.clone import (
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
from autoskillit.workspace.session_skills import (
    TIER2_SKILLS,
    DefaultSessionSkillManager,
    SkillsDirectoryProvider,
    resolve_ephemeral_root,
)
from autoskillit.workspace.skills import SkillResolver, bundled_skills_dir

delete_directory_contents = _delete_directory_contents

__all__ = [
    "CleanupResult",
    "delete_directory_contents",
    "classify_remote_url",
    "DefaultCloneManager",
    "DefaultWorkspaceManager",
    "DefaultSessionSkillManager",
    "SkillResolver",
    "SkillsDirectoryProvider",
    "TIER2_SKILLS",
    "bundled_skills_dir",
    "clone_repo",
    "detect_branch",
    "detect_source_dir",
    "detect_uncommitted_changes",
    "detect_unpublished_branch",
    "push_to_remote",
    "remove_clone",
    "resolve_ephemeral_root",
]
