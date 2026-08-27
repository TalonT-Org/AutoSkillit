"""Session-skill catalog compilation and unavailability publication.

Single owner of catalog compilation, refusal payload construction,
finalized native-role reachability, profile discovery/catalog compilation
and admission helpers, and the durable unavailability writer. Catalog
compilation remains role-neutral: it preserves the input catalog's execution
role and does not add a SESSION-only precondition (fleet callers compile
ORCHESTRATOR catalogs).

Both admission passes are preserved: semantic-operation filtering before
backend setup and finalized-role filtering after setup. Profile admission
decisions, prior refusal evidence, deterministic merged-payload ordering,
and relocatable unavailability metadata all remain intact.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, cast

from autoskillit.core import (
    AGENT_PROVISIONING_BASELINE,
    BUNDLED_EXPLORER_ROLES,
    AgentDef,
    EffectiveSkillCatalogAuthority,
    SkillSemanticAdaptationResult,
    SkillSemanticOperation,
    SkillSemanticPlan,
    SkillSource,
    SkillSourceRef,
    SkillUnavailabilityPayload,
    SkillUnavailabilityRecord,
    get_logger,
    load_bundled_agent_definitions,
    write_versioned_json,
)
from autoskillit.workspace.skills import (
    EffectiveSkillCatalog,
    SkillCatalogEntry,
    SkillExclusion,
    SkillInfo,
    _skill_info_from_frontmatter,
    render_skill_invalidities,
)

if TYPE_CHECKING:
    from autoskillit.core import CodingAgentBackend, SkillExecutionRole

logger = get_logger(__name__)

_SKILL_UNAVAILABILITY_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class SkillUnavailableMetadata:
    """Deterministic SESSION omission with supplemental backend detail."""

    skill: str
    backend: str
    operation: SkillSemanticOperation
    diagnostic: str

    def to_payload(self) -> SkillUnavailabilityRecord:
        return {
            "skill": self.skill,
            "backend": self.backend,
            "operation": self.operation.value,
            "diagnostic": self.diagnostic,
        }


@dataclass(frozen=True, slots=True)
class CompiledSessionSkillCatalog:
    backend: str
    catalog: EffectiveSkillCatalog
    unavailable: tuple[SkillUnavailableMetadata, ...]
    required_native_roles: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    unavailability_payload: SkillUnavailabilityPayload = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "required_native_roles",
            MappingProxyType(dict(self.required_native_roles)),
        )
        object.__setattr__(
            self,
            "unavailability_payload",
            _canonical_skill_unavailability_payload(
                self.backend,
                (item.to_payload() for item in self.unavailable),
            ),
        )


def _canonical_skill_unavailability_payload(
    backend: str | None,
    unavailable: Iterable[SkillUnavailabilityRecord],
) -> SkillUnavailabilityPayload:
    """Return one deterministic payload, deduplicated by its wire identity."""
    records_by_identity: dict[tuple[str, str, str, str], SkillUnavailabilityRecord] = {}
    for record in unavailable:
        identity = (
            record["skill"],
            record["backend"],
            record["operation"],
            record["diagnostic"],
        )
        records_by_identity[identity] = record
    return {
        "backend": backend,
        "unavailable": tuple(
            records_by_identity[identity] for identity in sorted(records_by_identity)
        ),
    }


def _merge_skill_unavailability_payloads(
    backend: str | None,
    *payloads: SkillUnavailabilityPayload,
) -> SkillUnavailabilityPayload:
    """Merge backend admission results without changing their record identities."""
    return _canonical_skill_unavailability_payload(
        backend,
        (record for payload in payloads for record in payload["unavailable"]),
    )


def _required_native_child_roles(
    plan: SkillSemanticPlan,
    adaptation: SkillSemanticAdaptationResult,
) -> tuple[str, ...]:
    return tuple(
        sorted({adaptation.logical_role_mapping[spawn.role] for spawn in plan.child_spawns})
    )


def _session_agent_definitions(
    required_native_roles: AbstractSet[str],
    explorer_binding_env: Mapping[str, Mapping[str, str]] | None,
) -> tuple[AgentDef, ...]:
    bound_explorer_roles = frozenset(explorer_binding_env or ())
    return tuple(
        definition
        for definition in load_bundled_agent_definitions()
        if not definition.reader_tools
        and (
            definition.name not in BUNDLED_EXPLORER_ROLES
            or definition.name in bound_explorer_roles
        )
        and (
            definition.provisioning == AGENT_PROVISIONING_BASELINE
            or definition.name in required_native_roles
        )
    )


def compile_session_skill_catalog(
    catalog: EffectiveSkillCatalogAuthority,
    backend: CodingAgentBackend,
    *,
    finalized_native_roles: frozenset[str] | None = None,
) -> CompiledSessionSkillCatalog:
    """Publish only skills whose mandatory semantics adapt on the selected backend."""
    supported: list[SkillCatalogEntry] = []
    unavailable: list[SkillUnavailableMetadata] = []
    required_native_roles: dict[str, tuple[str, ...]] = {}
    for skill in catalog.skills:
        plan = skill.semantic_plan
        if plan is None:
            supported.append(cast(SkillCatalogEntry, skill))
            required_native_roles[skill.name] = ()
            continue
        adaptation = backend.adapt_skill_semantics(plan)
        unsupported_operation = adaptation.validate_refusal_for(
            plan,
            backend=backend.name,
        )
        if unsupported_operation is not None:
            unavailable.append(
                SkillUnavailableMetadata(
                    skill=skill.name,
                    backend=backend.name,
                    operation=unsupported_operation,
                    diagnostic=adaptation.diagnostic or "unsupported skill semantics",
                )
            )
            continue
        adaptation.validate_for(plan, backend=backend.name)
        native_spawn_targets = _required_native_child_roles(plan, adaptation)
        if finalized_native_roles is not None:
            missing_targets = sorted(set(native_spawn_targets) - finalized_native_roles)
            if missing_targets:
                unavailable.append(
                    SkillUnavailableMetadata(
                        skill=skill.name,
                        backend=backend.name,
                        operation=SkillSemanticOperation.CHILD_SPAWN,
                        diagnostic=(
                            f"native child-spawn targets are unavailable: {missing_targets}"
                        ),
                    )
                )
                continue
        supported.append(cast(SkillCatalogEntry, skill))
        required_native_roles[skill.name] = native_spawn_targets
    filtered_names = {skill.name for skill in supported}
    namespace_sources = {
        name: source
        for name, source in catalog.namespace_sources.items()
        if name in filtered_names
    }
    return CompiledSessionSkillCatalog(
        backend=backend.name,
        catalog=EffectiveSkillCatalog(
            skills=tuple(supported),
            execution_role=catalog.execution_role,
            namespace_sources=namespace_sources,
            exclusions=cast(tuple[SkillExclusion, ...], tuple(catalog.exclusions)),
        ),
        unavailable=tuple(sorted(unavailable, key=lambda item: item.skill)),
        required_native_roles=required_native_roles,
    )


def write_skill_unavailability_metadata(
    add_dir: Path,
    *,
    unavailability_payload: SkillUnavailabilityPayload,
) -> None:
    """Publish deterministic machine-readable SESSION catalog omissions."""
    write_versioned_json(
        add_dir / "skill-unavailability.json",
        cast(dict[str, Any], unavailability_payload),
        schema_version=_SKILL_UNAVAILABILITY_SCHEMA_VERSION,
    )


def _profile_skill_infos(profile_skills_root: Path) -> tuple[SkillInfo, ...]:
    if not profile_skills_root.is_dir():
        return ()
    result: list[SkillInfo] = []
    for entry in sorted(profile_skills_root.iterdir(), key=lambda item: item.name):
        skill_md = entry / "SKILL.md"
        if (
            entry.is_symlink()
            or skill_md.is_symlink()
            or not entry.is_dir()
            or not skill_md.is_file()
        ):
            continue
        info = _skill_info_from_frontmatter(
            entry.name,
            SkillSource.THIRD_PARTY,
            skill_md,
            source_ref=SkillSourceRef(
                origin=SkillSource.THIRD_PARTY,
                logical_name=entry.name,
                skill_path=skill_md,
                search_dir=str(profile_skills_root),
            ),
        )
        if info.invalidities or info.execution_role is not SkillExecutionRole.SESSION:
            logger.warning(
                "profile_skill_contract_rejected",
                skill=entry.name,
                reason=(
                    render_skill_invalidities(info.invalidities)
                    if info.invalidities
                    else "non-session execution role"
                ),
            )
            continue
        result.append(info)
    return tuple(result)


def _profile_skill_catalog(infos: tuple[SkillInfo, ...]) -> EffectiveSkillCatalog:
    return EffectiveSkillCatalog(
        skills=tuple(SkillCatalogEntry.from_skill_info(info) for info in infos),
        execution_role=SkillExecutionRole.SESSION,
        namespace_sources={info.name: info.source for info in infos},
    )


def _compile_reachable_profile_skill_catalog(
    admission_compilation: CompiledSessionSkillCatalog,
    backend: CodingAgentBackend,
    finalized_native_roles: frozenset[str],
) -> CompiledSessionSkillCatalog:
    reachability_compilation = compile_session_skill_catalog(
        admission_compilation.catalog,
        backend,
        finalized_native_roles=finalized_native_roles,
    )
    return CompiledSessionSkillCatalog(
        backend=backend.name,
        catalog=reachability_compilation.catalog,
        unavailable=tuple(
            sorted(
                (
                    *admission_compilation.unavailable,
                    *reachability_compilation.unavailable,
                ),
                key=lambda item: item.skill,
            )
        ),
        required_native_roles=reachability_compilation.required_native_roles,
    )


__all__ = [
    "CompiledSessionSkillCatalog",
    "SkillUnavailableMetadata",
    "_SKILL_UNAVAILABILITY_SCHEMA_VERSION",
    "_canonical_skill_unavailability_payload",
    "_compile_reachable_profile_skill_catalog",
    "_merge_skill_unavailability_payloads",
    "_profile_skill_catalog",
    "_profile_skill_infos",
    "_required_native_child_roles",
    "_session_agent_definitions",
    "compile_session_skill_catalog",
    "write_skill_unavailability_metadata",
]
