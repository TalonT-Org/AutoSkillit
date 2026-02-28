"""workspace/ L1 package: directory cleanup and skill resolution.

Re-exports the full public surface of cleanup.py and skills.py.
Both sub-modules depend only on autoskillit.core.*.
"""

from autoskillit.workspace.cleanup import (
    CleanupResult,
    DefaultWorkspaceManager,
    _delete_directory_contents,
)
from autoskillit.workspace.skills import SkillResolver, bundled_skills_dir

delete_directory_contents = _delete_directory_contents

__all__ = [
    "CleanupResult",
    "_delete_directory_contents",
    "delete_directory_contents",
    "DefaultWorkspaceManager",
    "SkillResolver",
    "bundled_skills_dir",
]
