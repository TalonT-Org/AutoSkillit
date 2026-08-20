"""Declared safety backstops for destructive plugin-artifact retirement."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from ._type_plugin_source import PluginArtifactKind

__all__ = ["RETIREMENT_BACKSTOP_LEDGER", "RetirementBackstopDef"]


@dataclass(frozen=True, slots=True)
class RetirementBackstopDef:
    """Declared reclaim backstops for one artifact kind's owner."""

    artifact_kind: PluginArtifactKind
    owner_qualname: str
    wires_is_current: bool
    wires_current_identity: bool
    exclusive_lease_backstop: bool
    rationale: str


RETIREMENT_BACKSTOP_LEDGER: Mapping[PluginArtifactKind, RetirementBackstopDef] = MappingProxyType(
    {
        PluginArtifactKind.PROJECTION: RetirementBackstopDef(
            artifact_kind=PluginArtifactKind.PROJECTION,
            owner_qualname="ProjectedPluginRetirementOwner",
            wires_is_current=True,
            wires_current_identity=True,
            exclusive_lease_backstop=True,
            rationale=(
                "The active semantic key blocks the selected projection; exact identity "
                "and the launch reader lease protect every bound incarnation."
            ),
        ),
        PluginArtifactKind.INSTALLED_PLUGIN: RetirementBackstopDef(
            artifact_kind=PluginArtifactKind.INSTALLED_PLUGIN,
            owner_qualname="InstalledPluginArtifactRetirementOwner",
            wires_is_current=False,
            wires_current_identity=True,
            exclusive_lease_backstop=True,
            rationale=(
                "The installed tree has no selector; exact identity plus the exclusive "
                "lease prevents reclaiming a bound incarnation."
            ),
        ),
        PluginArtifactKind.PLUGIN_GENERATION: RetirementBackstopDef(
            artifact_kind=PluginArtifactKind.PLUGIN_GENERATION,
            owner_qualname="GenerationArtifactRetirementOwner",
            wires_is_current=True,
            wires_current_identity=True,
            exclusive_lease_backstop=True,
            rationale=(
                "The generation selector blocks the selected path; exact identity and "
                "the exclusive lease protect concurrently bound generations."
            ),
        ),
    }
)
