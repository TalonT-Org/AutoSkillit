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
    clone_repo,
    detect_source_dir,
    merge_feature_branch,
    push_to_remote,
    remove_clone,
)
from autoskillit.workspace.skills import SkillResolver, bundled_skills_dir

delete_directory_contents = _delete_directory_contents

__all__ = [
    "CleanupResult",
    "_delete_directory_contents",
    "delete_directory_contents",
    "DefaultCloneManager",
    "DefaultWorkspaceManager",
    "SkillResolver",
    "bundled_skills_dir",
    "clone_repo",
    "detect_source_dir",
    "merge_feature_branch",
    "push_to_remote",
    "remove_clone",
]
