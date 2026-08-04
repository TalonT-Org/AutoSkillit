"""Backend-neutral semantic requirements declared by portable skills."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from hashlib import sha256
from types import MappingProxyType

from ._type_exceptions import SkillContractError

__all__ = [
    "SKILL_MODEL_CLASS_REGISTRY",
    "SKILL_REASONING_EFFORTS",
    "SKILL_SEMANTIC_SCHEMA_VERSION",
    "ChildModelPolicySpec",
    "ChildSpawnSpec",
    "ConcurrencySpec",
    "EvidenceSpec",
    "GitMetadataWriteSpec",
    "JoinSpec",
    "LogicalRoleSpec",
    "SiblingSkillSpec",
    "SkillSemanticAdaptationResult",
    "SkillModelClassDef",
    "SkillSemanticOperation",
    "SkillSemanticPlan",
]


@dataclass(frozen=True, slots=True)
class SkillModelClassDef:
    """Static definition of one backend-neutral logical model class."""

    description: str


SKILL_MODEL_CLASS_REGISTRY: Mapping[str, SkillModelClassDef] = MappingProxyType(
    {
        "haiku": SkillModelClassDef(
            description="Lightweight logical class for focused delegated work",
        ),
        "sonnet": SkillModelClassDef(
            description="Balanced logical class for general delegated work",
        ),
        "opus": SkillModelClassDef(
            description="Highest-capability logical class for demanding delegated work",
        ),
    }
)

SKILL_REASONING_EFFORTS: frozenset[str] = frozenset({"medium", "high", "xhigh"})

SKILL_SEMANTIC_SCHEMA_VERSION = 1


class SkillSemanticOperation(StrEnum):
    """Closed portable operations a coding-agent backend may adapt."""

    CHILD_SPAWN = "child_spawn"
    REQUIRED_CONCURRENCY = "required_concurrency"
    REQUIRED_JOIN = "required_join"
    REQUIRED_EVIDENCE = "required_evidence"
    CHILD_MODEL_POLICY = "child_model_policy"
    LOGICAL_ROLE = "logical_role"
    SIBLING_SKILL_INVOKE = "sibling_skill_invoke"
    GIT_METADATA_WRITE = "git_metadata_write"


def _require_nonempty(value: str, field_name: str) -> None:
    if not value.strip():
        raise SkillContractError(f"{field_name} must be non-empty")


@dataclass(frozen=True, slots=True)
class ChildSpawnSpec:
    """Spawn one or more children that perform a named logical role."""

    role: str
    count: int = 1

    def __post_init__(self) -> None:
        _require_nonempty(self.role, "child spawn role")
        if self.count < 1:
            raise SkillContractError("child spawn count must be positive")


@dataclass(frozen=True, slots=True)
class ConcurrencySpec:
    """Whether the declared child work must overlap in time."""

    required: bool


@dataclass(frozen=True, slots=True)
class JoinSpec:
    """Whether every declared child must be joined before synthesis."""

    required: bool


@dataclass(frozen=True, slots=True)
class EvidenceSpec:
    """Evidence boundary the parent requires from child work."""

    required: bool
    independent: bool = False

    def __post_init__(self) -> None:
        if self.independent and not self.required:
            raise SkillContractError("independent evidence requires evidence collection")


@dataclass(frozen=True, slots=True)
class ChildModelPolicySpec:
    """Semantic model-class and reasoning-effort policy for one logical role."""

    role: str
    model_class: str | None = None
    reasoning_effort: str | None = None

    def __post_init__(self) -> None:
        _require_nonempty(self.role, "child model policy role")
        if self.model_class is not None and self.model_class not in SKILL_MODEL_CLASS_REGISTRY:
            raise SkillContractError(
                f"unknown semantic model class {self.model_class!r}; "
                f"expected one of {sorted(SKILL_MODEL_CLASS_REGISTRY)}"
            )
        if (
            self.reasoning_effort is not None
            and self.reasoning_effort not in SKILL_REASONING_EFFORTS
        ):
            raise SkillContractError(
                f"unknown semantic reasoning effort {self.reasoning_effort!r}; "
                f"expected one of {sorted(SKILL_REASONING_EFFORTS)}"
            )
        if self.model_class is None and self.reasoning_effort is None:
            raise SkillContractError("child model policy must constrain model class or effort")


@dataclass(frozen=True, slots=True)
class LogicalRoleSpec:
    """Backend-neutral name and purpose for delegated child work."""

    name: str
    purpose: str

    def __post_init__(self) -> None:
        _require_nonempty(self.name, "logical role name")
        _require_nonempty(self.purpose, "logical role purpose")


@dataclass(frozen=True, slots=True)
class SiblingSkillSpec:
    """Invoke another logical skill through the selected backend's native sigil."""

    name: str

    def __post_init__(self) -> None:
        _require_nonempty(self.name, "sibling skill name")


