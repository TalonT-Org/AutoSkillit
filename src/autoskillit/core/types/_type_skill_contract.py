"""Backend-neutral skill source identity contracts."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType

from ._type_enums import SkillExecutionRole, SkillSource
from ._type_exceptions import SkillContractError
from ._type_execution_identity import ExecutionIdentity
from ._type_exploration import ExplorationTaskSpec, RelationshipKind, RepositoryProfileId
from ._type_launch import ResolvedLaunchContract
from ._type_native_shell_capture import ManagedHeadlessSessionLineageRef
from ._type_results import WriteBehaviorSpec

__all__ = [
    "MACHINE_ONLY_SKILL_FRONTMATTER_KEYS",
    "PARENT_SANDBOX_MODES",
    "SKILL_PROJECTION_VERSION",
    "SKILL_SESSION_CONTRACT_SCHEMA_VERSION",
    "ExplorationVectorApplicabilityId",
    "ExplorationVectorDef",
    "ExplorationVectorDisposition",
    "SkillSessionContract",
    "SkillSourceIdentity",
    "SkillSourceRef",
    "SkillVisibilitySpec",
    "StoredSkillSessionContract",
    "normalize_parent_sandbox_mode",
]


MACHINE_ONLY_SKILL_FRONTMATTER_KEYS = frozenset(
    {
        "activate_deps",
        "execution_role",
        "exploration_vectors",
        "uses_capabilities",
    }
)
# Bumped for both package-owned semantic projection and marker-bound exploration
# vectors. Stored contracts also gained typed launch and execution identities.
SKILL_PROJECTION_VERSION = 5
SKILL_SESSION_CONTRACT_SCHEMA_VERSION = 5
PARENT_SANDBOX_MODES: frozenset[str] = frozenset({"read-only", "workspace-write"})
_CANONICAL_IDENTIFIER_RE = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*\Z")
_VECTOR_MARKER_TOKEN = "autoskillit:exploration-vector"
_MAX_VECTOR_RESULTS = 10_000
_MAX_VECTOR_REPORT_BYTES = 10_000_000


class ExplorationVectorApplicabilityId(StrEnum):
    """Closed authoring-time applicability identifiers for exploration vectors."""

    ALWAYS = "always"
    PLANNER_EXTRACT_DOMAIN_DEEP = "planner-extract-domain-deep"
    INVESTIGATE_STANDARD = "investigate-standard"
    INVESTIGATE_DEEP = "investigate-deep"
    SCOPE_SOFTWARE = "scope-software"
    SCOPE_NON_SOFTWARE = "scope-non-software"


class ExplorationVectorDisposition(StrEnum):
    """Reviewed migration outcome for one bounded exploration vector."""

    MIGRATED = "migrated"
    RETAINED = "retained"
    EXCLUDED = "excluded"


def _require_canonical_identifier(value: str, field_name: str) -> None:
    if not isinstance(value, str) or _CANONICAL_IDENTIFIER_RE.fullmatch(value) is None:
        raise SkillContractError(f"{field_name} must be a canonical identifier")


def _normalized_vector_body(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n").strip("\n")


@dataclass(frozen=True, slots=True)
class ExplorationVectorDef:
    """Static reviewed definition bound to one replaceable SKILL.md prose vector."""

    id: str
    disposition: ExplorationVectorDisposition
    rationale: str
    applicability: ExplorationVectorApplicabilityId
    role: str | None
    profile: RepositoryProfileId
    relationship_classes: tuple[RelationshipKind, ...]
    task: ExplorationTaskSpec
    max_results: int
    max_report_bytes: int
    evidence_version: int
    native_dispatch: bool
    body: str = ""

    def __post_init__(self) -> None:
        _require_canonical_identifier(self.id, "exploration vector id")
        _require_canonical_identifier(self.task.task_id, "exploration task id")
        _require_canonical_identifier(
            self.task.frontier_item_id,
            "exploration frontier item id",
        )
        for dependency in self.task.depends_on:
            _require_canonical_identifier(dependency, "exploration task dependency")
        if len(set(self.task.depends_on)) != len(self.task.depends_on):
            raise SkillContractError("exploration task dependencies must be unique")
        if self.task.task_id in self.task.depends_on:
            raise SkillContractError("exploration task cannot depend on itself")
        if self.task.profile is not self.profile:
            raise SkillContractError("exploration vector profile must match its task profile")
        if any(not item.strip() for item in self.task.scope):
            raise SkillContractError("exploration task scope entries must be non-empty")
        if not self.rationale.strip():
            raise SkillContractError("exploration vector rationale must be non-empty")
        if _VECTOR_MARKER_TOKEN in self.rationale:
            raise SkillContractError("exploration vector rationale contains a marker token")
        if self.role is not None:
            _require_canonical_identifier(self.role, "exploration vector role")
        if not self.relationship_classes:
            raise SkillContractError("exploration vector requires relationship classes")
        if len(set(self.relationship_classes)) != len(self.relationship_classes):
            raise SkillContractError("exploration vector relationship classes must be unique")
        if type(self.max_results) is not int or not 0 < self.max_results <= _MAX_VECTOR_RESULTS:
            raise SkillContractError("exploration vector max_results is outside its bound")
        if (
            type(self.max_report_bytes) is not int
            or not 0 < self.max_report_bytes <= _MAX_VECTOR_REPORT_BYTES
        ):
            raise SkillContractError("exploration vector max_report_bytes is outside its bound")
        if type(self.evidence_version) is not int or self.evidence_version < 1:
            raise SkillContractError("exploration vector evidence_version must be positive")
        if type(self.native_dispatch) is not bool:
            raise SkillContractError("exploration vector native_dispatch must be boolean")
        if self.disposition is ExplorationVectorDisposition.MIGRATED:
            if self.role is None or not self.native_dispatch:
                raise SkillContractError(
                    "migrated exploration vectors require a role and native dispatch coverage"
                )
        elif self.role is not None or self.native_dispatch:
            raise SkillContractError(
                "retained and excluded exploration vectors remain prose without native dispatch"
            )
        if _VECTOR_MARKER_TOKEN in self.body or "/autoskillit:exploration-vector" in self.body:
            raise SkillContractError("exploration vector body contains an embedded marker token")

    @property
    def marker_line(self) -> str:
        return f'<!-- autoskillit:exploration-vector id="{self.id}" -->'

    @property
    def digest(self) -> str:
        """Hash the normalized marker/body/task tuple with a domain separator."""
        task = self.task
        payload = [
            self.marker_line,
            _normalized_vector_body(self.body),
            [
                task.task_id,
                task.frontier_item_id,
                task.profile.value,
                list(task.depends_on),
                list(task.scope),
            ],
        ]
        encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("ascii")
        return hashlib.sha256(b"exploration-vector/v1\0" + encoded).hexdigest()


def normalize_parent_sandbox_mode(value: str) -> str:
    """Validate and return one backend-neutral parent sandbox policy."""
    if value not in PARENT_SANDBOX_MODES:
        raise SkillContractError(f"unsupported parent sandbox mode: {value!r}")
    return value


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
    exploration_vectors: Mapping[str, tuple[ExplorationVectorDef, ...]] = field(
        default_factory=dict
    )
    resolved_exploration_profile: RepositoryProfileId | None = None
    active_exploration_applicabilities: frozenset[ExplorationVectorApplicabilityId] = field(
        default_factory=lambda: frozenset({ExplorationVectorApplicabilityId.ALWAYS})
    )
    expected_output_patterns: tuple[str, ...] = ()
    write_behavior: WriteBehaviorSpec = WriteBehaviorSpec()
    read_only: bool = False
    parent_sandbox_mode: str = "workspace-write"
    completion_required: bool = False
    skill_contract_json: str = ""
    projection_substitutions: tuple[tuple[str, str], ...] = ()
    projection_gating: bool | None = None
    projection_namespace: str | None = None
    launch_contract: ResolvedLaunchContract | None = None
    launch_contract_digest: str = ""
    execution_identity: ExecutionIdentity = field(default_factory=ExecutionIdentity.empty)
    schema_version: int = SKILL_SESSION_CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "parent_sandbox_mode",
            normalize_parent_sandbox_mode(self.parent_sandbox_mode),
        )
        expected_parent_sandbox = "read-only" if self.read_only else "workspace-write"
        if self.parent_sandbox_mode != expected_parent_sandbox:
            raise SkillContractError(
                "parent sandbox mode does not match the persisted read_only authority"
            )
        if self.resolved_exploration_profile is RepositoryProfileId.AUTO:
            raise SkillContractError("persisted resolved exploration profile cannot be auto")
        active_applicabilities = frozenset(self.active_exploration_applicabilities)
        if ExplorationVectorApplicabilityId.ALWAYS not in active_applicabilities:
            raise SkillContractError("persisted exploration applicability must include always")
        object.__setattr__(
            self,
            "active_exploration_applicabilities",
            active_applicabilities,
        )
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
        if self.launch_contract is None:
            if self.launch_contract_digest:
                raise SkillContractError("launch digest requires a typed launch contract")
        else:
            digest = self.launch_contract.digest
            if self.launch_contract_digest and self.launch_contract_digest != digest:
                raise SkillContractError("launch contract digest mismatch")
            object.__setattr__(self, "launch_contract_digest", digest)
            if self.launch_contract.effective_backend != self.backend:
                raise SkillContractError("launch contract backend mismatch")
            if self.launch_contract.cwd != self.cwd:
                raise SkillContractError("launch contract cwd mismatch")
        exploration_vectors = self.exploration_vectors or {name: () for name in self.closure}
        object.__setattr__(
            self,
            "exploration_vectors",
            MappingProxyType(
                {name: tuple(vectors) for name, vectors in exploration_vectors.items()}
            ),
        )
        if not isinstance(self.execution_identity, ExecutionIdentity):
            raise SkillContractError("execution identity must be typed")


@dataclass(frozen=True, slots=True)
class StoredSkillSessionContract:
    """Validated contract plus the retained projected snapshot directory."""

    contract: SkillSessionContract
    snapshot_dir: Path
    raw_session_id: str
    managed_lineage_ref: ManagedHeadlessSessionLineageRef | None = None
