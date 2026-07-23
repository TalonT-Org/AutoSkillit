"""Projection of internal skill contracts into agent-visible documents."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from autoskillit.core import SkillContractError, SkillSourceRef, dump_yaml_str
from autoskillit.workspace.skills import SkillInfo

__all__ = [
    "AgentSkillDocument",
    "SkillProjectionContext",
    "project_agent_skill_document",
]

_MACHINE_ONLY_KEYS = frozenset({"uses_capabilities", "execution_role", "backend_requirements"})


@dataclass(frozen=True, slots=True)
class AgentSkillDocument:
    """One model-safe projection with identity bound to its canonical source."""

    content: str
    projected_digest: str
    canonical_digest: str
    source_ref: SkillSourceRef


@dataclass(frozen=True, slots=True)
class SkillProjectionContext:
    """Execution-local inputs that may affect an agent-visible projection."""

    execution_cwd: Path
    backend: Any | None = None
    conventions: Any | None = None
    substitutions: Mapping[str, str] | None = None
    gating: bool | None = None
    namespace: str | None = None
    projection_version: int = 1


def project_agent_skill_document(
    skill_info: SkillInfo,
    context: SkillProjectionContext,
) -> AgentSkillDocument:
    """Remove machine authority fields while preserving public YAML and body."""
    parsed = skill_info.frontmatter
    if parsed is None:
        raise SkillContractError(f"skill {skill_info.name!r} has no parsed machine contract")
    if not parsed.is_valid or parsed.data is None:
        raise SkillContractError(
            f"cannot project invalid contract for {skill_info.name!r}: {parsed.error}"
        )
    if skill_info.source_ref is None:
        raise SkillContractError(f"skill {skill_info.name!r} has no effective source reference")

    frontmatter = dict(parsed.data)
    for key in _MACHINE_ONLY_KEYS:
        frontmatter.pop(key, None)
    if context.gating is True:
        frontmatter["disable-model-invocation"] = True
    elif context.gating is False:
        frontmatter.pop("disable-model-invocation", None)

    yaml_text = dump_yaml_str(
        frontmatter,
        sort_keys=False,
        default_flow_style=False,
    ).rstrip("\n")
    content = f"---\n{yaml_text}\n---\n{parsed.body}"
    for source, replacement in (context.substitutions or {}).items():
        content = content.replace(source, replacement)

    projected_digest = hashlib.sha256(content.encode()).hexdigest()
    canonical_digest = (
        skill_info.canonical_digest
        or hashlib.sha256(skill_info.canonical_content.encode()).hexdigest()
    )
    return AgentSkillDocument(
        content=content,
        projected_digest=projected_digest,
        canonical_digest=canonical_digest,
        source_ref=skill_info.source_ref,
    )
