"""Single frontmatter parse entry point for skill resolution.

Owns ``_skill_info_from_frontmatter`` — the function that orchestrates the
fail-closed frontmatter / sidecar / marker / semantic-plan / authenticity
pipeline. It is the one function in the codebase that crosses all the seams;
isolating it in its own module makes the parse boundary visible.

Reaches ``_load_exploration_sidecar``, ``_parse_exploration_sidecar``, and
``_bind_exploration_vector_markers`` through ``autoskillit.workspace.skills``'s
module globals at call time so existing test monkeypatches via the facade
remain visible.
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import autoskillit.workspace.skills as _skills_facade
from autoskillit.core import (
    SKILL_CAPABILITY_REGISTRY,
    ExplorationVectorDef,
    SkillContractError,
    SkillInvalidityKind,
    SkillSource,
    SkillSourceRef,
    get_logger,
    validate_skill_capability_roles,
)
from autoskillit.workspace.skill_format import read_skill_frontmatter
from autoskillit.workspace.skills_records import (
    SkillInfo,
    SkillInvalidity,
)

logger = get_logger(__name__)


def _skill_info_from_frontmatter(
    name: str,
    source: SkillSource,
    skill_path: Path,
    *,
    source_ref: SkillSourceRef | None = None,
) -> SkillInfo:
    """Build a SkillInfo by reading all frontmatter fields in a single parse."""
    parsed = read_skill_frontmatter(skill_path)
    if not parsed.is_valid or parsed.data is None:
        return SkillInfo(
            name=name,
            source=source,
            path=skill_path,
            source_ref=source_ref,
            execution_role=None,
            canonical_content=parsed.content,
            canonical_digest=hashlib.sha256(parsed.content.encode()).hexdigest(),
            frontmatter=parsed,
            invalidities=(
                SkillInvalidity(
                    SkillInvalidityKind.FRONTMATTER_PARSE,
                    f"invalid frontmatter: {parsed.error}",
                ),
            ),
        )

    data = parsed.data
    invalidities: list[SkillInvalidity] = []
    categories_raw = data.get("categories", [])
    if not isinstance(categories_raw, list):
        invalidities.append(
            SkillInvalidity(SkillInvalidityKind.FIELD_SHAPE, "categories must be a list")
        )
        categories_raw = []
    categories = frozenset(str(c) for c in categories_raw)

    caps_raw = data.get("uses_capabilities", [])
    if not isinstance(caps_raw, list):
        logger.warning(
            "uses_capabilities_not_a_list",
            value=caps_raw,
            skill=name,
            hint="use bracket syntax: uses_capabilities: [agent_subagent]",
        )
        invalidities.append(
            SkillInvalidity(SkillInvalidityKind.FIELD_SHAPE, "uses_capabilities must be a list")
        )
        caps_raw = []
    uses_capabilities = frozenset(str(c) for c in caps_raw)

    from autoskillit.workspace.skill_capabilities import parse_skill_semantic_plan  # noqa: PLC0415

    semantic_plan, semantic_diagnostics = parse_skill_semantic_plan(
        data,
        path=skill_path,
        content=parsed.content,
        uses_capabilities=uses_capabilities,
    )
    invalidities.extend(SkillInvalidity(kind, detail) for kind, detail in semantic_diagnostics)

    execution_role = parsed.execution_role

    activate_deps_raw = data.get("activate_deps", [])
    if not isinstance(activate_deps_raw, list):
        invalidities.append(
            SkillInvalidity(SkillInvalidityKind.FIELD_SHAPE, "activate_deps must be a list")
        )
        activate_deps_raw = []
    activate_deps = tuple(str(dep) for dep in activate_deps_raw)

    exploration_vectors: tuple[ExplorationVectorDef, ...] = ()
    exploration_sidecar_digest = ""
    if "exploration_vectors" in data:
        invalidities.append(
            SkillInvalidity(
                SkillInvalidityKind.EXPLORATION_CONTRACT_INVALID,
                "exploration_vectors in frontmatter is no longer supported; "
                "moved to the exploration.yaml sidecar",
            )
        )
    else:
        try:
            sidecar_data, sidecar_digest = _skills_facade._load_exploration_sidecar(skill_path)
            exploration_sidecar_digest = sidecar_digest
            if sidecar_data is not None:
                parsed_vectors = _skills_facade._parse_exploration_sidecar(sidecar_data, name)
                exploration_vectors = _skills_facade._bind_exploration_vector_markers(
                    parsed.content,
                    parsed_vectors,
                )
            else:
                # No sidecar — check if there are markers in the body that expect one.
                # If markers exist but no sidecar, that is an error caught by the binder
                # when called with an empty vector tuple.
                pass
        except SkillContractError as exc:
            invalidities.append(
                SkillInvalidity(
                    SkillInvalidityKind.EXPLORATION_CONTRACT_INVALID,
                    str(exc),
                )
            )

    # These names are reserved machine-derived fields. Reading them here makes
    # attempts to inject source identity through YAML an explicit contract error.
    supplied_canonical_content = data.get("canonical_content")
    supplied_canonical_digest = data.get("canonical_digest")
    if supplied_canonical_content is not None or supplied_canonical_digest is not None:
        invalidities.append(
            SkillInvalidity(
                SkillInvalidityKind.RESERVED_FIELD,
                "canonical content and digest are source-derived",
            )
        )

    unknown_caps = uses_capabilities - frozenset(SKILL_CAPABILITY_REGISTRY)
    if unknown_caps:
        logger.warning(
            "unrecognized_uses_capabilities",
            invalid=sorted(unknown_caps),
            skill=name,
            valid=sorted(SKILL_CAPABILITY_REGISTRY),
        )
    assert execution_role is not None
    try:
        validate_skill_capability_roles(uses_capabilities, execution_role)
    except SkillContractError as exc:
        invalidities.append(SkillInvalidity(SkillInvalidityKind.UNKNOWN_CAPABILITY, str(exc)))

    canonical_digest = hashlib.sha256(parsed.content.encode()).hexdigest()

    info = SkillInfo(
        name=name,
        source=source,
        path=skill_path,
        source_ref=source_ref,
        categories=categories,
        uses_capabilities=uses_capabilities,
        semantic_plan=semantic_plan,
        execution_role=execution_role,
        activate_deps=activate_deps,
        exploration_vectors=exploration_vectors,
        exploration_sidecar_digest=exploration_sidecar_digest,
        canonical_content=parsed.content,
        canonical_digest=canonical_digest,
        frontmatter=parsed,
        invalidities=tuple(invalidities),
    )
    from autoskillit.workspace.skill_capabilities import (  # noqa: PLC0415
        validate_skill_capability_authenticity,
    )

    authenticity_diagnostics = validate_skill_capability_authenticity(info)
    if authenticity_diagnostics:
        info = replace(
            info,
            invalidities=info.invalidities
            + tuple(
                SkillInvalidity(
                    diagnostic.kind,
                    diagnostic.detail,
                    capability=diagnostic.capability,
                )
                for diagnostic in authenticity_diagnostics
            ),
        )
    return info


__all__: list[str] = []  # Internal shard — frontmatter orchestrator reached via the skills facade.
