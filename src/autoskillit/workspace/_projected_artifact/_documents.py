"""Projected-artifact documents — projection contexts and contract projection.

Single owner of the projection context types and ``project_agent_skill_document``.

Projection preserves exact context-bound-record validation, immutable payload
maps, removal of machine-only frontmatter, substitutions and namespace/backend
sigil rewrites, semantic adaptation, exploration replacement, and every
published digest.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import MappingProxyType
from typing import assert_never

import regex as re

from autoskillit.core import (
    MACHINE_ONLY_SKILL_FRONTMATTER_KEYS,
    PLUGINLESS_EXPLORER_ROLE,
    SKILL_PROJECTION_VERSION,
    BackendConventions,
    CodingAgentBackend,
    EffectiveSkillCatalogAuthority,
    EffectiveSkillInvocationAuthority,
    ExplorationApplicability,
    ExplorationRouterPlan,
    ExplorationVectorApplicabilityId,
    ExplorationVectorDef,
    ExplorationVectorDisposition,
    ProfileActivation,
    RepositoryProfileId,
    SemanticAdaptationContext,
    SkillAuthority,
    SkillContractError,
    SkillSemanticAdaptationResult,
    SkillSource,
    SkillSourceIdentity,
    dump_yaml_str,
    normalize_parent_sandbox_mode,
    render_exploration_failure_guidance,
    temp_dir_display_str,
)
from autoskillit.workspace.skills import (
    render_skill_invalidities,
    replace_exploration_vector_bodies,
)
from autoskillit.workspace.skills._resources import load_skill_resource

_SKILL_NAMESPACE_REF_RE = re.compile(r"/autoskillit:([a-z][a-z0-9-]*)")

SkillContractRecord = SkillAuthority


def _source_identity(skill: SkillContractRecord) -> SkillSourceIdentity:
    return skill.source_identity


def _default_base_branch(value: object) -> str:
    """Return configured branch text or the installation default for test doubles."""
    return value if isinstance(value, str) else "main"


def _agent_skill_namespace(source: SkillSource) -> str:
    match source:
        case SkillSource.BUNDLED:
            return "autoskillit:"
        case SkillSource.BUNDLED_EXTENDED | SkillSource.PROJECT_LOCAL | SkillSource.THIRD_PARTY:
            return ""
        case _ as unreachable:
            assert_never(unreachable)


def _resolve_projection_paths(
    cwd: Path,
    project_root: Path | None,
    invocation: EffectiveSkillInvocationAuthority | None,
) -> tuple[Path, Path | None]:
    """Resolve the invocation's working and optional project roots."""
    resolved_project_root = project_root
    if resolved_project_root is None and invocation is not None:
        resolved_project_root = invocation.project_root
    return (
        cwd.resolve(),
        resolved_project_root.resolve() if resolved_project_root is not None else None,
    )


def _bind_backend_conventions(
    backend: CodingAgentBackend | None,
    conventions: BackendConventions | None,
) -> BackendConventions | None:
    """Return conventions bound to *backend*, rejecting mismatched bindings."""
    if backend is None:
        if conventions is not None:
            raise SkillContractError("projection context conventions require a bound backend")
        return None
    backend_conventions = backend.conventions
    if conventions is not None and conventions != backend_conventions:
        raise SkillContractError("projection context conventions do not match its backend")
    return backend_conventions if conventions is None else conventions


def _normalize_exploration_context(
    launch_context_ref: str | None,
    resolved_profile: RepositoryProfileId | None,
    active_applicabilities: frozenset[ExplorationVectorApplicabilityId],
) -> frozenset[ExplorationVectorApplicabilityId]:
    """Validate and freeze the exploration inputs that affect projection bytes."""
    if launch_context_ref is not None and not launch_context_ref.strip():
        raise SkillContractError("exploration launch-context reference must be non-empty text")
    if resolved_profile is RepositoryProfileId.AUTO:
        raise SkillContractError("resolved exploration profile cannot remain auto")
    normalized_applicabilities = frozenset(active_applicabilities)
    if ExplorationVectorApplicabilityId.ALWAYS not in normalized_applicabilities:
        raise SkillContractError("active exploration applicability must include always")
    return normalized_applicabilities


