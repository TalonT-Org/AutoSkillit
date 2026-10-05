"""Primitive child and logical role specifications for semantic skill plans."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from ..foundation._type_exceptions import (
    ChildSpawnCardinalityError,
    LogicalRoleNameError,
    SkillContractError,
)

__all__ = [
    "AGENT_NAME_PATTERN",
    "SKILL_MODEL_CLASS_REGISTRY",
    "SKILL_REASONING_EFFORTS",
    "ChildModelPolicySpec",
    "ChildSpawnSpec",
    "DELEGATED_WORKER_ROLE",
    "LogicalRoleSpec",
    "SkillModelClassDef",
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

SKILL_REASONING_EFFORTS: frozenset[str] = frozenset({"medium", "high"})
DELEGATED_WORKER_ROLE: Final = "delegated-worker"
AGENT_NAME_PATTERN: Final = re.compile(r"^[a-z][a-z0-9-]*$")


def _require_nonempty(value: str, field_name: str) -> None:
    if not value.strip():
        raise SkillContractError(f"{field_name} must be non-empty")


@dataclass(frozen=True, slots=True)
class ChildSpawnSpec:
    """Spawn one or more children that perform a named logical role."""

    role: str
    count: int | None = None
    for_each: str | None = None

    def __post_init__(self) -> None:
        _require_nonempty(self.role, "child spawn role")
        has_count = self.count is not None
        has_for_each = self.for_each is not None
        if has_count == has_for_each:
            raise ChildSpawnCardinalityError(
                "child spawn requires exactly one cardinality authority: "
                "count: <positive integer> or for_each: <runtime collection>"
            )
        if self.count is not None:
            if type(self.count) is not int or self.count <= 0:
                raise ChildSpawnCardinalityError(
                    "child spawn count must be a positive integer; use "
                    "count: <positive integer> or for_each: <runtime collection>"
                )
        if has_for_each and (not isinstance(self.for_each, str) or not self.for_each.strip()):
            raise ChildSpawnCardinalityError(
                "child spawn for_each must be a non-empty runtime collection name; use "
                "count: <positive integer> or for_each: <runtime collection>"
            )


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
    runtime_bound: bool = False

    def __post_init__(self) -> None:
        _require_nonempty(self.name, "logical role name")
        _require_nonempty(self.purpose, "logical role purpose")
        if type(self.runtime_bound) is not bool:
            raise SkillContractError("logical role runtime_bound must be a boolean")
        if AGENT_NAME_PATTERN.fullmatch(self.name) is None:
            raise LogicalRoleNameError(
                f"logical role name {self.name!r} must match {AGENT_NAME_PATTERN.pattern}; "
                f"name the bare agent definition or {DELEGATED_WORKER_ROLE!r} and let the "
                "backend add its native namespace"
            )
        if self.runtime_bound and self.name == DELEGATED_WORKER_ROLE:
            raise SkillContractError("delegated worker role cannot be runtime-bound")
