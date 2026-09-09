"""Value objects for projected-artifact authority transactions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from autoskillit.core import (
    EffectiveSkillCatalogAuthority,
    PluginArtifactIdentity,
    SkillAuthority,
    SkillProjectionRefusal,
    SkillSemanticAdaptationResult,
)
from autoskillit.workspace._projected_artifact._documents import SkillProjectionContext
from autoskillit.workspace._projection_cache import read_projected_plugin_identity


@dataclass(frozen=True, slots=True)
class _ProjectedArtifactPlan:
    source_root: Path
    destination: Path
    manifest_path: Path
    lease_path: Path
    semantic_key: str
    catalog: EffectiveSkillCatalogAuthority
    validation_catalog: tuple[SkillAuthority, ...] | EffectiveSkillCatalogAuthority
    require_sources_within_root: bool
    context: SkillProjectionContext
    unavailable: tuple[SkillProjectionRefusal, ...]
    semantic_adaptations: Mapping[str, SkillSemanticAdaptationResult]


@dataclass(frozen=True, slots=True)
class _StagedProjectedArtifact:
    root: Path
    manifest: Path
    identity: PluginArtifactIdentity


def _manifest_identity(plan: _ProjectedArtifactPlan) -> PluginArtifactIdentity:
    return read_projected_plugin_identity(
        plan.destination,
        manifest_path=plan.manifest_path,
        expected_semantic_key=plan.semantic_key,
        expected_projection_version=plan.context.projection_version,
    )
