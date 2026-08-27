"""Immutable skill record types and diagnostic helpers.

Defines ``SkillInfo``, ``SkillCatalogEntry``, ``SkillExclusion``,
``EffectiveSkillCatalog``, ``EffectiveSkillInvocation``, the diagnostic helpers
(``invalidity_hints``, ``render_skill_invalidities``), and the transitive-closure
BFS over a captured catalog. ``SkillInfo.__post_init__`` parses frontmatter via
``skill_format`` so each record still embeds the canonical parsed contract.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

from autoskillit.core import (
    PACK_REGISTRY,
    SKILL_CONTRACT_REMEDIATIONS,
    ExplorationVectorDef,
    SkillContractError,
    SkillExecutionRole,
    SkillInvalidityAuthority,
    SkillInvalidityKind,
    SkillSemanticPlan,
    SkillSource,
    SkillSourceIdentity,
    SkillSourceRef,
    get_logger,
    validate_skill_capability_roles,
)
from autoskillit.workspace.skill_format import (
    SkillFrontmatterParseResult,
    parse_frontmatter_content,
)

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class SkillInvalidity:
    """One typed reason a skill's contract failed validation."""

    kind: SkillInvalidityKind
    detail: str
    capability: str | None = None


def invalidity_hints(invalidities: Iterable[SkillInvalidity]) -> tuple[str, ...]:
    """Return one deduplicated remediation hint per distinct invalidity kind."""
    return tuple(
        SKILL_CONTRACT_REMEDIATIONS[kind].hint
        for kind in dict.fromkeys(item.kind for item in invalidities)
    )


def render_skill_invalidities(invalidities: Iterable[SkillInvalidityAuthority]) -> str:
    """Render typed skill invalidities for operator-facing diagnostics."""
    return "; ".join(item.detail for item in invalidities)


@dataclass(frozen=True, slots=True)
class SkillInfo:
    """One exact, typed skill machine contract selected from a source."""

    name: str
    source: SkillSource
    path: Path
    source_ref: SkillSourceRef | None = None
    categories: frozenset[str] = frozenset()
    uses_capabilities: frozenset[str] = frozenset()
    semantic_plan: SkillSemanticPlan | None = None
    execution_role: SkillExecutionRole | None = SkillExecutionRole.SESSION
    activate_deps: tuple[str, ...] = ()
    required_resources: tuple[str, ...] = ()
    resource_digests: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    exploration_vectors: tuple[ExplorationVectorDef, ...] = ()
    exploration_sidecar_digest: str = ""
    canonical_content: str = ""
    canonical_digest: str = ""
    frontmatter: SkillFrontmatterParseResult | None = None
    invalidities: tuple[SkillInvalidity, ...] = ()

    def __post_init__(self) -> None:
        invalidities: list[SkillInvalidity] = []
        if self.source_ref is None:
            object.__setattr__(
                self, "source_ref", SkillSourceRef(self.source, self.name, self.path)
            )
        else:
            self.source_ref.validate_identity(
                self.source,
                self.name,
                self.path,
            )
        if not self.canonical_content and self.path.is_file():
            try:
                object.__setattr__(
                    self,
                    "canonical_content",
                    self.path.read_text(encoding="utf-8"),
                )
            except (OSError, UnicodeDecodeError):
                logger.warning(
                    "skill_info_canonical_content_unreadable",
                    skill_name=self.name,
                    source=str(self.source),
                    path=str(self.path),
                    exc_info=True,
                )
                invalidities.append(
                    SkillInvalidity(
                        SkillInvalidityKind.FRONTMATTER_PARSE,
                        f"could not read canonical content from {self.path}",
                    )
                )
        if invalidities:
            object.__setattr__(
                self,
                "invalidities",
                tuple(self.invalidities) + tuple(invalidities),
            )
        if self.canonical_content and not self.canonical_digest:
            object.__setattr__(
                self,
                "canonical_digest",
                hashlib.sha256(self.canonical_content.encode()).hexdigest(),
            )
        if self.frontmatter is None and self.canonical_content:
            object.__setattr__(
                self,
                "frontmatter",
                parse_frontmatter_content(self.canonical_content),
            )
        if (
            self.frontmatter is not None
            and not self.frontmatter.is_valid
            and not self.invalidities
        ):
            object.__setattr__(
                self,
                "invalidities",
                (
                    SkillInvalidity(
                        SkillInvalidityKind.FRONTMATTER_PARSE,
                        f"invalid frontmatter: {self.frontmatter.error}",
                    ),
                ),
            )

    @property
    def canonical_bytes(self) -> bytes:
        return self.canonical_content.encode("utf-8")

    @property
    def source_identity(self) -> SkillSourceIdentity:
        """Return the validated effective source identity."""
        assert self.source_ref is not None
        return self.source_ref.identity


