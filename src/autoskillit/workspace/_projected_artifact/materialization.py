"""Identity-preserving facade over the projected-artifact shards.

This module is the compatibility surface for the decomposition of the
proposed-document, publication, and validation ownership into:

- ``autoskillit.workspace._projected_artifact._documents``
- ``autoskillit.workspace._projected_artifact._publication``
- ``autoskillit.workspace._projected_artifact._validation``

Every public symbol re-exported here is ``is``-equal to the canonical
definition in the owning shard. The decomposition preserves the prior
public surface so existing callers — including the
``workspace/_projected_artifact/__init__.py`` package facade, the
durable-writer registry, and the monkeypatch test surface — continue
to resolve unchanged.

``authority.py`` (publication + exact-incarnation validation + reader/writer
lease handoff) imports its private symbols through the canonical owners and
the public surface through this facade.
"""

from __future__ import annotations

from autoskillit.workspace._projected_artifact._documents import (
    AgentSkillDocument,
    SkillContractRecord,
    SkillProjectionContext,
    _default_base_branch,
    _direct_install_projection_context,
    project_agent_skill_document,
)
from autoskillit.workspace._projected_artifact._publication import (
    materialize_agent_skill_tree,
    materialize_sanitized_plugin_root,
    write_generated_hooks_json,
)
from autoskillit.workspace._projected_artifact._validation import (
    validate_sanitized_plugin_artifact,
)

__all__ = [
    "AgentSkillDocument",
    "SkillProjectionContext",
    "project_agent_skill_document",
    "materialize_agent_skill_tree",
    "materialize_sanitized_plugin_root",
    "validate_sanitized_plugin_artifact",
    "write_generated_hooks_json",
    "_default_base_branch",
    "_direct_install_projection_context",
    "SkillContractRecord",
]
