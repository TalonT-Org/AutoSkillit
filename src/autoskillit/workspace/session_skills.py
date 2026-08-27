"""Per-session ephemeral skill directory management — facade.

This module is the identity-preserving compatibility facade over the
session-skill shards:

- ``autoskillit.workspace.session_skill_catalog``
- ``autoskillit.workspace.session_skill_provider``
- ``autoskillit.workspace.session_skill_lifecycle``
- ``autoskillit.workspace.session_skill_materialization``
- ``autoskillit.workspace.session_skill_manager``

Every public symbol re-exported here is ``is``-equal to the canonical
definition in the owning shard. The decomposition preserves the prior
public surface so existing callers — including the
``workspace/__init__.py`` facade, the durable-writer registry, and the
monkeypatch test surface — continue to resolve unchanged.

The acyclic import direction is enforced by the one-way-import guard:
catalog, provider, and lifecycle depend only on lower-level existing
modules; materialization depends on catalog and lower-level modules;
manager depends on catalog, provider, lifecycle, and materialization;
and this facade alone depends on all five session shards.
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