@dataclass(frozen=True, slots=True)
class AgentSkillDocument:
    """One model-safe projection with identity bound to its canonical source."""

    content: str
    projected_digest: str
    canonical_digest: str
    source_identity: SkillSourceIdentity
    semantic_payload: Mapping[str, object] = field(default_factory=dict)
    adaptation_payload: Mapping[str, object] = field(default_factory=dict)
    semantic_digest: str = ""
    adaptation_digest: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "semantic_payload", MappingProxyType(dict(self.semantic_payload)))
        object.__setattr__(
            self,
            "adaptation_payload",
            MappingProxyType(dict(self.adaptation_payload)),
        )


@dataclass(frozen=True, slots=True)
class _SemanticAdaptationOutcome:
    """Result of attaching semantic payload and optional backend adaptation to a skill document."""

    content: str
    semantic_payload: Mapping[str, object]
    adaptation_payload: Mapping[str, object]
    semantic_digest: str
    adaptation_digest: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "semantic_payload", MappingProxyType(dict(self.semantic_payload)))
        object.__setattr__(
            self,
            "adaptation_payload",
            MappingProxyType(dict(self.adaptation_payload)),
        )


@dataclass(frozen=True, slots=True)
class SkillProjectionContext:
    """Execution-local inputs that may affect an agent-visible projection.

    ``provisioning_disposition`` is tri-state. The state table is the
    authoritative contract; downstream code MUST branch on these values:

    =============  ===================================================
    Value          Meaning
    =============  ===================================================
    ``True``       Authorize the session-scoped provisioning preamble
                   when the bound backend's capability also permits it.
    ``False``      Render the unavailable-fallback text and route the
                   vector to the pluginless explorer role.
    ``None``       Preserve native dispatch without projecting any
                   provisioning instructions.
    =============  ===================================================

    The later provisioning call still validates request identity, session
    scope, and store state, regardless of the disposition chosen here.
    """

    cwd: Path
    project_root: Path | None = None
    catalog: EffectiveSkillCatalogAuthority | None = None
    invocation: EffectiveSkillInvocationAuthority | None = None
    backend: CodingAgentBackend | None = None
    conventions: BackendConventions | None = None
    substitutions: Mapping[str, str] | None = None
    gating: bool | None = None
    namespace: str | None = None
    exploration_launch_context_ref: str | None = None
    resolved_exploration_profile: RepositoryProfileId | None = None
    active_exploration_applicabilities: frozenset[ExplorationVectorApplicabilityId] = frozenset(
        {ExplorationVectorApplicabilityId.ALWAYS}
    )
    parent_sandbox_mode: str = "workspace-write"
    adaptation_context: SemanticAdaptationContext | None = None
    managed_codex_route: str | None = None
    provisioning_disposition: bool | None = None
    projection_version: int = SKILL_PROJECTION_VERSION

    def __post_init__(self) -> None:
        if type(self.projection_version) is not int or self.projection_version < 1:
            raise SkillContractError("projection version must be a positive integer")
        object.__setattr__(
            self,
            "parent_sandbox_mode",
            normalize_parent_sandbox_mode(self.parent_sandbox_mode),
        )
        if self.managed_codex_route not in (None, "parent", "leaf", "interactive-parent"):
            raise SkillContractError(
                "managed Codex route must be parent, leaf, interactive-parent, or absent"
            )
        if (self.catalog is None) == (self.invocation is None):
            raise SkillContractError(
                "projection context must bind exactly one effective catalog or invocation"
            )
        resolved_cwd, resolved_project_root = _resolve_projection_paths(
            self.cwd,
            self.project_root,
            self.invocation,
        )
        object.__setattr__(self, "cwd", resolved_cwd)
        object.__setattr__(self, "project_root", resolved_project_root)
        object.__setattr__(
            self,
            "conventions",
            _bind_backend_conventions(self.backend, self.conventions),
        )
        active_applicabilities = _normalize_exploration_context(
            self.exploration_launch_context_ref,
            self.resolved_exploration_profile,
            self.active_exploration_applicabilities,
        )
        object.__setattr__(
            self,
            "active_exploration_applicabilities",
            active_applicabilities,
        )
        if self.substitutions is not None:
            object.__setattr__(
                self,
                "substitutions",
                MappingProxyType(dict(self.substitutions)),
            )

    @property
    def skills(self) -> tuple[SkillContractRecord, ...]:
        """Return the immutable effective skill set bound to this projection."""
        if self.invocation is not None:
            return self.invocation.closure
        assert self.catalog is not None
        return self.catalog.skills

    @property
    def exploration_vectors(self) -> Mapping[str, tuple[ExplorationVectorDef, ...]]:
        """Return validated vectors keyed by the exact bound skill name."""
        return MappingProxyType({skill.name: skill.exploration_vectors for skill in self.skills})


