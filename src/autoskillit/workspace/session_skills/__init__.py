"""Per-session ephemeral skill directory management — package facade.

Identity-preserving facade over the private session-skill shards (issue
#4989 relocated the seven-file flat cluster into this package: this file
was ``workspace/session_skills.py``, and the five shards plus
``_projection`` were ``workspace/session_skill_*.py`` and
``workspace/skill_projection.py`` respectively). Re-exports the retained
surface (see ``__all__``). Every name here is ``is``-equal to its canonical
shard definition, so existing importers and ``monkeypatch.setattr`` call
sites resolve unchanged. Roughly twenty pre-refactor module-level names are
deliberately not re-exported — they have no external importer and stay
reachable only through their owning shard.

Import direction is one-way and guarded: shards never import this facade.
"""

from __future__ import annotations

from ..skills import compute_skill_closure as compute_skill_closure
from ._catalog import (
    CompiledSessionSkillCatalog,
    SkillUnavailableMetadata,
    compile_session_skill_catalog,
    write_skill_unavailability_metadata,
)
from ._lifecycle import (
    resolve_persistent_session_root,
    resolve_persistent_session_roots,
)
from ._manager import (
    DefaultSessionSkillManager,
)
from ._materialization import (
    materialize_profile_skills,
)
from ._provider import (
    SkillsDirectoryProvider,
    _parse_write_paths,
    default_skill_resolver,
    resolve_closure_write_dirs,
    resolve_ephemeral_root,
)

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
