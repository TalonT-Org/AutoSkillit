"""Immutable plan-set authority contracts shared by the core and server layers."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Self

from ..audit.closure_hashing import canonical_json_bytes, compute_canonical_hash

__all__ = [
    "ALLOCATION_KIND",
    "PLAN_SET_AUTHORITY_DOMAIN",
    "PLAN_SET_AUTHORITY_ID_DOMAIN",
    "PLAN_SET_MAX_AUTHORITY_BYTES",
    "PLAN_SET_MAX_ISSUE_BYTES",
    "PLAN_SET_MAX_PART_BYTES",
    "PLAN_SET_SCHEMA_VERSION",
    "AllocationKind",
    "AllocationRowDef",
    "AssignedRequirementDef",
    "CoverageResultDef",
    "CoverageStatus",
    "InventoryMode",
    "IssueSnapshotRef",
    "PlanPartRef",
    "PlanSetAuthority",
    "PlanSetBindRequest",
    "PlanSetBindResult",
    "PlanSetBindingMode",
    "PlanSetPreflightEvidence",
    "PlanSetRejectReason",
    "PlanSetState",
    "PlanSetVerification",
    "RequirementDef",
    "RequirementKind",
]

PLAN_SET_SCHEMA_VERSION = 1
PLAN_SET_AUTHORITY_DOMAIN = "autoskillit:plan-set-authority:v1:sha256"
PLAN_SET_AUTHORITY_ID_DOMAIN = "autoskillit:plan-set-authority-id:v1:sha256"
PLAN_SET_MAX_ISSUE_BYTES = 2_000_000
PLAN_SET_MAX_PART_BYTES = 2_000_000
PLAN_SET_MAX_AUTHORITY_BYTES = 2_000_000


class PlanSetState(StrEnum):
    OPEN = "open"
    SEALED = "sealed"


class InventoryMode(StrEnum):
    ENUMERATED = "enumerated"
    UNENUMERATED = "unenumerated"
    NO_ISSUE = "no_issue"


class RequirementKind(StrEnum):
    ITEM = "item"
    CONTAINER = "container"
    PLANNER_DECLARED = "planner_declared"


class AllocationKind(StrEnum):
    OWNED = "owned"
    SHARED = "shared"


# Compatibility with the noun used in the persisted allocation vocabulary.
ALLOCATION_KIND = AllocationKind


class PlanSetBindingMode(StrEnum):
    RECIPE = "recipe"
    STANDALONE = "standalone"


class PlanSetRejectReason(StrEnum):
    AUTHORITY_NOT_CANONICAL = "authority_not_canonical"
    AUTHORITY_INVALID = "authority_invalid"
    AUTHORITY_DIGEST = "authority_digest"
    AUTHORITY_NOT_SEALED = "not_sealed"
    BINDING_MODE = "binding_mode_mismatch"
    COVERAGE_FAILED = "coverage_failed"
    EXECUTION_GENERATION = "execution_mismatch"
    ISSUE_DRIFT = "issue_drift"
    ISSUE_FETCH_FAILED = "issue_fetch_failed"
    KITCHEN_ID = "kitchen_mismatch"
    NO_PARTS = "no_parts"
    PART_CONTENT_CHANGED = "part_digest_mismatch"
    PART_FILE_MISSING = "part_file_missing"
    PART_LIST_CHANGED = "part_list_changed"
    PART_NOT_FOUND = "part_not_found"
    PATH_ESCAPE = "path_escape"
    SYMLINK = "symlink"
    HARDLINK = "hardlink"
    OVERSIZED = "oversized"
    WORLD_WRITABLE = "world_writable"
    METADATA_DRIFT = "metadata_drift"
    SCHEMA_VERSION = "schema_version"
    UNPARSED_MARKERS = "unparsed_markers"
    ALLOCATION_STEP_MISSING = "allocation_step_missing"
    ALLOCATION_STEP_UNREFERENCED = "allocation_step_unreferenced"
    CONTAINMENT = "containment"
    INTERNAL = "internal"


class CoverageStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    NOT_EVALUATED = "not_evaluated"


def _tuple_of(name: str, value: object, item_type: type[Any]) -> tuple[Any, ...]:
    if not isinstance(value, (tuple, list)):
        raise ValueError(f"{name} must be a tuple")
    values = tuple(value)
    if not all(isinstance(item, item_type) for item in values):
        raise ValueError(f"{name} must contain {item_type.__name__} entries")
    return values


def _nonempty(name: str, value: object) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")


@dataclass(frozen=True, slots=True)
class RequirementDef:
    requirement_id: str
    label: str
    kind: RequirementKind
    parent_label: str | None
    text: str
    source_line: int
    text_digest: str

    def __post_init__(self) -> None:
        _nonempty("RequirementDef.requirement_id", self.requirement_id)
        _nonempty("RequirementDef.label", self.label)
        if not isinstance(self.kind, RequirementKind):
            raise ValueError("RequirementDef.kind must be a RequirementKind")
        if self.parent_label is not None:
            _nonempty("RequirementDef.parent_label", self.parent_label)
        if not isinstance(self.text, str):
            raise ValueError("RequirementDef.text must be a string")
        if isinstance(self.source_line, bool) or self.source_line < 1:
            raise ValueError("RequirementDef.source_line must be positive")
        _nonempty("RequirementDef.text_digest", self.text_digest)

    def to_dict(self) -> dict[str, Any]:
        return {
            "requirement_id": self.requirement_id,
            "label": self.label,
            "kind": self.kind.value,
            "parent_label": self.parent_label,
            "text": self.text,
            "source_line": self.source_line,
            "text_digest": self.text_digest,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(
            requirement_id=data["requirement_id"],
            label=data["label"],
            kind=RequirementKind(data["kind"]),
            parent_label=data["parent_label"],
            text=data["text"],
            source_line=data["source_line"],
            text_digest=data["text_digest"],
        )


@dataclass(frozen=True, slots=True)
class PlanPartRef:
    ordinal: int
    part_key: str
    part_suffix: str
    locator: str
    byte_size: int
    content_digest: str

    def __post_init__(self) -> None:
        if isinstance(self.ordinal, bool) or self.ordinal < 1:
            raise ValueError("PlanPartRef.ordinal must be positive")
        if self.part_key != f"P{self.ordinal}":
            raise ValueError("PlanPartRef.part_key must match ordinal")
        for name in ("part_key", "part_suffix", "locator", "content_digest"):
            _nonempty(f"PlanPartRef.{name}", getattr(self, name))
        if isinstance(self.byte_size, bool) or self.byte_size < 0:
            raise ValueError("PlanPartRef.byte_size must be non-negative")

    def to_dict(self) -> dict[str, Any]:
        return {
            "ordinal": self.ordinal,
            "part_key": self.part_key,
            "part_suffix": self.part_suffix,
            "locator": self.locator,
            "byte_size": self.byte_size,
            "content_digest": self.content_digest,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(**{name: data[name] for name in cls.__dataclass_fields__})


@dataclass(frozen=True, slots=True)
class AllocationRowDef:
    requirement_id: str
    part_key: str
    allocation: AllocationKind
    implementation_step: str

    def __post_init__(self) -> None:
        for name in ("requirement_id", "part_key", "implementation_step"):
            _nonempty(f"AllocationRowDef.{name}", getattr(self, name))
        if not isinstance(self.allocation, AllocationKind):
            raise ValueError("AllocationRowDef.allocation must be an AllocationKind")

    def to_dict(self) -> dict[str, Any]:
        return {
            "requirement_id": self.requirement_id,
            "part_key": self.part_key,
            "allocation": self.allocation.value,
            "implementation_step": self.implementation_step,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(
            requirement_id=data["requirement_id"],
            part_key=data["part_key"],
            allocation=AllocationKind(data["allocation"]),
            implementation_step=data["implementation_step"],
        )


@dataclass(frozen=True, slots=True)
class AssignedRequirementDef:
    requirement_id: str
    label: str
    kind: RequirementKind
    text: str
    source_line: int
    allocation: AllocationKind
    implementation_step: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "requirement_id": self.requirement_id,
            "label": self.label,
            "kind": self.kind.value,
            "text": self.text,
            "source_line": self.source_line,
            "allocation": self.allocation.value,
            "implementation_step": self.implementation_step,
        }


@dataclass(frozen=True, slots=True)
class CoverageResultDef:
    status: CoverageStatus
    unassigned: tuple[str, ...] = ()
    uncovered: tuple[str, ...] = ()
    duplicate: tuple[str, ...] = ()
    unknown: tuple[str, ...] = ()
    container_allocated: tuple[str, ...] = ()
    parts_without_obligations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.status, CoverageStatus):
            raise ValueError("CoverageResultDef.status must be a CoverageStatus")
        for name in self.__dataclass_fields__:
            if name != "status":
                object.__setattr__(self, name, tuple(getattr(self, name)))

    def to_dict(self) -> dict[str, Any]:
        return {
            name: list(getattr(self, name)) if name != "status" else self.status.value
            for name in self.__dataclass_fields__
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(
            status=CoverageStatus(data["status"]),
            **{
                name: tuple(data.get(name, ()))
                for name in cls.__dataclass_fields__
                if name != "status"
            },
        )


@dataclass(frozen=True, slots=True)
class IssueSnapshotRef:
    issue_url: str
    issue_number: int | None
    locator: str
    byte_size: int
    content_digest: str
    fetched_at: str

    def __post_init__(self) -> None:
        for name in ("issue_url", "locator", "content_digest", "fetched_at"):
            _nonempty(f"IssueSnapshotRef.{name}", getattr(self, name))
        if self.issue_number is not None and self.issue_number < 1:
            raise ValueError("IssueSnapshotRef.issue_number must be positive")
        if self.byte_size < 0:
            raise ValueError("IssueSnapshotRef.byte_size must be non-negative")

    def to_dict(self) -> dict[str, Any]:
        return {
            "issue_url": self.issue_url,
            "issue_number": self.issue_number,
            "locator": self.locator,
            "byte_size": self.byte_size,
            "content_digest": self.content_digest,
            "fetched_at": self.fetched_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        return cls(**{name: data[name] for name in cls.__dataclass_fields__})


@dataclass(frozen=True, slots=True)
class PlanSetPreflightEvidence:
    status: str
    plan_set_authority_path: str
    plan_set_authority_digest: str
    plan_set_authority_id: str
    revision: int
    part_key: str | None
    part_ordinal: int | None
    part_count: int
    part_suffix: str | None
    plan_set_state: PlanSetState
    coverage_status: str
    assigned_requirements: tuple[AssignedRequirementDef, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "plan_set_authority_path": self.plan_set_authority_path,
            "plan_set_authority_digest": self.plan_set_authority_digest,
            "plan_set_authority_id": self.plan_set_authority_id,
            "revision": self.revision,
            "part_key": self.part_key,
            "part_ordinal": self.part_ordinal,
            "part_count": self.part_count,
            "part_suffix": self.part_suffix,
            "plan_set_state": self.plan_set_state.value,
            "coverage_status": self.coverage_status,
            "assigned_requirements": [item.to_dict() for item in self.assigned_requirements],
        }


@dataclass(frozen=True, slots=True)
class PlanSetAuthority:
    schema_version: int
    binding_mode: PlanSetBindingMode
    execution_generation: str
    kitchen_id: str
    dispatch_id: str
    plan_set_authority_id: str
    revision: int
    parent_authority_digest: str | None
    state: PlanSetState
    inventory_mode: InventoryMode
    issue: IssueSnapshotRef | None
    requirements: tuple[RequirementDef, ...]
    parts: tuple[PlanPartRef, ...]
    allocations: tuple[AllocationRowDef, ...]
    coverage: CoverageResultDef
    unparsed_marker_lines: tuple[int, ...]
    generated_at: str
    authority_digest: str

    def __post_init__(self) -> None:
        if self.schema_version != PLAN_SET_SCHEMA_VERSION:
            raise ValueError(f"PlanSetAuthority.schema_version must be {PLAN_SET_SCHEMA_VERSION}")
        for name in ("plan_set_authority_id", "generated_at", "authority_digest"):
            _nonempty(f"PlanSetAuthority.{name}", getattr(self, name))
        if not isinstance(self.execution_generation, str) or not isinstance(self.kitchen_id, str):
            raise ValueError("PlanSetAuthority execution identity fields must be strings")
        if not isinstance(self.binding_mode, PlanSetBindingMode):
            raise ValueError("PlanSetAuthority.binding_mode is invalid")
        if self.binding_mode is PlanSetBindingMode.RECIPE and not self.execution_generation:
            raise ValueError("recipe-bound authority requires an execution generation")
        if not isinstance(self.state, PlanSetState) or not isinstance(
            self.inventory_mode, InventoryMode
        ):
            raise ValueError("PlanSetAuthority state or inventory mode is invalid")
        if isinstance(self.revision, bool) or self.revision < 1:
            raise ValueError("PlanSetAuthority.revision must be positive")
        if self.revision > 1 and not self.parent_authority_digest:
            raise ValueError("later authority revision requires a parent digest")
        if self.parent_authority_digest is not None:
            _nonempty("PlanSetAuthority.parent_authority_digest", self.parent_authority_digest)
        if self.issue is not None and not isinstance(self.issue, IssueSnapshotRef):
            raise ValueError("PlanSetAuthority.issue is invalid")
        for name, item_type in (
            ("requirements", RequirementDef),
            ("parts", PlanPartRef),
            ("allocations", AllocationRowDef),
        ):
            object.__setattr__(
                self, name, _tuple_of(f"PlanSetAuthority.{name}", getattr(self, name), item_type)
            )
        object.__setattr__(self, "unparsed_marker_lines", tuple(self.unparsed_marker_lines))
        if not isinstance(self.coverage, CoverageResultDef):
            raise ValueError("PlanSetAuthority.coverage is invalid")
        if self.state is PlanSetState.SEALED and self.coverage.status is not CoverageStatus.PASS:
            raise ValueError("sealed authority requires passing coverage")
        if tuple(part.ordinal for part in self.parts) != tuple(range(1, len(self.parts) + 1)):
            raise ValueError("PlanSetAuthority.parts must have consecutive ordinals")
        if len({Path(part.locator).resolve() for part in self.parts}) != len(self.parts):
            raise ValueError("PlanSetAuthority.parts contain duplicate locators")
        if self.authority_digest != self.compute_digest():
            raise ValueError("PlanSetAuthority.authority_digest does not match event content")

    @classmethod
    def create(
        cls,
        *,
        binding_mode: PlanSetBindingMode,
        execution_generation: str,
        kitchen_id: str,
        dispatch_id: str,
        plan_set_authority_id: str,
        revision: int,
        parent_authority_digest: str | None,
        state: PlanSetState,
        inventory_mode: InventoryMode,
        issue: IssueSnapshotRef | None,
        requirements: tuple[RequirementDef, ...],
        parts: tuple[PlanPartRef, ...],
        allocations: tuple[AllocationRowDef, ...],
        coverage: CoverageResultDef,
        unparsed_marker_lines: tuple[int, ...],
        generated_at: str,
    ) -> Self:
        if not isinstance(binding_mode, PlanSetBindingMode):
            raise ValueError("PlanSetAuthority.binding_mode is invalid")
        payload = {
            "schema_version": PLAN_SET_SCHEMA_VERSION,
            "binding_mode": binding_mode.value,
            "execution_generation": execution_generation,
            "kitchen_id": kitchen_id,
            "dispatch_id": dispatch_id,
            "plan_set_authority_id": plan_set_authority_id,
            "revision": revision,
            "parent_authority_digest": parent_authority_digest,
            "state": state.value,
            "inventory_mode": inventory_mode.value,
            "issue": issue.to_dict() if issue else None,
            "requirements": [item.to_dict() for item in requirements],
            "parts": [item.to_dict() for item in parts],
            "allocations": [item.to_dict() for item in allocations],
            "coverage": coverage.to_dict(),
            "unparsed_marker_lines": list(unparsed_marker_lines),
            "generated_at": generated_at,
        }
        digest = compute_canonical_hash(payload, domain=PLAN_SET_AUTHORITY_DOMAIN)
        return cls(
            schema_version=PLAN_SET_SCHEMA_VERSION,
            binding_mode=binding_mode,
            execution_generation=execution_generation,
            kitchen_id=kitchen_id,
            dispatch_id=dispatch_id,
            plan_set_authority_id=plan_set_authority_id,
            revision=revision,
            parent_authority_digest=parent_authority_digest,
            state=state,
            inventory_mode=inventory_mode,
            issue=issue,
            requirements=requirements,
            parts=parts,
            allocations=allocations,
            coverage=coverage,
            unparsed_marker_lines=unparsed_marker_lines,
            generated_at=generated_at,
            authority_digest=digest,
        )

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_dict())

    def compute_digest(self) -> str:
        return compute_canonical_hash(
            self.to_dict(include_digest=False), domain=PLAN_SET_AUTHORITY_DOMAIN
        )

    def to_dict(self, *, include_digest: bool = True) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema_version": self.schema_version,
            "binding_mode": self.binding_mode.value,
            "execution_generation": self.execution_generation,
            "kitchen_id": self.kitchen_id,
            "dispatch_id": self.dispatch_id,
            "plan_set_authority_id": self.plan_set_authority_id,
            "revision": self.revision,
            "parent_authority_digest": self.parent_authority_digest,
            "state": self.state.value,
            "inventory_mode": self.inventory_mode.value,
            "issue": self.issue.to_dict() if self.issue else None,
            "requirements": [item.to_dict() for item in self.requirements],
            "parts": [item.to_dict() for item in self.parts],
            "allocations": [item.to_dict() for item in self.allocations],
            "coverage": self.coverage.to_dict(),
            "unparsed_marker_lines": list(self.unparsed_marker_lines),
            "generated_at": self.generated_at,
        }
        if include_digest:
            result["authority_digest"] = self.authority_digest
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        try:
            issue = data["issue"]
            return cls(
                schema_version=data["schema_version"],
                binding_mode=PlanSetBindingMode(data["binding_mode"]),
                execution_generation=data["execution_generation"],
                kitchen_id=data["kitchen_id"],
                dispatch_id=data["dispatch_id"],
                plan_set_authority_id=data["plan_set_authority_id"],
                revision=data["revision"],
                parent_authority_digest=data["parent_authority_digest"],
                state=PlanSetState(data["state"]),
                inventory_mode=InventoryMode(data["inventory_mode"]),
                issue=IssueSnapshotRef.from_dict(issue) if issue is not None else None,
                requirements=tuple(
                    RequirementDef.from_dict(item) for item in data["requirements"]
                ),
                parts=tuple(PlanPartRef.from_dict(item) for item in data["parts"]),
                allocations=tuple(
                    AllocationRowDef.from_dict(item) for item in data["allocations"]
                ),
                coverage=CoverageResultDef.from_dict(data["coverage"]),
                unparsed_marker_lines=tuple(data["unparsed_marker_lines"]),
                generated_at=data["generated_at"],
                authority_digest=data["authority_digest"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid PlanSetAuthority: {exc}") from exc


@dataclass(frozen=True, slots=True)
class PlanSetVerification:
    accepted: bool
    reason: PlanSetRejectReason | None
    detail: tuple[str, ...]
    authority: PlanSetAuthority | None
    evidence: PlanSetPreflightEvidence | None


@dataclass(frozen=True, slots=True)
class PlanSetBindRequest:
    plan_parts_raw: str
    allowed_root: Path
    issue_url: str
    parent_authority_path: str
    seal: bool
    step_name: str
    execution_generation: str
    kitchen_id: str
    dispatch_id: str
    binding_mode: PlanSetBindingMode


@dataclass(frozen=True, slots=True)
class PlanSetBindResult:
    success: bool
    reason: PlanSetRejectReason | None
    error: str
    plan_set_authority_path: str = ""
    plan_set_authority_digest: str = ""
    plan_set_authority_id: str = ""
    coverage: CoverageResultDef = field(
        default_factory=lambda: CoverageResultDef(CoverageStatus.NOT_EVALUATED)
    )
    plan_set_parts: str = ""
    plan_set_state: str = ""
    coverage_status: str = ""
    unassigned: tuple[str, ...] = ()
    uncovered: tuple[str, ...] = ()
    duplicate: tuple[str, ...] = ()
    unknown: tuple[str, ...] = ()
    container_allocated: tuple[str, ...] = ()
    parts_without_obligations: tuple[str, ...] = ()
    first_part_path: str = ""
    coverage_gaps: str = ""

    def to_dict(self) -> dict[str, Any]:
        result = {name: getattr(self, name) for name in self.__dataclass_fields__}
        result["reason"] = self.reason.value if self.reason else ""
        for name in (
            "unassigned",
            "uncovered",
            "duplicate",
            "unknown",
            "container_allocated",
            "parts_without_obligations",
        ):
            result[name] = list(getattr(self, name))
        result["coverage"] = self.coverage.to_dict()
        return result
