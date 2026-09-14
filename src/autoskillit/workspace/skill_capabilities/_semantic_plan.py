"""Skill semantic-plan parser.

Owns the construction of ``SkillSemanticPlan`` from frontmatter-declared
semantic declarations. Includes the retirement registry
(``RETIRED_SEMANTIC_CAPABILITIES``) and the supporting helpers
(``_semantic_body``, ``_semantic_error``, ``_mapping_list``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from autoskillit.core import (
    CODEX_VALID_MODEL_IDS,
    SKILL_SEMANTIC_SCHEMA_VERSION,
    ChildModelPolicySpec,
    ChildSpawnCardinalityError,
    ChildSpawnSpec,
    ConcurrencySpec,
    EvidenceSpec,
    GitMetadataWriteSpec,
    JoinSpec,
    LogicalRoleSpec,
    SiblingSkillSpec,
    SkillContractError,
    SkillInvalidityKind,
    SkillSemanticPlan,
)

RETIRED_SEMANTIC_CAPABILITIES: dict[str, str] = {
    "agent_model": "semantic_requirements.child_model_policies",
    "agent_subagent": "semantic_requirements.child_spawns",
    "cross_skill_ref": "semantic_requirements.sibling_skills",
    "git_metadata_write": "semantic_requirements.git_metadata_writes",
}
_RETIRED_SEMANTIC_DECLARATIONS: dict[str, str] = {
    **RETIRED_SEMANTIC_CAPABILITIES,
    "backend_requirements": "backend selection outside skill declarations",
    "required_backends": "backend selection outside skill declarations",
}
_RAW_PORTABLE_TOKEN_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("Agent(", "semantic_requirements.child_spawns"),
    ("Task(", "semantic_requirements.child_spawns"),
    ("spawn_agent", "semantic_requirements.child_spawns"),
    ("send_message", "semantic_requirements.join"),
    ("wait_agent", "semantic_requirements.join"),
    ("subagent_type=", "semantic_requirements.logical_roles"),
)
_SEMANTIC_REQUIREMENT_KEYS = frozenset(
    {
        "child_spawns",
        "concurrency",
        "join",
        "evidence",
        "child_model_policies",
        "logical_roles",
        "sibling_skills",
        "git_metadata_writes",
    }
)


def _semantic_body(content: str) -> str:
    if not content.startswith("---"):
        return content
    parts = content.split("---", maxsplit=2)
    return parts[2] if len(parts) == 3 else content


def _semantic_error(
    path: Path,
    *,
    schema_version: object,
    offending: str,
    replacement: str,
) -> str:
    return (
        f"{path}: skill semantic schema version {schema_version!r} rejects offending token "
        f"{offending!r}; replace with {replacement}"
    )


def _mapping_list(value: Any, field_name: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise SkillContractError(f"semantic_requirements.{field_name} must be a list of mappings")
    return value


def parse_skill_semantic_plan(
    data: dict[str, Any],
    *,
    path: Path,
    content: str,
    uses_capabilities: frozenset[str],
) -> tuple[SkillSemanticPlan | None, tuple[tuple[SkillInvalidityKind, str], ...]]:
    """Parse one source declaration without granting it backend authority."""
    diagnostics: list[tuple[SkillInvalidityKind, str]] = []
    schema_version = data.get("semantic_version", SKILL_SEMANTIC_SCHEMA_VERSION)
    retired_caps = sorted(uses_capabilities & RETIRED_SEMANTIC_CAPABILITIES.keys())
    for capability in retired_caps:
        diagnostics.append(
            (
                SkillInvalidityKind.SEMANTIC_UNDECLARED_TOKENS,
                _semantic_error(
                    path,
                    schema_version=schema_version,
                    offending=capability,
                    replacement=RETIRED_SEMANTIC_CAPABILITIES[capability],
                ),
            )
        )

    body = _semantic_body(content)
    raw_tokens = (
        *_RAW_PORTABLE_TOKEN_REPLACEMENTS,
        *(
            (model_id, "semantic_requirements.child_model_policies.model_class")
            for model_id in sorted(CODEX_VALID_MODEL_IDS)
        ),
    )
    for token, replacement in raw_tokens:
        if token in body:
            diagnostics.append(
                (
                    SkillInvalidityKind.SEMANTIC_UNDECLARED_TOKENS,
                    _semantic_error(
                        path,
                        schema_version=schema_version,
                        offending=token,
                        replacement=replacement,
                    ),
                )
            )

    has_declaration = "semantic_version" in data or "semantic_requirements" in data
    if not has_declaration:
        return None, tuple(diagnostics)
    if "semantic_version" not in data:
        diagnostics.append(
            (
                SkillInvalidityKind.SEMANTIC_MISSING_VERSION,
                _semantic_error(
                    path,
                    schema_version="missing",
                    offending="semantic_requirements",
                    replacement=f"semantic_version: {SKILL_SEMANTIC_SCHEMA_VERSION}",
                ),
            )
        )
        return None, tuple(diagnostics)
    if schema_version != SKILL_SEMANTIC_SCHEMA_VERSION:
        diagnostics.append(
            (
                SkillInvalidityKind.SEMANTIC_VERSION_MISMATCH,
                _semantic_error(
                    path,
                    schema_version=schema_version,
                    offending=f"semantic_version: {schema_version}",
                    replacement=f"semantic_version: {SKILL_SEMANTIC_SCHEMA_VERSION}",
                ),
            )
        )
        return None, tuple(diagnostics)

    raw_requirements = data.get("semantic_requirements", {})
    if not isinstance(raw_requirements, dict):
        diagnostics.append(
            (
                SkillInvalidityKind.SEMANTIC_PLAN_INVALID,
                _semantic_error(
                    path,
                    schema_version=schema_version,
                    offending="semantic_requirements",
                    replacement="a mapping of version-1 semantic requirement fields",
                ),
            )
        )
        return None, tuple(diagnostics)

    unknown = sorted(set(raw_requirements) - _SEMANTIC_REQUIREMENT_KEYS)
    for token in unknown:
        replacement = _RETIRED_SEMANTIC_DECLARATIONS.get(
            token, f"one of {sorted(_SEMANTIC_REQUIREMENT_KEYS)}"
        )
        diagnostics.append(
            (
                SkillInvalidityKind.SEMANTIC_PLAN_INVALID,
                _semantic_error(
                    path,
                    schema_version=schema_version,
                    offending=token,
                    replacement=replacement,
                ),
            )
        )
    if diagnostics:
        return None, tuple(diagnostics)

    try:
        logical_roles = tuple(
            LogicalRoleSpec(
                name=str(item.get("name", "")),
                purpose=str(item.get("purpose", "")),
            )
            for item in _mapping_list(raw_requirements.get("logical_roles", []), "logical_roles")
        )
        child_spawns = tuple(
            ChildSpawnSpec(
                role=str(item.get("role", "")),
                count=item.get("count"),
                for_each=item.get("for_each"),
            )
            for item in _mapping_list(raw_requirements.get("child_spawns", []), "child_spawns")
        )
        child_model_policies = tuple(
            ChildModelPolicySpec(
                role=str(item.get("role", "")),
                model_class=(
                    str(item["model_class"]) if item.get("model_class") is not None else None
                ),
                reasoning_effort=(
                    str(item["reasoning_effort"])
                    if item.get("reasoning_effort") is not None
                    else None
                ),
            )
            for item in _mapping_list(
                raw_requirements.get("child_model_policies", []),
                "child_model_policies",
            )
        )
        sibling_skills = tuple(
            SiblingSkillSpec(name=str(item.get("name", "")))
            for item in _mapping_list(raw_requirements.get("sibling_skills", []), "sibling_skills")
        )
        git_metadata_writes = tuple(
            GitMetadataWriteSpec(purpose=str(item.get("purpose", "")))
            for item in _mapping_list(
                raw_requirements.get("git_metadata_writes", []),
                "git_metadata_writes",
            )
        )

        def optional_spec(field_name: str, spec_type: type[Any]) -> Any:
            raw = raw_requirements.get(field_name)
            if raw is None:
                return None
            if not isinstance(raw, dict):
                raise SkillContractError(f"semantic_requirements.{field_name} must be a mapping")
            return spec_type(**raw)

        plan = SkillSemanticPlan(
            schema_version=schema_version,
            child_spawns=child_spawns,
            concurrency=optional_spec("concurrency", ConcurrencySpec),
            join=optional_spec("join", JoinSpec),
            evidence=optional_spec("evidence", EvidenceSpec),
            child_model_policies=child_model_policies,
            logical_roles=logical_roles,
            sibling_skills=sibling_skills,
            git_metadata_writes=git_metadata_writes,
        )
    except ChildSpawnCardinalityError as exc:
        diagnostics.append(
            (
                SkillInvalidityKind.SEMANTIC_CHILD_CARDINALITY_INVALID,
                _semantic_error(
                    path,
                    schema_version=schema_version,
                    offending="semantic_requirements.child_spawns cardinality",
                    replacement=str(exc),
                ),
            )
        )
        return None, tuple(diagnostics)
    except (SkillContractError, TypeError, ValueError) as exc:
        diagnostics.append(
            (
                SkillInvalidityKind.SEMANTIC_PLAN_INVALID,
                _semantic_error(
                    path,
                    schema_version=schema_version,
                    offending="semantic_requirements",
                    replacement=f"a valid version-{SKILL_SEMANTIC_SCHEMA_VERSION} plan ({exc})",
                ),
            )
        )
        return None, tuple(diagnostics)
    return plan, ()


__all__ = [
    "RETIRED_SEMANTIC_CAPABILITIES",
    "parse_skill_semantic_plan",
]
