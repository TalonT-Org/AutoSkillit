"""Managed fixed-batch request normalization and membership validation."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from fastmcp import Context

from autoskillit.core import (
    ManagedJoinAttestation,
    SemanticAdaptationContext,
    SkillContractError,
    SkillSemanticAdaptationResult,
)
from autoskillit.execution import MANAGED_CODEX_PARENT_GUARD_SET
from autoskillit.hooks._hook_settings import validate_session_id
from autoskillit.hooks._session_binding import (
    LoadedSkillEntry,
    SessionBinding,
    SessionBindingError,
    binding_lock,
    normalize_skill_name,
    read_binding,
    resolve_binding_path,
    write_binding,
)
from autoskillit.server._run_skill_completion import _request_session_identity
from autoskillit.server.tools.tools_execution._managed_fixed_batch import ManagedLaunchBinding
from autoskillit.server.tools.tools_execution._managed_leaf import ManagedLeafAssignmentInput

if TYPE_CHECKING:
    from autoskillit.pipeline import ToolContext


@dataclass(frozen=True, slots=True)
class _ManagedRequestFacts:
    launch: ManagedLaunchBinding
    binding: SessionBinding
    selected_source: LoadedSkillEntry
    channel_dir: Path
    adaptation_context: SemanticAdaptationContext


def _text(
    value: object,
    *,
    operation: str,
    field: str,
    maximum: int,
    required: bool = True,
    preserve_content: bool = False,
) -> str:
    if not isinstance(value, str):
        raise SkillContractError(f"{operation} {field} must be text")
    candidate = value if preserve_content else " ".join(value.split())
    if required and not candidate.strip():
        raise SkillContractError(f"{operation} {field} must be non-empty")
    if len(candidate) > maximum:
        raise SkillContractError(f"{operation} {field} exceeds the {maximum}-character bound")
    return candidate


def _normalize_assignments(raw: object) -> tuple[ManagedLeafAssignmentInput, ...]:
    if not isinstance(raw, list) or not raw:
        raise SkillContractError("run_fixed_batch assignments must be a non-empty array")
    if len(raw) > _MAX_ASSIGNMENTS:
        raise SkillContractError(f"run_fixed_batch accepts at most {_MAX_ASSIGNMENTS} assignments")
    assignments: list[ManagedLeafAssignmentInput] = []
    for ordinal, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise SkillContractError(f"run_fixed_batch assignment {ordinal} must be an object")
        unknown = set(item) - {"role", "label", "task_prompt", "runtime_key"}
        if unknown:
            raise SkillContractError(
                f"run_fixed_batch assignment {ordinal} has unknown fields: {sorted(unknown)}"
            )
        assignments.append(
            ManagedLeafAssignmentInput(
                role=_text(
                    item.get("role"),
                    operation="run_fixed_batch",
                    field="role",
                    maximum=_MAX_ROLE_CHARS,
                ),
                label=_text(
                    item.get("label"),
                    operation="run_fixed_batch",
                    field="label",
                    maximum=_MAX_LABEL_CHARS,
                ),
                task_prompt=_text(
                    item.get("task_prompt"),
                    operation="run_fixed_batch",
                    field="task_prompt",
                    maximum=_MAX_TASK_PROMPT_CHARS,
                    preserve_content=True,
                ),
                runtime_key=_text(
                    item.get("runtime_key", ""),
                    operation="run_fixed_batch",
                    field="runtime_key",
                    maximum=_MAX_RUNTIME_KEY_CHARS,
                    required=False,
                ),
            )
        )
    return tuple(assignments)


def _validate_membership(
    assignments: Sequence[ManagedLeafAssignmentInput],
    selected_source: LoadedSkillEntry,
    adaptation: SkillSemanticAdaptationResult,
) -> None:
    cardinality = selected_source.child_spawn_cardinality
    if not cardinality:
        raise SkillContractError("run_fixed_batch source has no declared child cardinality")
    received_roles = Counter(item.role for item in assignments)
    static_counts = {role: count for role, count in cardinality.items() if type(count) is int}
    if static_counts and any(
        received_roles.get(role, 0) != count for role, count in static_counts.items()
    ):
        raise SkillContractError(
            "run_fixed_batch assignments do not match declared role cardinality"
        )
    undeclared_roles = set(received_roles) - set(cardinality)
    if undeclared_roles:
        raise SkillContractError(
            f"run_fixed_batch assignments use undeclared roles: {sorted(undeclared_roles)}"
        )
    if all(type(count) is int for count in cardinality.values()) and len(assignments) != sum(
        int(count) for count in cardinality.values()
    ):
        raise SkillContractError(
            "run_fixed_batch assignments do not match declared static cardinality"
        )
    dynamic_roles = {role for role, count in cardinality.items() if isinstance(count, str)}
    if dynamic_roles:
        dynamic_keys = [item.runtime_key for item in assignments if item.role in dynamic_roles]
        if not dynamic_keys or any(not key for key in dynamic_keys):
            raise SkillContractError(
                "run_fixed_batch dynamic assignments require authoritative runtime keys"
            )
        if len(dynamic_keys) != len(set(dynamic_keys)):
            raise SkillContractError("run_fixed_batch dynamic runtime keys must be unique")
    logical_roles = adaptation.logical_role_mapping
    if logical_roles and any(role not in logical_roles for role in received_roles):
        raise SkillContractError(
            "run_fixed_batch assignments use roles absent from source adaptation"
        )


def _request_facts(
    *,
    skill_name: str,
    request_context: Context,
    tool_ctx: ToolContext,
) -> _ManagedRequestFacts:
    request_session_id = _request_session_identity(request_context)
    validate_session_id(request_session_id)
    normalized_skill_name = normalize_skill_name(skill_name)
    binding_path = resolve_binding_path(str(tool_ctx.project_dir), request_session_id)
    try:
        binding = read_binding(binding_path)
    except SessionBindingError as exc:
        raise SkillContractError("run_fixed_batch session binding is invalid") from exc
    if binding is None or binding.session_id != request_session_id or not binding.binding_valid:
        raise SkillContractError("run_fixed_batch requires a valid request session binding")
    if binding.managed_leaf_id:
        raise SkillContractError("run_fixed_batch is unavailable to managed leaf sessions")
    if not binding.managed_parent_id:
        raise SkillContractError("run_fixed_batch binding lacks a managed parent identity")
    selected_source = next(
        (
            entry
            for entry in reversed(binding.loaded_skills)
            if entry.skill_name == normalized_skill_name
        ),
        None,
    )
    if (
        selected_source is None
        or not selected_source.join_required
        or not selected_source.binding_valid
    ):
        raise SkillContractError("run_fixed_batch requires the exact loaded join-bearing skill")
    if not (
        selected_source.source_artifact_digest
        and selected_source.source_artifact_incarnation_id
        and selected_source.semantic_digest
        and selected_source.adaptation_digest
    ):
        raise SkillContractError(
            "run_fixed_batch source binding lacks immutable identity evidence"
        )
    backend = tool_ctx.backend
    authority = tool_ctx.managed_join_attestation_authority
    service = tool_ctx.managed_fixed_batch_supervisor
    if backend is None or authority is None or service is None:
        raise SkillContractError("run_fixed_batch managed authority is unavailable")
    adaptation_context = authority.find_verified_context(
        backend=backend.name,
        parent_session_id=request_session_id,
    )
    if adaptation_context is None:
        raise SkillContractError("run_fixed_batch requires a current server-issued attestation")
    attestation = adaptation_context.managed_join_attestation
    if attestation is None or not service.recovery_ready:
        raise SkillContractError("run_fixed_batch is blocked by managed recovery")
    binding = _bind_managed_parent_route(
        binding_path,
        request_session_id=request_session_id,
        attestation=attestation,
    )
    return _ManagedRequestFacts(
        launch=ManagedLaunchBinding(
            request_session_id=request_session_id,
            managed_parent_id=binding.managed_parent_id,
            parent_session_id=attestation.parent_session_id,
            caller_key="pending",
            attestation_epoch=attestation.activation_epoch,
            recovery_ready=service.recovery_ready,
            selected_source=selected_source,
        ),
        binding=binding,
        selected_source=selected_source,
        channel_dir=binding_path.parent,
        adaptation_context=adaptation_context,
    )


_MAX_ASSIGNMENTS = 128


_MAX_LABEL_CHARS = 160


_MAX_ROLE_CHARS = 100


_MAX_RUNTIME_KEY_CHARS = 240


_MAX_TASK_PROMPT_CHARS = 16_000


def _bind_managed_parent_route(
    binding_path: Path,
    *,
    request_session_id: str,
    attestation: ManagedJoinAttestation,
) -> SessionBinding:
    """Mint or verify the server-owned parent route under the binding lock."""
    expected_guards = tuple(sorted(MANAGED_CODEX_PARENT_GUARD_SET))
    config_digest = getattr(attestation, "hook_registry_digest", "")
    if not isinstance(config_digest, str) or not config_digest:
        raise SkillContractError("run_fixed_batch attestation lacks a managed config digest")
    with binding_lock(binding_path):
        current = read_binding(binding_path)
        if (
            current is None
            or current.session_id != request_session_id
            or not current.binding_valid
        ):
            raise SkillContractError(
                "run_fixed_batch request binding changed during authorization"
            )
        if current.managed_leaf_id:
            raise SkillContractError("run_fixed_batch is unavailable to managed leaf sessions")
        if current.managed_route == "":
            current = current._replace(
                managed_route="parent",
                managed_guard_set=expected_guards,
                managed_config_digest=config_digest,
            )
            write_binding(binding_path, current)
        if (
            current.managed_route != "parent"
            or current.managed_guard_set != expected_guards
            or current.managed_config_digest != config_digest
        ):
            raise SkillContractError(
                "run_fixed_batch binding does not match the managed parent route"
            )
        return current
