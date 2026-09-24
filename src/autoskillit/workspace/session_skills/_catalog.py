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

import os
import shutil
import stat
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
    SemanticAdaptationContext,
    SkillExecutionRole,
    SkillSemanticAdaptationResult,
    SkillSemanticOperation,
    SkillSemanticPlan,
    SkillSource,
    SkillSourceRef,
    SkillUnavailabilityPayload,
    SkillUnavailabilityRecord,
    get_logger,
    launch_evidence_digest,
    load_bundled_agent_definitions,
    strict_walk,
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
    from autoskillit.core import CodingAgentBackend

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
    launch_evidence_digest: str = ""
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

    def restrict_to_native_roles(
        self,
        finalized_native_roles: frozenset[str],
    ) -> CompiledSessionSkillCatalog:
        """Narrow this admission to skills whose native child-spawn targets were finalized."""
        kept: list[SkillCatalogEntry] = []
        dropped: list[SkillUnavailableMetadata] = []
        for skill in self.catalog.skills:
            missing_targets = sorted(
                set(self.required_native_roles.get(skill.name, ())) - finalized_native_roles
            )
            if missing_targets:
                dropped.append(
                    SkillUnavailableMetadata(
                        skill=skill.name,
                        backend=self.backend,
                        operation=SkillSemanticOperation.CHILD_SPAWN,
                        diagnostic=(
                            f"native child-spawn targets are unavailable: {missing_targets}"
                        ),
                    )
                )
                continue
            kept.append(skill)
        kept_names = {skill.name for skill in kept}
        return CompiledSessionSkillCatalog(
            backend=self.backend,
            catalog=EffectiveSkillCatalog(
                skills=tuple(kept),
                execution_role=self.catalog.execution_role,
                namespace_sources={
                    name: source
                    for name, source in self.catalog.namespace_sources.items()
                    if name in kept_names
                },
                exclusions=self.catalog.exclusions,
            ),
            unavailable=tuple(sorted((*self.unavailable, *dropped), key=lambda item: item.skill)),
            required_native_roles={
                name: roles
                for name, roles in self.required_native_roles.items()
                if name in kept_names
            },
            launch_evidence_digest=self.launch_evidence_digest,
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
    if adaptation.adaptation_context_digest:
        return ()
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
    adaptation_context: SemanticAdaptationContext | None = None,
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
        adaptation = backend.adapt_skill_semantics(plan, adaptation_context)
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
        launch_evidence_digest=launch_evidence_digest(adaptation_context),
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


def _copy_restored_skill_catalog(
    snapshot_dir: Path,
    catalog_dir: Path,
    *,
    skills_subdir: Path,
) -> None:
    if snapshot_dir.is_symlink():
        raise ValueError(f"restored skill snapshot root must not be a symlink: {snapshot_dir}")
    if not snapshot_dir.is_dir():
        raise ValueError(f"restored skill snapshot root must be a real directory: {snapshot_dir}")
    _validate_restored_snapshot(snapshot_dir)
    source_catalog = snapshot_dir / skills_subdir
    if source_catalog.is_symlink() or not source_catalog.is_dir():
        raise ValueError(
            f"restored skill snapshot catalog must be a real directory: {source_catalog}"
        )
    _validate_restored_skill_catalog(source_catalog)
    if os.path.lexists(catalog_dir):
        raise RuntimeError(f"restored skill catalog path already exists: {catalog_dir}")
    catalog_dir.parent.mkdir(parents=True, exist_ok=True)
    catalog_dir.mkdir()
    for entry in strict_walk(source_catalog):
        relative_path = Path(entry.relative_path)
        destination = catalog_dir / relative_path
        if entry.kind == "l":
            raise ValueError(
                f"restored skill snapshot contains a symlink: {source_catalog / relative_path}"
            )
        if entry.kind == "d":
            destination.mkdir()
            continue
        if entry.kind != "f":
            raise ValueError(
                "restored skill snapshot contains an invalid entry: "
                f"{source_catalog / relative_path}"
            )
        _copy_restored_regular_file(entry.dir_fd, entry.name, destination)


def _validate_restored_snapshot(snapshot_dir: Path) -> None:
    for entry in strict_walk(snapshot_dir):
        if entry.kind == "l":
            raise ValueError(
                f"restored skill snapshot contains a symlink: {snapshot_dir / entry.relative_path}"
            )


def _validate_restored_skill_catalog(source_catalog: Path) -> None:
    skill_names: set[str] = set()
    skill_documents: set[str] = set()
    for entry in strict_walk(source_catalog):
        relative_path = Path(entry.relative_path)
        if entry.kind == "l":
            raise ValueError(
                f"restored skill snapshot contains a symlink: {source_catalog / relative_path}"
            )
        if len(relative_path.parts) == 1:
            if entry.kind != "d" or relative_path.name.startswith("."):
                raise ValueError(
                    f"restored skill snapshot contains an invalid catalog entry: "
                    f"{source_catalog / relative_path}"
                )
            skill_names.add(relative_path.name)
        elif len(relative_path.parts) == 2 and relative_path.name == "SKILL.md":
            if entry.kind == "f":
                skill_documents.add(relative_path.parts[0])
    if not skill_names or skill_documents != skill_names:
        raise ValueError("restored skill snapshot entries must each contain a regular SKILL.md")


def _copy_restored_regular_file(source_dir_fd: int, source_name: str, destination: Path) -> None:
    source_fd = os.open(
        source_name,
        os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
        dir_fd=source_dir_fd,
    )
    try:
        if not stat.S_ISREG(os.fstat(source_fd).st_mode):
            raise ValueError(f"restored skill snapshot contains a non-regular file: {source_name}")
        with os.fdopen(source_fd, "rb") as source:
            source_fd = -1
            with destination.open("xb") as target:
                shutil.copyfileobj(source, target)
    finally:
        if source_fd != -1:
            os.close(source_fd)


__all__ = [
    "CompiledSessionSkillCatalog",
    "SkillUnavailableMetadata",
    "_SKILL_UNAVAILABILITY_SCHEMA_VERSION",
    "_canonical_skill_unavailability_payload",
    "_copy_restored_skill_catalog",
    "_merge_skill_unavailability_payloads",
    "_profile_skill_catalog",
    "_profile_skill_infos",
    "_required_native_child_roles",
    "_session_agent_definitions",
    "compile_session_skill_catalog",
    "write_skill_unavailability_metadata",
]
