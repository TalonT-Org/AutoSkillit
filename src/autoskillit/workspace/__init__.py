"""workspace/ IL-1 package: directory cleanup, skill resolution, and clone isolation.

Re-exports the full public surface of cleanup.py, skills.py, and clone.py.
All sub-modules depend only on autoskillit.core.*.
"""

from autoskillit.core import SkillResolver
from autoskillit.workspace._clone_detect import (
    RUNS_DIR,
    classify_remote_url,
    detect_branch,
    detect_source_dir,
    detect_uncommitted_changes,
    detect_unpublished_branch,
)
from autoskillit.workspace._clone_remote import CloneSourceResolution
from autoskillit.workspace.cleanup import (
    CleanupResult,
    DefaultWorkspaceManager,
    _delete_directory_contents,
)
from autoskillit.workspace.clone import (
    DefaultCloneManager,
    clone_repo,
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
    collect_closure_write_paths,
    resolve_closure_write_dirs,
    resolve_ephemeral_root,
)
from autoskillit.workspace.skill_capabilities import (
    SkillCapabilityEvidence,
    SkillCapabilityValidation,
    classify_skill_capability_evidence,
    detect_skill_capabilities,
    validate_skill_capability_authenticity,
    validate_skill_capability_declarations,
)
from autoskillit.workspace.skill_format import (
    SkillFrontmatterParseError,
    SkillFrontmatterParseResult,
    parse_frontmatter_content,
    read_skill_frontmatter,
    validate_skill_frontmatter,
)
from autoskillit.workspace.skill_projection import (
    AgentSkillDocument,
    SkillProjectionContext,
    project_agent_skill_document,
)
from autoskillit.workspace.skills import (
    DefaultSkillResolver,
    EffectiveSkillCatalog,
    EffectiveSkillInvocation,
    ProjectLocalOverride,
    SkillInfo,
    bundled_skills_dir,
    bundled_skills_extended_dir,
    detect_project_local_overrides,
    override_names,
    validate_skill_tier_roles,
)
from autoskillit.workspace.worktree import (
    WORKTREES_DIR,
    list_git_worktrees,
    remove_git_worktree,
    remove_worktree_sidecar,
    write_worktree_sidecar,
)

delete_directory_contents = _delete_directory_contents

__all__ = [
    "batch_delete",
    "AgentSkillDocument",
    "CleanupResult",
    "cleanup_candidates",
    "delete_directory_contents",
    "classify_remote_url",
    "classify_skill_capability_evidence",
    "DefaultCloneManager",
    "DefaultWorkspaceManager",
    "DefaultSessionSkillManager",
    "list_git_worktrees",
    "read_registry",
    "register_clone",
    "remove_git_worktree",
    "remove_worktree_sidecar",
    "write_worktree_sidecar",
    "RUNS_DIR",
    "DefaultSkillResolver",
    "EffectiveSkillCatalog",
    "EffectiveSkillInvocation",
    "SkillResolver",
    "SkillInfo",
    "SkillCapabilityEvidence",
    "SkillCapabilityValidation",
    "SkillFrontmatterParseError",
    "SkillFrontmatterParseResult",
    "SkillProjectionContext",
    "SkillsDirectoryProvider",
    "bundled_skills_dir",
    "bundled_skills_extended_dir",
    "detect_project_local_overrides",
    "override_names",
    "ProjectLocalOverride",
    "clone_repo",
    "CloneSourceResolution",
    "collect_closure_write_paths",
    "resolve_closure_write_dirs",
    "detect_branch",
    "detect_skill_capabilities",
    "detect_source_dir",
    "detect_uncommitted_changes",
    "detect_unpublished_branch",
    "push_to_remote",
    "remove_clone",
    "resolve_ephemeral_root",
    "WORKTREES_DIR",
    "parse_frontmatter_content",
    "project_agent_skill_document",
    "read_skill_frontmatter",
    "validate_skill_tier_roles",
    "validate_skill_capability_authenticity",
    "validate_skill_capability_declarations",
    "validate_skill_frontmatter",
]