@dataclass(frozen=True, slots=True)
class SkillCatalogEntry:
    """Path-free machine contract used by role-derived downstream catalogs."""

    name: str
    source: SkillSource
    source_identity: SkillSourceIdentity
    categories: frozenset[str]
    uses_capabilities: frozenset[str]
    semantic_plan: SkillSemanticPlan | None
    execution_role: SkillExecutionRole
    activate_deps: tuple[str, ...]
    required_resources: tuple[str, ...]
    resource_digests: Mapping[str, str]
    exploration_vectors: tuple[ExplorationVectorDef, ...]
    exploration_sidecar_digest: str
    canonical_content: str
    canonical_digest: str
    frontmatter: SkillFrontmatterParseResult
    invalidities: tuple[SkillInvalidity, ...] = ()

    @classmethod
    def from_skill_info(cls, skill: SkillInfo) -> SkillCatalogEntry:
        """Remove private source paths while preserving the parsed contract."""
        if skill.invalidities:
            raise SkillContractError(
                f"invalid contract for {skill.name!r}: "
                f"{render_skill_invalidities(skill.invalidities)}"
            )
        if skill.execution_role is None:
            raise SkillContractError(f"skill {skill.name!r} has no valid execution role")
        if skill.source_ref is None:
            raise SkillContractError(f"skill {skill.name!r} has no effective source identity")
        if skill.frontmatter is None:
            raise SkillContractError(f"skill {skill.name!r} has no parsed frontmatter")
        return cls(
            name=skill.name,
            source=skill.source,
            source_identity=skill.source_ref.identity,
            categories=skill.categories,
            uses_capabilities=skill.uses_capabilities,
            semantic_plan=skill.semantic_plan,
            execution_role=skill.execution_role,
            activate_deps=skill.activate_deps,
            required_resources=skill.required_resources,
            resource_digests=skill.resource_digests,
            exploration_vectors=skill.exploration_vectors,
            exploration_sidecar_digest=skill.exploration_sidecar_digest,
            canonical_content=skill.canonical_content,
            canonical_digest=skill.canonical_digest,
            frontmatter=skill.frontmatter,
        )


@dataclass(frozen=True, slots=True)
class SkillExclusion:
    """One project-local skill candidate excluded from the effective catalog.

    Recorded — never silently dropped — whenever the resolution boundary
    (``resolve_effective`` / ``_list_effective_unfiltered``) skips an invalid
    project-local candidate in favor of the next valid source or, if none
    exists, an operator-visible absence.
    """

    name: str
    path: Path
    search_dir: str
    invalidities: tuple[SkillInvalidity, ...]
    fallback: SkillSource | None
    hints: tuple[str, ...]

    @classmethod
    def from_skill_info(cls, info: SkillInfo, *, fallback: SkillSource | None) -> SkillExclusion:
        """Build a record from a rejected candidate's own invalidities."""
        assert info.source_ref is not None
        return cls(
            name=info.name,
            path=info.path,
            search_dir=info.source_ref.search_dir or "",
            invalidities=info.invalidities,
            fallback=fallback,
            hints=invalidity_hints(info.invalidities),
        )


