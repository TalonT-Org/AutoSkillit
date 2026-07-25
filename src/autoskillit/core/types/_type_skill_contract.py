"""Backend-neutral skill source identity contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from ._type_constants_registries import (
    SKILL_CAPABILITY_REGISTRY,
    validate_skill_capability_roles,
)
from ._type_enums import SkillExecutionRole, SkillSource
from ._type_exceptions import SkillContractError
from ._type_results import WriteBehaviorSpec

__all__ = [
    "MACHINE_ONLY_SKILL_FRONTMATTER_KEYS",
    "SKILL_PROJECTION_VERSION",
    "SKILL_SESSION_CONTRACT_SCHEMA_VERSION",
    "SkillSessionContract",
    "SkillSourceIdentity",
    "SkillSourceRef",
    "SkillVisibilitySpec",
    "StoredSkillSessionContract",
    "derive_backend_requirements",
]


MACHINE_ONLY_SKILL_FRONTMATTER_KEYS = frozenset(
    {
        "activate_deps",
        "backend_requirements",
        "execution_role",
        "uses_capabilities",
    }
)
# Bumped 1 -> 2: the projection's *source* changed (the running package rather
# than a third-party cache snapshot) and its cache key gained a content digest
# over the projected asset tree. Every existing projection and every stored
# dispatch contract is therefore semantically stale. Bumping makes that
# invalidation explicit instead of relying on the new digest to happen to
# differ, and causes stale stored contracts to be refused loudly by the
# assertion in _skill_session_contract_store rather than silently reused.
SKILL_PROJECTION_VERSION = 2
SKILL_SESSION_CONTRACT_SCHEMA_VERSION = 2


def derive_backend_requirements(uses_capabilities: frozenset[str]) -> frozenset[str]:
    """Return the backend-name constraints implied by a capability set."""
    known = uses_capabilities & SKILL_CAPABILITY_REGISTRY.keys()
    return frozenset().union(
        *(SKILL_CAPABILITY_REGISTRY[capability].required_backends for capability in known)
    )


@dataclass(frozen=True, slots=True)
class SkillSourceIdentity:
    """Path-free logical identity safe to carry beyond source resolution."""

    origin: SkillSource
    logical_name: str
    search_dir: str | None = None
    precedence: int | None = None


@dataclass(frozen=True, slots=True)
class SkillSourceRef:
    """Private source reference selected for a skill machine contract."""

    origin: SkillSource
    logical_name: str
    skill_path: Path
    search_dir: str | None = None
    precedence: int | None = None

    def validate_identity(
        self,
        origin: SkillSource,
        logical_name: str,
        skill_path: Path,
    ) -> None:
        """Reject a direct skill identity that conflicts with this source reference."""
        if (origin, logical_name, skill_path) != (
            self.origin,
            self.logical_name,
            self.skill_path,
        ):
            raise SkillContractError("SkillInfo source_ref does not match direct fields")

    @property
    def identity(self) -> SkillSourceIdentity:
        """Return the path-free identity used by catalogs and projections."""
        return SkillSourceIdentity(
            origin=self.origin,
            logical_name=self.logical_name,
            search_dir=self.search_dir,
            precedence=self.precedence,
        )


@dataclass(frozen=True, slots=True)
class SkillVisibilitySpec:
    """Typed visibility and tier policy passed across composition boundaries."""

    disabled_categories: frozenset[str] = frozenset()
    custom_tags: Mapping[str, frozenset[str]] = field(default_factory=dict)
    features: Mapping[str, bool] = field(default_factory=dict)
    experimental_enabled: bool = False
    enabled_packs: frozenset[str] = frozenset()
    tier1_skills: frozenset[str] = frozenset()
    tier2_skills: frozenset[str] = frozenset()
    tier3_skills: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "disabled_categories",
            frozenset(self.disabled_categories),
        )
        object.__setattr__(
            self,
            "custom_tags",
            MappingProxyType(
                {tag: frozenset(skill_names) for tag, skill_names in self.custom_tags.items()}
            ),
        )
        object.__setattr__(self, "features", MappingProxyType(dict(self.features)))
        object.__setattr__(self, "enabled_packs", frozenset(self.enabled_packs))
        object.__setattr__(self, "tier1_skills", frozenset(self.tier1_skills))
        object.__setattr__(self, "tier2_skills", frozenset(self.tier2_skills))
        object.__setattr__(self, "tier3_skills", frozenset(self.tier3_skills))


@dataclass(frozen=True, slots=True)
class SkillSessionContract:
    """Immutable execution contract bound to a projected skill snapshot."""

    root_name: str
    execution_role: SkillExecutionRole
    source_refs: Mapping[str, SkillSourceRef]
    closure: tuple[str, ...]
    capability_union: frozenset[str]
    canonical_digests: Mapping[str, str]
    projected_digests: Mapping[str, str]
    projection_version: int
    project_root: str
    cwd: str
    backend: str
    resolved_command: str
    member_roles: Mapping[str, SkillExecutionRole]
    member_capabilities: Mapping[str, frozenset[str]]
    member_activate_deps: Mapping[str, tuple[str, ...]]
    canonical_contents: Mapping[str, str]
    expected_output_patterns: tuple[str, ...] = ()
    write_behavior: WriteBehaviorSpec = WriteBehaviorSpec()
    read_only: bool = False
    completion_required: bool = False
    skill_contract_json: str = ""
    projection_substitutions: tuple[tuple[str, str], ...] = ()
    projection_gating: bool | None = None
    projection_namespace: str | None = None
    schema_version: int = SKILL_SESSION_CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_refs", MappingProxyType(dict(self.source_refs)))
        object.__setattr__(
            self,
            "canonical_digests",
            MappingProxyType(dict(self.canonical_digests)),
        )
        object.__setattr__(
            self,
            "projected_digests",
            MappingProxyType(dict(self.projected_digests)),
        )
        object.__setattr__(self, "member_roles", MappingProxyType(dict(self.member_roles)))
        object.__setattr__(
            self,
            "member_capabilities",
            MappingProxyType(
                {
                    name: frozenset(capabilities)
                    for name, capabilities in self.member_capabilities.items()
                }
            ),
        )
        object.__setattr__(
            self,
            "member_activate_deps",
            MappingProxyType(
                {
                    name: tuple(dependencies)
                    for name, dependencies in self.member_activate_deps.items()
                }
            ),
        )
        object.__setattr__(
            self,
            "canonical_contents",
            MappingProxyType(dict(self.canonical_contents)),
        )

    @property
    def backend_requirements(self) -> frozenset[str]:
        """Derive backend constraints from the persisted capability union."""
        validate_skill_capability_roles(self.capability_union, self.execution_role)
        return derive_backend_requirements(self.capability_union)


@dataclass(frozen=True, slots=True)
class StoredSkillSessionContract:
    """Validated contract plus the retained projected snapshot directory."""

    contract: SkillSessionContract
    snapshot_dir: Path
    raw_session_id: str
