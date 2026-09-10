"""Managed fixed-batch request normalization and membership validation."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence

from autoskillit.core import (
    SkillContractError,
    SkillSemanticAdaptationResult,
)
from autoskillit.hooks import LoadedSkillEntry
from autoskillit.server.tools.tools_execution._managed_leaf import ManagedLeafAssignmentInput


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


_MAX_ASSIGNMENTS = 128


_MAX_LABEL_CHARS = 160


_MAX_ROLE_CHARS = 100


_MAX_RUNTIME_KEY_CHARS = 240


_MAX_TASK_PROMPT_CHARS = 16_000
