"""Immutable recipe-execution attestation and admission contracts.

This module is IL-0 and stdlib-only.  It deliberately separates invocation
template identity from runtime-bound values and from the final delivery payload
identity, preventing a digest embedded in a payload from depending on that
payload's eventual hash.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol, TypedDict, runtime_checkable

from ..closure_hashing import HASH_RE, compute_canonical_hash
from ._type_audit_admission import InstallationVersion
from ._type_audit_admission_ledger import AuditAdmissionLedger
from ._type_audit_cycle import InventoryAdmissionDecision
from ._type_recipe_binding import (
    AbsentBoundValue,
    BoundScalar,
    BoundStepInvocation,
    BoundValue,
)

__all__ = [
    "InputPreflightResolver",
    "InstalledRecipeExecution",
    "InvocationTemplate",
    "PreflightEvidence",
    "PreflightKind",
    "RECIPE_EXECUTION_CREDENTIAL_WIRE_FIELDS",
    "RECIPE_EXECUTION_CREDENTIAL_WIRE_KEY",
    "RUN_SKILL_ATTESTATION_PARAMS",
    "RecipeExecutionCredential",
    "RecipeExecutionFactory",
    "RecipeExecutionLock",
    "RecipeExecutionSnapshot",
    "VerifiedInputPreflightRequest",
    "VerifiedInputPreflightResult",
    "build_recipe_execution_credential",
    "compute_audit_slot_intent_digest",
    "compute_invocation_template_digest",
    "compute_recipe_execution_snapshot_digest",
    "compute_runtime_binding_digest",
]

_AUDIT_SLOT_INTENT_DOMAIN = "autoskillit:audit-slot-intent:v1:sha256"
_INVOCATION_TEMPLATE_DOMAIN = "autoskillit:recipe-invocation-template:v1:sha256"
_RECIPE_EXECUTION_SNAPSHOT_DOMAIN = "autoskillit:recipe-execution-snapshot:v1:sha256"
_RUNTIME_BINDING_DOMAIN = "autoskillit:recipe-runtime-binding:v1:sha256"


@runtime_checkable
class RecipeExecutionLock(Protocol):
    """Context-manager contract for atomic recipe-execution state changes."""

    def __enter__(self) -> Any: ...

    def __exit__(self, *args: Any) -> Any: ...


def _bound_scalar_payload(value: object | AbsentBoundValue) -> object:
    if isinstance(value, AbsentBoundValue):
        return {"absent": True}
    return value


def _bound_value_payload(value: BoundValue) -> dict[str, object]:
    return {
        "context_dependencies": list(value.context_dependencies),
        "declared_value": _bound_scalar_payload(value.declared_value),
        "effective_value": _bound_scalar_payload(value.effective_value),
        "input_dependencies": list(value.input_dependencies),
        "name": value.name,
        "origin": value.origin.value,
        "state": value.state.value,
        "template_dependencies": list(value.template_dependencies),
        "absence_value": value.absence_value,
    }


def compute_invocation_template_digest(
    *,
    execution_id: str,
    recipe_name: str,
    content_hash: str,
    composite_hash: str,
    invocation: BoundStepInvocation,
    tool_contract_identity: str,
    skill_contract_identity: str,
) -> str:
    """Hash one immutable invocation template under its own domain."""
    payload = {
        "composite_hash": composite_hash,
        "content_hash": content_hash,
        "execution_id": execution_id,
        "mcp_kwargs": [_bound_value_payload(value) for value in invocation.mcp_kwargs],
        "recipe_name": recipe_name,
        "skill_contract_identity": skill_contract_identity,
        "skill_inputs": [_bound_value_payload(value) for value in invocation.skill_inputs],
        "skill_name": invocation.skill_name,
        "step_name": invocation.step_name,
        "tool_contract_identity": tool_contract_identity,
        "tool_name": invocation.tool_name,
    }
    return compute_canonical_hash(payload, domain=_INVOCATION_TEMPLATE_DOMAIN)


@dataclass(frozen=True, slots=True)
class InvocationTemplate:
    invocation: BoundStepInvocation
    tool_contract_identity: str
    skill_contract_identity: str
    template_digest: str


def compute_recipe_execution_snapshot_digest(
    *,
    execution_id: str,
    recipe_name: str,
    content_hash: str,
    composite_hash: str,
    templates: Mapping[str, InvocationTemplate],
    dynamic_skill_step_names: frozenset[str] = frozenset(),
) -> str:
    """Hash the compiled execution snapshot without any delivery hash."""
    payload = {
        "composite_hash": composite_hash,
        "content_hash": content_hash,
        "dynamic_skill_step_names": sorted(dynamic_skill_step_names),
        "execution_id": execution_id,
        "recipe_name": recipe_name,
        "templates": [
            {
                "digest": template.template_digest,
                "skill_contract_identity": template.skill_contract_identity,
                "step_name": step_name,
                "tool_contract_identity": template.tool_contract_identity,
            }
            for step_name, template in templates.items()
        ],
    }
    return compute_canonical_hash(payload, domain=_RECIPE_EXECUTION_SNAPSHOT_DOMAIN)


@dataclass(frozen=True, slots=True)
class RecipeExecutionSnapshot:
    execution_id: str
    recipe_name: str
    content_hash: str
    composite_hash: str
    templates: Mapping[str, InvocationTemplate]
    snapshot_digest: str
    dynamic_skill_step_names: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not self.execution_id or not self.recipe_name:
            raise ValueError("recipe execution identity must be non-empty")
        if not HASH_RE.fullmatch(self.content_hash) or not HASH_RE.fullmatch(self.composite_hash):
            raise ValueError("recipe execution hashes must be canonical sha256 identities")
        copied = dict(self.templates)
        dynamic_skill_step_names = frozenset(self.dynamic_skill_step_names)
        if any(name != template.invocation.step_name for name, template in copied.items()):
            raise ValueError("recipe execution template keys must match step names")
        if any(not name for name in dynamic_skill_step_names):
            raise ValueError("dynamic recipe skill step names must be non-empty")
        if dynamic_skill_step_names.intersection(copied):
            raise ValueError("recipe skill steps cannot be both dynamic and attested")
        for template in copied.values():
            expected_template_digest = compute_invocation_template_digest(
                execution_id=self.execution_id,
                recipe_name=self.recipe_name,
                content_hash=self.content_hash,
                composite_hash=self.composite_hash,
                invocation=template.invocation,
                tool_contract_identity=template.tool_contract_identity,
                skill_contract_identity=template.skill_contract_identity,
            )
            if template.template_digest != expected_template_digest:
                raise ValueError("recipe execution invocation template digest mismatch")
        object.__setattr__(self, "templates", MappingProxyType(copied))
        object.__setattr__(self, "dynamic_skill_step_names", dynamic_skill_step_names)
        expected = compute_recipe_execution_snapshot_digest(
            execution_id=self.execution_id,
            recipe_name=self.recipe_name,
            content_hash=self.content_hash,
            composite_hash=self.composite_hash,
            templates=copied,
            dynamic_skill_step_names=dynamic_skill_step_names,
        )
        if self.snapshot_digest != expected:
            raise ValueError("recipe execution snapshot digest does not match content")

    @property
    def template_digests(self) -> Mapping[str, str]:
        return MappingProxyType(
            {name: template.template_digest for name, template in self.templates.items()}
        )


RECIPE_EXECUTION_CREDENTIAL_WIRE_KEY: str = "recipe_execution"

RUN_SKILL_ATTESTATION_PARAMS: frozenset[str] = frozenset(
    {"recipe_execution_id", "invocation_template_digest"}
)


class _SkillInputShape(TypedDict):
    keys: list[str]
    absence_values: dict[str, BoundScalar]


@dataclass(frozen=True, slots=True)
class RecipeExecutionCredential:
    """The caller-visible identity of one installed recipe execution."""

    execution_id: str
    snapshot_digest: str
    invocation_template_digests: Mapping[str, str]
    skill_input_shapes: Mapping[str, _SkillInputShape]

    def as_wire_block(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "invocation_template_digests": dict(self.invocation_template_digests),
            "skill_input_shapes": {
                step_name: {
                    "keys": list(shape["keys"]),
                    "absence_values": dict(shape["absence_values"]),
                }
                for step_name, shape in self.skill_input_shapes.items()
            },
            "snapshot_digest": self.snapshot_digest,
        }


RECIPE_EXECUTION_CREDENTIAL_WIRE_FIELDS: frozenset[str] = frozenset(
    f.name for f in dataclasses.fields(RecipeExecutionCredential)
)


def build_recipe_execution_credential(
    snapshot: RecipeExecutionSnapshot,
) -> RecipeExecutionCredential:
    """Project the sole caller-visible credential for an execution snapshot."""
    skill_input_shapes: dict[str, _SkillInputShape] = {}
    for step_name, template in snapshot.templates.items():
        present = tuple(value for value in template.invocation.skill_inputs if value.is_present)
        skill_input_shapes[step_name] = {
            "keys": [value.name for value in present],
            "absence_values": {
                value.name: value.absence_value
                for value in present
                if value.absence_value is not None
            },
        }
    return RecipeExecutionCredential(
        execution_id=snapshot.execution_id,
        snapshot_digest=snapshot.snapshot_digest,
        invocation_template_digests=dict(snapshot.template_digests),
        skill_input_shapes=skill_input_shapes,
    )


class PreflightKind(StrEnum):
    AUDIT_CYCLE_INVENTORY = "audit_cycle_inventory"


@dataclass(frozen=True, slots=True)
class VerifiedInputPreflightRequest:
    execution_generation: str
    step_name: str
    skill_name: str
    plan_path: str
    audit_cycle_path: str | None
    plan_disposition_path: str | None
    expected_plan_set_id: str = ""
    expected_scope_id: str = ""
    expected_part_id: str = ""


@dataclass(frozen=True, slots=True)
class PreflightEvidence:
    name: str
    value: BoundScalar


@dataclass(frozen=True, slots=True)
class VerifiedInputPreflightResult:
    decision: InventoryAdmissionDecision
    evidence: tuple[PreflightEvidence, ...] = ()


@runtime_checkable
class InputPreflightResolver(Protocol):
    def resolve(
        self,
        request: VerifiedInputPreflightRequest,
        *,
        allowed_root: Path | None = None,
    ) -> VerifiedInputPreflightResult: ...


@dataclass(frozen=True, slots=True)
class InstalledRecipeExecution:
    """One atomically replaceable active execution generation."""

    snapshot: RecipeExecutionSnapshot
    installation_version: InstallationVersion
    runtime_binding_digests: Mapping[str, str]
    audit_admission_ledger: AuditAdmissionLedger
    input_preflight_resolver: InputPreflightResolver

    def __post_init__(self) -> None:
        if not isinstance(self.installation_version, InstallationVersion):
            raise ValueError("installation_version must be an InstallationVersion")
        object.__setattr__(
            self,
            "runtime_binding_digests",
            MappingProxyType(dict(self.runtime_binding_digests)),
        )


@runtime_checkable
class RecipeExecutionFactory(Protocol):
    """Composition-root factory for a fully wired execution generation."""

    def __call__(
        self,
        *,
        snapshot: RecipeExecutionSnapshot,
        allowed_root: Path,
        installation_version: InstallationVersion,
        audit_admission_ledger: AuditAdmissionLedger,
    ) -> InstalledRecipeExecution: ...


def _build_runtime_binding_payload(
    *,
    execution_id: str,
    step_name: str,
    template_digest: str,
    bound_inputs: tuple[tuple[str, BoundScalar], ...],
    actual_mcp_kwargs: Mapping[str, BoundScalar],
    preflight: VerifiedInputPreflightResult | None,
    retry_after_audit_attempt_id: str | None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "bound_inputs": [{"name": name, "value": value} for name, value in bound_inputs],
        "execution_id": execution_id,
        "mcp_kwargs": [
            {"name": name, "value": actual_mcp_kwargs[name]} for name in sorted(actual_mcp_kwargs)
        ],
        "preflight": (
            None
            if preflight is None
            else {
                "evidence": [
                    {"name": item.name, "value": item.value} for item in preflight.evidence
                ],
                "reason": preflight.decision.reason.value,
                "status": preflight.decision.status.value,
            }
        ),
        "step_name": step_name,
        "template_digest": template_digest,
    }
    if retry_after_audit_attempt_id is not None:
        payload["retry_after_audit_attempt_id"] = retry_after_audit_attempt_id
    return payload


def compute_runtime_binding_digest(
    *,
    execution_id: str,
    step_name: str,
    template_digest: str,
    bound_inputs: tuple[tuple[str, BoundScalar], ...],
    actual_mcp_kwargs: Mapping[str, BoundScalar],
    preflight: VerifiedInputPreflightResult | None,
    retry_after_audit_attempt_id: str | None = None,
) -> str:
    """Hash actual ordered values independently from the template/payload."""
    payload = _build_runtime_binding_payload(
        execution_id=execution_id,
        step_name=step_name,
        template_digest=template_digest,
        bound_inputs=bound_inputs,
        actual_mcp_kwargs=actual_mcp_kwargs,
        preflight=preflight,
        retry_after_audit_attempt_id=retry_after_audit_attempt_id,
    )
    return compute_canonical_hash(payload, domain=_RUNTIME_BINDING_DOMAIN)


def compute_audit_slot_intent_digest(
    *,
    execution_id: str,
    step_name: str,
    template_digest: str,
    bound_inputs: tuple[tuple[str, BoundScalar], ...],
    actual_mcp_kwargs: Mapping[str, BoundScalar],
    preflight: VerifiedInputPreflightResult | None,
    retry_after_audit_attempt_id: str | None = None,
) -> str:
    """Hash audit-slot intent independently from any retry attempt."""
    stable_mcp_kwargs = {
        name: value
        for name, value in actual_mcp_kwargs.items()
        if name != "retry_after_audit_attempt_id"
    }
    payload = _build_runtime_binding_payload(
        execution_id=execution_id,
        step_name=step_name,
        template_digest=template_digest,
        bound_inputs=bound_inputs,
        actual_mcp_kwargs=stable_mcp_kwargs,
        preflight=preflight,
        retry_after_audit_attempt_id=None,
    )
    return compute_canonical_hash(payload, domain=_AUDIT_SLOT_INTENT_DOMAIN)