def _exploration_router_plan(
    vectors: tuple[ExplorationVectorDef, ...],
) -> ExplorationRouterPlan:
    """Build the one backend-neutral authoring plan for migrated vectors."""
    migrated = tuple(
        sorted(
            (
                vector
                for vector in vectors
                if vector.disposition is ExplorationVectorDisposition.MIGRATED
            ),
            key=lambda vector: vector.task.task_id,
        )
    )
    task_ids = {vector.task.task_id for vector in migrated}
    if len(task_ids) != len(migrated):
        raise SkillContractError("migrated exploration task ids must be unique")
    unknown_dependencies = {
        dependency
        for vector in migrated
        for dependency in vector.task.depends_on
        if dependency not in task_ids
    }
    if unknown_dependencies:
        raise SkillContractError(
            "migrated exploration tasks name unknown dependencies: "
            f"{sorted(unknown_dependencies)!r}"
        )
    pending = {vector.task.task_id: set(vector.task.depends_on) for vector in migrated}
    resolved: set[str] = set()
    while pending:
        ready = sorted(
            task_id for task_id, dependencies in pending.items() if dependencies <= resolved
        )
        if not ready:
            raise SkillContractError("migrated exploration tasks contain a dependency cycle")
        resolved.update(ready)
        for task_id in ready:
            pending.pop(task_id)
    profiles = sorted({vector.profile for vector in migrated})
    return ExplorationRouterPlan(
        snapshot=None,
        tasks=tuple(vector.task for vector in migrated),
        activations=tuple(
            ProfileActivation(
                profile,
                ExplorationApplicability.APPLICABLE,
                "authoring applicability:always",
            )
            for profile in profiles
        ),
    )


def _active_exploration_vectors(
    vectors: tuple[ExplorationVectorDef, ...],
    context: SkillProjectionContext,
) -> tuple[ExplorationVectorDef, ...]:
    """Resolve profile:auto and closed applicability before native rendering."""
    active: list[ExplorationVectorDef] = []
    for vector in vectors:
        if (
            vector.disposition is not ExplorationVectorDisposition.MIGRATED
            or vector.applicability not in context.active_exploration_applicabilities
        ):
            continue
        profile = vector.profile
        if profile is RepositoryProfileId.AUTO:
            resolved_profile = context.resolved_exploration_profile
            if resolved_profile is None:
                raise SkillContractError(
                    "profile:auto exploration requires a trusted resolved repository profile"
                )
            profile = resolved_profile
        active.append(
            replace(
                vector,
                profile=profile,
                task=replace(vector.task, profile=profile),
            )
        )
    return tuple(active)


