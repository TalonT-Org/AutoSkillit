"""Per-session ephemeral skill directory management — facade.

Re-exports the retained surface (see ``__all__``) from the
``session_skill_*`` shards. Roughly twenty pre-refactor module-level names
are deliberately not re-exported — they have no external importer and stay
reachable only through their owning shard.

Import direction is one-way and guarded: shards never import this facade.
"""

from __future__ import annotations

from autoskillit.workspace.session_skill_catalog import (
    CompiledSessionSkillCatalog,
    SkillUnavailableMetadata,
    compile_session_skill_catalog,
    write_skill_unavailability_metadata,
)
from autoskillit.workspace.session_skill_lifecycle import (
    resolve_persistent_session_root,
    resolve_persistent_session_roots,
)
from autoskillit.workspace.session_skill_manager import (
    DefaultSessionSkillManager,
)
from autoskillit.workspace.session_skill_materialization import (
    materialize_profile_skills,
)
from autoskillit.workspace.session_skill_provider import (
    SkillsDirectoryProvider,
    _parse_write_paths,
    default_skill_resolver,
    resolve_closure_write_dirs,
    resolve_ephemeral_root,
)
from autoskillit.workspace.skills import compute_skill_closure as compute_skill_closure

__all__ = [
    "CompiledSessionSkillCatalog",
    "DefaultSessionSkillManager",
    "SkillsDirectoryProvider",
    "SkillUnavailableMetadata",
    "compile_session_skill_catalog",
    "compute_skill_closure",
    "default_skill_resolver",
    "materialize_profile_skills",
    "resolve_closure_write_dirs",
    "resolve_ephemeral_root",
    "resolve_persistent_session_root",
    "resolve_persistent_session_roots",
    "write_skill_unavailability_metadata",
    "_parse_write_paths",
]