@dataclass(frozen=True, slots=True)
class EffectiveSkillCatalog:
    """Immutable role-filtered view of every effective skill source."""

    skills: tuple[SkillCatalogEntry, ...]
    execution_role: SkillExecutionRole
    namespace_sources: Mapping[str, SkillSource] = field(default_factory=dict)
    exclusions: tuple[SkillExclusion, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "namespace_sources",
            MappingProxyType(dict(self.namespace_sources)),
        )
        for skill in self.skills:
            if skill.invalidities:
                raise SkillContractError(
                    f"invalid catalog contract for {skill.name!r}: "
                    f"{render_skill_invalidities(skill.invalidities)}"
                )
            if skill.execution_role is not self.execution_role:
                raise SkillContractError(
                    f"catalog role {self.execution_role.value!r} cannot contain "
                    f"{skill.execution_role.value!r} skill {skill.name!r}"
                )


@dataclass(frozen=True, slots=True)
class EffectiveSkillInvocation:
    """Validated root plus its complete executable dependency closure."""

    root: SkillInfo
    closure: tuple[SkillInfo, ...]
    capability_union: frozenset[str]
    project_root: Path | None
    execution_role: SkillExecutionRole

    def __post_init__(self) -> None:
        if self.root not in self.closure:
            raise SkillContractError("effective invocation root is absent from its closure")
        names = tuple(member.name for member in self.closure)
        if len(names) != len(set(names)):
            raise SkillContractError("effective invocation closure contains duplicate members")
        for member in self.closure:
            if (
                member.invalidities
                or member.frontmatter is None
                or not member.frontmatter.is_valid
            ):
                reason = (
                    render_skill_invalidities(member.invalidities)
                    if member.invalidities
                    else "missing parsed frontmatter"
                )
                raise SkillContractError(
                    f"invalid effective invocation contract for {member.name!r}: {reason}"
                )
            if member.execution_role is not self.execution_role:
                actual = (
                    member.execution_role.value if member.execution_role is not None else "invalid"
                )
                raise SkillContractError(
                    f"effective invocation role {self.execution_role.value!r} cannot contain "
                    f"{actual!r} skill {member.name!r}"
                )
            validate_skill_capability_roles(member.uses_capabilities, self.execution_role)
        expected_union = frozenset().union(*(member.uses_capabilities for member in self.closure))
        if self.capability_union != expected_union:
            raise SkillContractError(
                "effective invocation capability union does not match its closure"
            )

    @property
    def semantic_plans(self) -> tuple[SkillSemanticPlan, ...]:
        """Portable semantic plans retained by the exact invocation closure."""
        return tuple(
            member.semantic_plan for member in self.closure if member.semantic_plan is not None
        )


def _build_pack_index(skills: Iterable[SkillInfo]) -> dict[str, set[str]]:
    index: dict[str, set[str]] = {}
    for skill in skills:
        for category in skill.categories:
            index.setdefault(category, set()).add(skill.name)
    return index


def compute_skill_closure(skill_name: str, provider: Any) -> frozenset[str]:
    """Return the valid transitive dependency closure from one captured catalog."""
    skills = {skill.name: skill for skill in provider.list_skills()}
    return _compute_skill_closure(skill_name, skills)


def _compute_skill_closure(
    skill_name: str,
    skills: Mapping[str, SkillInfo],
) -> frozenset[str]:
    """Compute a closure without consulting a current metadata resolver."""
    if skill_name not in skills:
        return frozenset()
    pack_index: dict[str, set[str]] | None = None
    visited: set[str] = set()
    resolved: set[str] = set()
    queue: list[str] = [skill_name]
    while queue:
        name = queue.pop()
        if name in visited:
            continue
        visited.add(name)
        info = skills.get(name)
        if (
            info is None
            or info.invalidities
            or info.execution_role is not SkillExecutionRole.SESSION
        ):
            continue
        resolved.add(name)
        for dependency in info.activate_deps:
            if dependency in PACK_REGISTRY:
                if pack_index is None:
                    pack_index = _build_pack_index(skills.values())
                queue.extend(
                    member for member in pack_index.get(dependency, ()) if member not in visited
                )
            elif dependency not in visited:
                queue.append(dependency)
    return frozenset(resolved)


__all__ = [
    "EffectiveSkillCatalog",
    "EffectiveSkillInvocation",
    "SkillCatalogEntry",
    "SkillExclusion",
    "SkillInfo",
    "SkillInvalidity",
    "compute_skill_closure",
    "invalidity_hints",
    "render_skill_invalidities",
]