def _direct_install_projection_context(
    *,
    cwd: Path,
    project_root: Path | None,
    catalog: EffectiveSkillCatalogAuthority,
    backend: CodingAgentBackend,
    destination: Path,
    default_base_branch: str,
    adaptation_context: SemanticAdaptationContext | None = None,
    projection_version: int = SKILL_PROJECTION_VERSION,
) -> SkillProjectionContext:
    """Bind every byte-affecting input shared by a direct install and dispatch."""
    return SkillProjectionContext(
        cwd=cwd,
        project_root=project_root,
        catalog=catalog,
        backend=backend,
        conventions=backend.conventions,
        resolved_exploration_profile=RepositoryProfileId.LANGUAGE_NEUTRAL,
        substitutions={
            "{{AUTOSKILLIT_TEMP}}": temp_dir_display_str(None),
            "{{AUTOSKILLIT_SCRIPTS}}": str(destination / "recipes" / "scripts"),
            "{{DEFAULT_BASE_BRANCH}}": default_base_branch,
        },
        adaptation_context=adaptation_context,
        projection_version=projection_version,
    )


def _validate_projection_input(
    skill_info: SkillContractRecord,
    context: SkillProjectionContext,
) -> tuple[Mapping[str, object], str]:
    """Return parsed projection inputs after confirming the contract matches the bound record."""
    if skill_info.invalidities:
        raise SkillContractError(
            f"cannot project invalid contract for {skill_info.name!r}: "
            f"{render_skill_invalidities(skill_info.invalidities)}"
        )
    parsed = skill_info.frontmatter
    if parsed is None:
        raise SkillContractError(f"skill {skill_info.name!r} has no parsed machine contract")
    if not parsed.is_valid or parsed.data is None:
        raise SkillContractError(
            f"cannot project invalid contract for {skill_info.name!r}: {parsed.error}"
        )
    bound = {skill.name: skill for skill in context.skills}.get(skill_info.name)
    if bound != skill_info:
        raise SkillContractError(
            f"skill {skill_info.name!r} is not the exact contract bound to this projection"
        )
    return parsed.data, parsed.body


def _public_projection_frontmatter(
    parsed_frontmatter: Mapping[str, object],
    gating: bool | None,
) -> dict[str, object]:
    """Remove machine authority fields and apply the public gating override."""
    frontmatter = dict(parsed_frontmatter)
    for key in MACHINE_ONLY_SKILL_FRONTMATTER_KEYS:
        frontmatter.pop(key, None)
    if gating is True:
        frontmatter["disable-model-invocation"] = True
    elif gating is False:
        frontmatter.pop("disable-model-invocation", None)
    return frontmatter


def _rewrite_skill_namespace_reference(
    match: re.Match[str],
    bound_by_name: Mapping[str, SkillContractRecord],
    context: SkillProjectionContext,
) -> str:
    """Project one bundled skill reference into the target backend namespace."""
    target = bound_by_name.get(match.group(1))
    source = (
        target.source
        if target is not None
        else context.catalog.namespace_sources.get(match.group(1))
        if context.catalog is not None
        else None
    )
    if source is None:
        return match.group(0)
    skill_sigil = context.conventions.skill_sigil if context.conventions is not None else "/"
    return f"{skill_sigil}{_agent_skill_namespace(source)}{match.group(1)}"


def _render_projection_content(
    frontmatter: Mapping[str, object],
    body: str,
    context: SkillProjectionContext,
) -> str:
    """Render public YAML/body text, substitutions, and skill references."""
    yaml_text = dump_yaml_str(
        frontmatter,
        sort_keys=False,
        default_flow_style=False,
    ).rstrip("\n")
    content = f"---\n{yaml_text}\n---\n{body}"
    for source, replacement in (context.substitutions or {}).items():
        content = content.replace(source, replacement)
    bound_by_name = {skill.name: skill for skill in context.skills}
    return _SKILL_NAMESPACE_REF_RE.sub(
        lambda match: _rewrite_skill_namespace_reference(match, bound_by_name, context),
        content,
    )


