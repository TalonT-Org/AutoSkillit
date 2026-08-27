"""Identity-preserving facade over the projected-artifact shards.

Re-exports the prior module surface from ``_documents``, ``_publication``,
and ``_validation``. Every symbol here is ``is``-equal to its canonical
shard definition, so existing importers and ``monkeypatch.setattr`` call
sites resolve unchanged.

Shard-internal consumers such as ``authority.py`` import directly from the
canonical owners rather than through this facade.
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
    SANITIZED_PLUGIN_MANIFEST_SCHEMA_VERSION,
    _copy_non_skill_plugin_assets,
    _projection_skills_manifest,
    _render_agent_definitions,
    materialize_agent_skill_tree,
    materialize_sanitized_plugin_root,
    write_generated_hooks_json,
)
from autoskillit.workspace._projected_artifact._validation import (
    validate_sanitized_plugin_artifact,
)

__all__ = [
    "AgentSkillDocument",
    "SANITIZED_PLUGIN_MANIFEST_SCHEMA_VERSION",
    "SkillProjectionContext",
    "project_agent_skill_document",
    "materialize_agent_skill_tree",
    "materialize_sanitized_plugin_root",
    "validate_sanitized_plugin_artifact",
    "write_generated_hooks_json",
    "_copy_non_skill_plugin_assets",
    "_default_base_branch",
    "_direct_install_projection_context",
    "_projection_skills_manifest",
    "_render_agent_definitions",
    "SkillContractRecord",
]