@dataclass(frozen=True, slots=True)
class GitMetadataWriteSpec:
    """Semantically required write to repository metadata."""

    purpose: str

    def __post_init__(self) -> None:
        _require_nonempty(self.purpose, "git metadata write purpose")


@dataclass(frozen=True, slots=True)
class SkillSemanticPlan:
    """One versioned, backend-neutral portable skill requirement plan."""

    schema_version: int
    child_spawns: tuple[ChildSpawnSpec, ...] = ()
    concurrency: ConcurrencySpec | None = None
    join: JoinSpec | None = None
    evidence: EvidenceSpec | None = None
    child_model_policies: tuple[ChildModelPolicySpec, ...] = ()
    logical_roles: tuple[LogicalRoleSpec, ...] = ()
    sibling_skills: tuple[SiblingSkillSpec, ...] = ()
    git_metadata_writes: tuple[GitMetadataWriteSpec, ...] = ()

    def __post_init__(self) -> None:
        if self.schema_version != SKILL_SEMANTIC_SCHEMA_VERSION:
            raise SkillContractError(
                f"unsupported skill semantic schema version {self.schema_version!r}; "
                f"replace with schema version {SKILL_SEMANTIC_SCHEMA_VERSION}"
            )
        role_names = [role.name for role in self.logical_roles]
        if len(role_names) != len(set(role_names)):
            raise SkillContractError("logical role names must be unique")
        declared_roles = frozenset(role_names)
        referenced_roles = {
            *(spawn.role for spawn in self.child_spawns),
            *(policy.role for policy in self.child_model_policies),
        }
        unknown = referenced_roles - declared_roles
        if unknown:
            raise SkillContractError(
                f"semantic plan references unknown logical role: {sorted(unknown)}"
            )

    @property
    def operations(self) -> frozenset[SkillSemanticOperation]:
        operations: set[SkillSemanticOperation] = set()
        if self.child_spawns:
            operations.add(SkillSemanticOperation.CHILD_SPAWN)
        if self.concurrency is not None and self.concurrency.required:
            operations.add(SkillSemanticOperation.REQUIRED_CONCURRENCY)
        if self.join is not None and self.join.required:
            operations.add(SkillSemanticOperation.REQUIRED_JOIN)
        if self.evidence is not None and self.evidence.required:
            operations.add(SkillSemanticOperation.REQUIRED_EVIDENCE)
        if self.child_model_policies:
            operations.add(SkillSemanticOperation.CHILD_MODEL_POLICY)
        if self.logical_roles:
            operations.add(SkillSemanticOperation.LOGICAL_ROLE)
        if self.sibling_skills:
            operations.add(SkillSemanticOperation.SIBLING_SKILL_INVOKE)
        if self.git_metadata_writes:
            operations.add(SkillSemanticOperation.GIT_METADATA_WRITE)
        return frozenset(operations)

    @property
    def canonical_payload(self) -> Mapping[str, object]:
        """Return the complete machine-readable portable authority."""
        payload: dict[str, object] = {
            "schema_version": self.schema_version,
            "child_spawns": tuple(
                {"role": item.role, "count": item.count} for item in self.child_spawns
            ),
            "concurrency": (
                {"required": self.concurrency.required} if self.concurrency is not None else None
            ),
            "join": {"required": self.join.required} if self.join is not None else None,
            "evidence": (
                {
                    "required": self.evidence.required,
                    "independent": self.evidence.independent,
                }
                if self.evidence is not None
                else None
            ),
            "child_model_policies": tuple(
                {
                    "role": item.role,
                    "model_class": item.model_class,
                    "reasoning_effort": item.reasoning_effort,
                }
                for item in self.child_model_policies
            ),
            "logical_roles": tuple(
                {"name": item.name, "purpose": item.purpose} for item in self.logical_roles
            ),
            "sibling_skills": tuple({"name": item.name} for item in self.sibling_skills),
            "git_metadata_writes": tuple(
                {"purpose": item.purpose} for item in self.git_metadata_writes
            ),
        }
        return MappingProxyType(payload)

    @property
    def digest(self) -> str:
        return sha256(
            json.dumps(
                dict(self.canonical_payload),
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class SkillSemanticAdaptationResult:
    """Selected backend's exact adaptation or one unsupported-operation refusal."""

    instruction_fragments: tuple[str, ...] = ()
    logical_role_mapping: Mapping[str, str] = field(default_factory=dict)
    sibling_skill_targets: Mapping[str, str] = field(default_factory=dict)
    model_effort_policy: Mapping[str, tuple[str, str | None]] = field(default_factory=dict)
    unsupported_operation: SkillSemanticOperation | None = None
    diagnostic: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "logical_role_mapping", MappingProxyType(dict(self.logical_role_mapping))
        )
        object.__setattr__(
            self, "sibling_skill_targets", MappingProxyType(dict(self.sibling_skill_targets))
        )
        object.__setattr__(
            self, "model_effort_policy", MappingProxyType(dict(self.model_effort_policy))
        )
        if (self.unsupported_operation is None) != (self.diagnostic is None):
            raise SkillContractError(
                "unsupported semantic operation and diagnostic must be declared together"
            )
        if self.unsupported_operation is not None and (
            self.instruction_fragments
            or self.logical_role_mapping
            or self.sibling_skill_targets
            or self.model_effort_policy
        ):
            raise SkillContractError("unsupported semantic adaptation cannot carry instructions")
        if any(not fragment.strip() for fragment in self.instruction_fragments):
            raise SkillContractError("semantic adaptation instructions must be non-empty")
        for field_name, mapping in (
            ("logical role mapping", self.logical_role_mapping),
            ("sibling skill targets", self.sibling_skill_targets),
        ):
            if any(not key.strip() or not value.strip() for key, value in mapping.items()):
                raise SkillContractError(f"{field_name} must map non-empty strings")
        for role, policy in self.model_effort_policy.items():
            if not role.strip() or not isinstance(policy, tuple) or len(policy) != 2:
                raise SkillContractError("model effort policy must use role -> (model, effort)")
            model, effort = policy
            if not isinstance(model, str) or (effort is not None and not isinstance(effort, str)):
                raise SkillContractError("model effort policy values must be strings")
            if effort is not None and effort not in SKILL_REASONING_EFFORTS:
                raise SkillContractError(f"unknown adapted reasoning effort {effort!r}")

    @property
    def canonical_payload(self) -> Mapping[str, object]:
        return MappingProxyType(
            {
                "instruction_fragments": self.instruction_fragments,
                "logical_role_mapping": dict(sorted(self.logical_role_mapping.items())),
                "sibling_skill_targets": dict(sorted(self.sibling_skill_targets.items())),
                "model_effort_policy": {
                    key: value for key, value in sorted(self.model_effort_policy.items())
                },
                "unsupported_operation": (
                    self.unsupported_operation.value
                    if self.unsupported_operation is not None
                    else None
                ),
                "diagnostic": self.diagnostic,
            }
        )

    @property
    def digest(self) -> str:
        return sha256(
            json.dumps(
                dict(self.canonical_payload),
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()

    def validate_refusal_for(
        self,
        plan: SkillSemanticPlan,
        *,
        backend: str,
    ) -> SkillSemanticOperation | None:
        """Return a refusal's structured authority after validating it against the plan."""
        operation = self.unsupported_operation
        if operation is None:
            if self.diagnostic is not None:
                raise SkillContractError("supported semantic adaptation cannot carry a diagnostic")
            return None
        if operation not in plan.operations:
            raise SkillContractError(
                f"backend {backend!r} reported unsupported semantic operation "
                f"{operation.value!r} not declared by the semantic plan"
            )
        return operation

    def validate_for(self, plan: SkillSemanticPlan, *, backend: str) -> None:
        """Fail closed unless every declared semantic field has one observable adaptation."""
        if self.validate_refusal_for(plan, backend=backend) is not None:
            raise SkillContractError(self.diagnostic or "unsupported skill semantics")
        logical_names = {role.name for role in plan.logical_roles}
        if set(self.logical_role_mapping) != logical_names:
            raise SkillContractError("semantic adaptation logical role mapping is incomplete")
        native_roles = tuple(self.logical_role_mapping.values())
        if len(native_roles) != len(set(native_roles)):
            raise SkillContractError("semantic adaptation maps multiple logical roles to one role")
        sibling_names = {sibling.name for sibling in plan.sibling_skills}
        if set(self.sibling_skill_targets) != sibling_names:
            raise SkillContractError("semantic adaptation sibling target mapping is incomplete")
        expected_policy_roles = {
            self.logical_role_mapping[policy.role] for policy in plan.child_model_policies
        }
        if set(self.model_effort_policy) != expected_policy_roles:
            raise SkillContractError("semantic adaptation model/effort policy is incomplete")
        policies_by_role = {policy.role: policy for policy in plan.child_model_policies}
        for logical_role, policy in policies_by_role.items():
            native_role = self.logical_role_mapping[logical_role]
            model, effort = self.model_effort_policy[native_role]
            if policy.model_class is not None and not model:
                raise SkillContractError(
                    f"semantic adaptation did not resolve model class for {logical_role!r}"
                )
            if policy.reasoning_effort is not None and effort != policy.reasoning_effort:
                raise SkillContractError(
                    f"semantic adaptation changed required effort for {logical_role!r}"
                )
        if plan.operations and not self.instruction_fragments:
            raise SkillContractError("semantic adaptation omitted observable instructions")

    @classmethod
    def unsupported(
        cls,
        *,
        backend: str,
        operation: SkillSemanticOperation,
    ) -> SkillSemanticAdaptationResult:
        return cls(
            unsupported_operation=operation,
            diagnostic=(
                f"backend {backend!r} does not support skill semantic operation "
                f"{operation.value!r}"
            ),
        )