def _apply_semantic_adaptation(
    content: str,
    skill_info: SkillContractRecord,
    context: SkillProjectionContext,
    supplied_adaptation: SkillSemanticAdaptationResult | None,
) -> _SemanticAdaptationOutcome:
    """Attach semantic payload and an optional backend-specific adaptation."""
    semantic_payload: Mapping[str, object] = MappingProxyType({})
    adaptation_payload: Mapping[str, object] = MappingProxyType({})
    semantic_digest = ""
    adaptation_digest = ""
    if skill_info.semantic_plan is not None:
        semantic_payload = skill_info.semantic_plan.canonical_payload
        semantic_digest = skill_info.semantic_plan.digest
    if skill_info.semantic_plan is None or context.backend is None:
        return _SemanticAdaptationOutcome(
            content=content,
            semantic_payload=semantic_payload,
            adaptation_payload=adaptation_payload,
            semantic_digest=semantic_digest,
            adaptation_digest=adaptation_digest,
        )

    adaptation = supplied_adaptation or context.backend.adapt_skill_semantics(
        skill_info.semantic_plan,
        context.adaptation_context,
    )
    adaptation.validate_for(skill_info.semantic_plan, backend=context.backend.name)
    adaptation_payload = adaptation.canonical_payload
    adaptation_digest = adaptation.digest
    if adaptation.instruction_fragments:
        rendered = "\n".join(
            f"- {instruction}" for instruction in adaptation.instruction_fragments
        )
        content = (
            content.rstrip("\n")
            + "\n\n## Backend-adapted semantic execution contract\n\n"
            + rendered
            + "\n\n```json\n"
            + json.dumps(
                {
                    "semantic_digest": semantic_digest,
                    "adaptation_digest": adaptation_digest,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n```"
            + "\n"
        )
    return _SemanticAdaptationOutcome(
        content=content,
        semantic_payload=semantic_payload,
        adaptation_payload=adaptation_payload,
        semantic_digest=semantic_digest,
        adaptation_digest=adaptation_digest,
    )


def _prepare_exploration_replacements(
    migrated: tuple[ExplorationVectorDef, ...],
    context: SkillProjectionContext,
    skill_name: str,
) -> tuple[dict[str, str], str]:
    """Build body replacements and an optional backend exploration preamble."""
    active_vectors = _active_exploration_vectors(migrated, context)
    replacements = {
        vector.id: (
            "This exploration vector is not applicable to the current invocation; "
            "do not dispatch it."
        )
        for vector in migrated
        if vector.applicability not in context.active_exploration_applicabilities
    }
    if not active_vectors or context.provisioning_disposition is False:
        if context.provisioning_disposition is False and active_vectors:
            assert context.backend is not None
            conventions = context.backend.exploration_dispatch_renderer.conventions
            dispatch_role = f"{conventions.role_prefix}{PLUGINLESS_EXPLORER_ROLE}"
            guidance = render_exploration_failure_guidance(
                fallback_dispatch=f"the {dispatch_role!r} specialist"
            )
            replacements.update(
                {
                    vector.id: (
                        "Explorer provisioning is unavailable in this context. "
                        f"{guidance} Launch with {conventions.launcher}("
                        f"{conventions.role_argument}={dispatch_role!r}) for this "
                        "exploration vector's task."
                    )
                    for vector in active_vectors
                    if vector.id not in replacements
                }
            )
        return replacements, ""

    assert context.backend is not None
    plan = _exploration_router_plan(active_vectors)
    materialized = context.backend.exploration_dispatch_renderer.render(
        plan,
        active_vectors,
        include_provisioning_preamble=(
            context.provisioning_disposition is True
            and context.backend.capabilities.session_scoped_explorer_capable
        ),
        launch_context_ref=context.exploration_launch_context_ref or f"skill:{skill_name}",
    )
    if materialized.router_plan_digest != plan.digest:
        raise SkillContractError(
            "backend exploration renderer changed the canonical router-plan identity"
        )
    replacements.update(materialized.replacements)
    return replacements, materialized.preamble


def _insert_exploration_preamble(content: str, preamble: str) -> str:
    """Insert a rendered exploration preamble directly after public frontmatter."""
    if not preamble:
        return content
    frontmatter_close = "---\n"
    first_idx = content.find(frontmatter_close)
    if first_idx < 0:
        return content
    second_idx = content.find(frontmatter_close, first_idx + len(frontmatter_close))
    if second_idx < 0:
        return content
    splice_point = second_idx + len(frontmatter_close)
    return content[:splice_point] + "\n" + preamble + "\n\n" + content[splice_point:]


def _apply_exploration_content(
    content: str,
    skill_info: SkillContractRecord,
    context: SkillProjectionContext,
) -> str:
    """Replace exploration vectors after semantic content has been rendered."""
    vectors = context.exploration_vectors.get(skill_info.name, ())
    if not vectors:
        return content
    if context.backend is None or context.conventions is None:
        raise SkillContractError(
            "exploration-bearing skill projection requires a bound backend and conventions"
        )
    migrated = tuple(
        vector for vector in vectors if vector.disposition is ExplorationVectorDisposition.MIGRATED
    )
    if not migrated:
        return content
    replacements, preamble = _prepare_exploration_replacements(
        migrated,
        context,
        skill_info.name,
    )
    content = replace_exploration_vector_bodies(content, vectors, replacements)
    return _insert_exploration_preamble(content, preamble)


def _append_projected_resources(content: str, skill_info: SkillContractRecord) -> str:
    """Append verified resource bodies without changing their published bytes."""
    for resource_id in skill_info.required_resources:
        resource = load_skill_resource(resource_id)
        expected_digest = skill_info.resource_digests.get(resource_id)
        if expected_digest != resource.digest:
            raise SkillContractError(
                f"skill {skill_info.name!r} resource {resource_id!r} digest is not "
                "bound to the projected contract"
            )
        rows = "" if resource.table_row_count is None else str(resource.table_row_count)
        content += (
            f"\n\n## Provided resource: {resource.title}\n\n"
            f"{resource.summary}\n\n"
            f"{resource.body}"
            f'\n<!-- autoskillit:skill-resource id="{resource.id}" '
            f'digest="{resource.digest}" rows="{rows}" -->'
        )
    return content


def _projection_digests(skill_info: SkillContractRecord, content: str) -> tuple[str, str]:
    """Compute final projected and canonical document digests after all rendering."""
    return (
        hashlib.sha256(content.encode()).hexdigest(),
        skill_info.canonical_digest
        or hashlib.sha256(skill_info.canonical_content.encode()).hexdigest(),
    )


def project_agent_skill_document(
    skill_info: SkillContractRecord,
    context: SkillProjectionContext,
    semantic_adaptation: SkillSemanticAdaptationResult | None = None,
) -> AgentSkillDocument:
    """Remove machine authority fields while preserving public YAML and body."""
    parsed_frontmatter, body = _validate_projection_input(skill_info, context)
    frontmatter = _public_projection_frontmatter(parsed_frontmatter, context.gating)
    content = _render_projection_content(frontmatter, body, context)
    semantic_outcome = _apply_semantic_adaptation(
        content, skill_info, context, semantic_adaptation
    )
    content = semantic_outcome.content
    semantic_payload = semantic_outcome.semantic_payload
    adaptation_payload = semantic_outcome.adaptation_payload
    semantic_digest = semantic_outcome.semantic_digest
    adaptation_digest = semantic_outcome.adaptation_digest
    content = _apply_exploration_content(content, skill_info, context)
    content = _append_projected_resources(content, skill_info)
    projected_digest, canonical_digest = _projection_digests(skill_info, content)
    return AgentSkillDocument(
        content=content,
        projected_digest=projected_digest,
        canonical_digest=canonical_digest,
        source_identity=_source_identity(skill_info),
        semantic_payload=semantic_payload,
        adaptation_payload=adaptation_payload,
        semantic_digest=semantic_digest,
        adaptation_digest=adaptation_digest,
    )


__all__ = [
    "AgentSkillDocument",
    "SkillContractRecord",
    "SkillProjectionContext",
    "_SKILL_NAMESPACE_REF_RE",
    "_active_exploration_vectors",
    "_agent_skill_namespace",
    "_default_base_branch",
    "_direct_install_projection_context",
    "_exploration_router_plan",
    "_source_identity",
    "project_agent_skill_document",
]
