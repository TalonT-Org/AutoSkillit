"""Shared projected-plugin construction and validation primitives."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any, assert_never

import regex as re

from autoskillit.core import (
    MACHINE_ONLY_SKILL_FRONTMATTER_KEYS,
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
    ResolvedSkillAuthority,
    SkillAuthority,
    SkillContractError,
    SkillSource,
    SkillSourceIdentity,
    atomic_write,
    destination_location,
    dump_yaml_str,
    load_agent_definition,
    normalize_parent_sandbox_mode,
    project_agent_tool_name,
    read_versioned_json,
    temp_dir_display_str,
    validate_agent_tool_canonical,
    write_versioned_json,
)
from autoskillit.workspace._projection_cache import is_projected_asset
from autoskillit.workspace.skill_format import parse_frontmatter_content
from autoskillit.workspace.skills import (
    SkillInfo,
    render_skill_invalidities,
    replace_exploration_vector_bodies,
)

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
class SkillProjectionContext:
    """Execution-local inputs that may affect an agent-visible projection."""

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
    explorer_provisioning_eligible: bool = False
    projection_version: int = SKILL_PROJECTION_VERSION

    def __post_init__(self) -> None:
        if type(self.projection_version) is not int or self.projection_version < 1:
            raise SkillContractError("projection version must be a positive integer")
        object.__setattr__(
            self,
            "parent_sandbox_mode",
            normalize_parent_sandbox_mode(self.parent_sandbox_mode),
        )
        if (self.catalog is None) == (self.invocation is None):
            raise SkillContractError(
                "projection context must bind exactly one effective catalog or invocation"
            )
        object.__setattr__(self, "cwd", self.cwd.resolve())
        project_root = self.project_root
        if (
            project_root is None
            and self.invocation is not None
            and self.invocation.project_root is not None
        ):
            project_root = self.invocation.project_root
        if project_root is not None:
            object.__setattr__(self, "project_root", project_root.resolve())
        if self.backend is not None:
            backend_conventions = self.backend.conventions
            if self.conventions is None:
                object.__setattr__(self, "conventions", backend_conventions)
            elif self.conventions != backend_conventions:
                raise SkillContractError("projection context conventions do not match its backend")
        elif self.conventions is not None:
            raise SkillContractError("projection context conventions require a bound backend")
        if self.exploration_launch_context_ref is not None and (
            not isinstance(self.exploration_launch_context_ref, str)
            or not self.exploration_launch_context_ref.strip()
        ):
            raise SkillContractError("exploration launch-context reference must be non-empty text")
        if self.resolved_exploration_profile is RepositoryProfileId.AUTO:
            raise SkillContractError("resolved exploration profile cannot remain auto")
        active_applicabilities = frozenset(self.active_exploration_applicabilities)
        if ExplorationVectorApplicabilityId.ALWAYS not in active_applicabilities:
            raise SkillContractError("active exploration applicability must include always")
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
        projection_version=projection_version,
    )


def project_agent_skill_document(
    skill_info: SkillContractRecord,
    context: SkillProjectionContext,
) -> AgentSkillDocument:
    """Remove machine authority fields while preserving public YAML and body."""
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

    frontmatter = dict(parsed.data)
    for key in MACHINE_ONLY_SKILL_FRONTMATTER_KEYS:
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
    bound_by_name = {skill.name: skill for skill in context.skills}

    def _rewrite_namespace(match: re.Match[str]) -> str:
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

    content = _SKILL_NAMESPACE_REF_RE.sub(_rewrite_namespace, content)

    semantic_payload: Mapping[str, object] = MappingProxyType({})
    adaptation_payload: Mapping[str, object] = MappingProxyType({})
    semantic_digest = ""
    adaptation_digest = ""
    if skill_info.semantic_plan is not None:
        semantic_payload = skill_info.semantic_plan.canonical_payload
        semantic_digest = skill_info.semantic_plan.digest
    if skill_info.semantic_plan is not None and context.backend is not None:
        adaptation = context.backend.adapt_skill_semantics(skill_info.semantic_plan)
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
    vectors = context.exploration_vectors.get(skill_info.name, ())
    if vectors:
        if context.backend is None or context.conventions is None:
            raise SkillContractError(
                "exploration-bearing skill projection requires a bound backend and conventions"
            )
        migrated = tuple(
            vector
            for vector in vectors
            if vector.disposition is ExplorationVectorDisposition.MIGRATED
        )
        if migrated:
            active_vectors = _active_exploration_vectors(vectors, context)
            replacements = {
                vector.id: (
                    "This exploration vector is not applicable to the current invocation; "
                    "do not dispatch it."
                )
                for vector in migrated
                if vector.applicability not in context.active_exploration_applicabilities
            }
            if not active_vectors or not context.explorer_provisioning_eligible:
                if not context.explorer_provisioning_eligible and active_vectors:
                    replacements.update(
                        {
                            vector.id: (
                                "Explorer provisioning is unavailable in this context; "
                                "do not dispatch this exploration vector."
                            )
                            for vector in active_vectors
                            if vector.id not in replacements
                        }
                    )
                content = replace_exploration_vector_bodies(content, vectors, replacements)
            else:
                plan = _exploration_router_plan(active_vectors)
                materialized = context.backend.exploration_dispatch_renderer.render(
                    plan,
                    active_vectors,
                    launch_context_ref=(
                        context.exploration_launch_context_ref or f"skill:{skill_info.name}"
                    ),
                )
                if materialized.router_plan_digest != plan.digest:
                    raise SkillContractError(
                        "backend exploration renderer changed the canonical router-plan identity"
                    )
                replacements.update(materialized.replacements)
                content = replace_exploration_vector_bodies(
                    content,
                    vectors,
                    replacements,
                )
                # Splice the preamble as the first body content after the
                # frontmatter close delimiter. This placement is mode-neutral
                # and section-agnostic — it precedes every marker regardless
                # of which section the marker belongs to.
                if materialized.preamble:
                    frontmatter_close = "---\n"
                    # Find the second occurrence of "---\n" (closing delimiter)
                    first_idx = content.find(frontmatter_close)
                    if first_idx >= 0:
                        second_idx = content.find(
                            frontmatter_close, first_idx + len(frontmatter_close)
                        )
                        if second_idx >= 0:
                            splice_point = second_idx + len(frontmatter_close)
                            content = (
                                content[:splice_point]
                                + "\n"
                                + materialized.preamble
                                + "\n\n"
                                + content[splice_point:]
                            )

    projected_digest = hashlib.sha256(content.encode()).hexdigest()
    canonical_digest = (
        skill_info.canonical_digest
        or hashlib.sha256(skill_info.canonical_content.encode()).hexdigest()
    )
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


def _skill_sequence(
    skills_or_catalog: EffectiveSkillCatalogAuthority | Iterable[SkillContractRecord],
) -> tuple[SkillContractRecord, ...]:
    if isinstance(skills_or_catalog, EffectiveSkillCatalogAuthority):
        return skills_or_catalog.skills
    return tuple(skills_or_catalog)


def _replace_directory(staging: Path, destination: Path) -> None:
    if destination.is_symlink() or (destination.exists() and not destination.is_dir()):
        destination.unlink()
    elif destination.exists():
        shutil.rmtree(destination)
    os.replace(staging, destination)


def materialize_agent_skill_tree(
    destination: Path,
    skills_or_catalog: EffectiveSkillCatalogAuthority | Iterable[SkillContractRecord],
    context: SkillProjectionContext,
) -> dict[str, AgentSkillDocument]:
    """Replace *destination* with an exact tree of agent-safe skill projections."""
    destination = Path(destination)
    if not destination.name:
        raise SkillContractError("projected skill destination must not be a filesystem root")
    skills = _skill_sequence(skills_or_catalog)
    resolved_destination = destination_location(destination)
    for skill in skills:
        if (
            isinstance(skill, ResolvedSkillAuthority)
            and resolved_destination in skill.path.resolve().parents
        ):
            raise SkillContractError(
                f"projected skill destination contains canonical source for {skill.name!r}"
            )
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{destination.name}.projection-",
            dir=destination.parent,
        )
    )
    documents: dict[str, AgentSkillDocument] = {}
    try:
        for skill in sorted(skills, key=lambda item: item.name):
            if (
                not skill.name
                or skill.name in {".", ".."}
                or "/" in skill.name
                or "\\" in skill.name
                or "\x00" in skill.name
            ):
                raise SkillContractError(f"invalid projected skill name: {skill.name!r}")
            if skill.name in documents:
                raise SkillContractError(f"duplicate projected skill name: {skill.name!r}")
            document = project_agent_skill_document(skill, context)
            skill_dir = staging / skill.name
            skill_dir.mkdir()
            atomic_write(skill_dir / "SKILL.md", document.content)
            documents[skill.name] = document
        _replace_directory(staging, destination)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return documents


def _copy_non_skill_plugin_assets(
    source_root: Path,
    destination: Path,
    *,
    top_level: bool = True,
) -> None:
    for entry in source_root.iterdir():
        if not is_projected_asset(entry, top_level=top_level):
            continue
        if entry.is_symlink():
            raise SkillContractError(f"plugin asset must not be a symlink: {entry}")
        target = destination / entry.name
        if entry.is_dir():
            target.mkdir()
            _copy_non_skill_plugin_assets(entry, target, top_level=False)
        elif entry.is_file():
            shutil.copy2(entry, target)
        else:
            raise SkillContractError(f"plugin asset must be a regular file or directory: {entry}")


def _manifest_skill_entry(
    skill: SkillContractRecord,
    document: AgentSkillDocument,
) -> dict[str, Any]:
    role = skill.execution_role
    return {
        "canonical_digest": document.canonical_digest,
        "projected_digest": document.projected_digest,
        "source": document.source_identity.origin.value,
        "logical_name": document.source_identity.logical_name,
        "search_dir": document.source_identity.search_dir,
        "precedence": document.source_identity.precedence,
        "uses_capabilities": sorted(skill.uses_capabilities),
        "execution_role": role.value if role is not None else None,
        "activate_deps": list(skill.activate_deps),
    }


def _projection_skills_manifest(
    skill_infos: tuple[SkillContractRecord, ...],
    documents: Mapping[str, AgentSkillDocument],
) -> dict[str, dict[str, Any]]:
    skill_by_name = {skill.name: skill for skill in skill_infos}
    return {
        name: _manifest_skill_entry(skill_by_name[name], document)
        for name, document in documents.items()
    }


def _render_agent_definitions(agents_dir: Path, mcp_tool_prefix: str) -> None:
    """Rewrite MCP tool prefixes in copied agent definitions.

    Performs a format-preserving line-level rewrite of the ``tools:`` frontmatter
    line only: each DIRECT-canonical MCP tool name is projected to the target
    prefix.  Non-MCP tools pass through unchanged.  The source definitions are
    validated before projection — a non-canonical MCP tool raises immediately.

    Each rendered file is re-parsed via the canonical fail-closed loader to
    assert semantic equality with the source definition (modulo tools prefix).
    """
    if not agents_dir.is_dir():
        return
    for path in sorted(agents_dir.glob("*.md")):
        if path.name in {"AGENTS.md", "CLAUDE.md"}:
            continue
        content = path.read_text(encoding="utf-8")
        lines = content.splitlines(keepends=True)
        tools_line_idx: int | None = None
        for idx, line in enumerate(lines):
            stripped = line.lstrip()
            if stripped.startswith("tools:"):
                tools_line_idx = idx
                break
        if tools_line_idx is None:
            continue

        source_def = load_agent_definition(path)
        has_mcp_tools = any(tool.startswith("mcp__") for tool in source_def.tools)
        if not has_mcp_tools:
            continue

        for tool in source_def.tools:
            if tool.startswith("mcp__"):
                validate_agent_tool_canonical(tool)

        projected_tools = tuple(
            project_agent_tool_name(tool, mcp_tool_prefix) for tool in source_def.tools
        )
        new_tools_value = "[" + ", ".join(projected_tools) + "]"
        indent = lines[tools_line_idx][
            : len(lines[tools_line_idx]) - len(lines[tools_line_idx].lstrip())
        ]
        lines[tools_line_idx] = f"{indent}tools: {new_tools_value}\n"
        rendered = "".join(lines)
        atomic_write(path, rendered)

        rendered_def = load_agent_definition(path)
        if rendered_def.name != source_def.name:
            raise SkillContractError(
                f"agent definition name changed after rendering: "
                f"{source_def.name!r} → {rendered_def.name!r}"
            )
        if rendered_def.body != source_def.body:
            raise SkillContractError(
                f"agent definition body changed after rendering: {source_def.name!r}"
            )
        if (
            tuple(project_agent_tool_name(tool, mcp_tool_prefix) for tool in source_def.tools)
            != rendered_def.tools
        ):
            raise SkillContractError(
                f"agent definition tool projection mismatch after rendering: {source_def.name!r}"
            )


def materialize_sanitized_plugin_root(
    source_root: Path,
    destination: Path,
    catalog: EffectiveSkillCatalogAuthority | Iterable[SkillContractRecord],
    context: SkillProjectionContext,
    *,
    mcp_tool_prefix: str,
) -> Path:
    """Copy plugin assets and replace its public skills with safe projections.

    The returned manifest path is deliberately a sibling of the public plugin
    root so machine provenance never becomes agent-visible plugin content.
    """
    source_root = Path(source_root).resolve()
    destination = Path(destination)
    if not destination.name:
        raise SkillContractError("sanitized plugin destination must not be a filesystem root")
    resolved_destination = destination_location(destination)
    if source_root == resolved_destination or source_root in resolved_destination.parents:
        raise SkillContractError("sanitized plugin destination must be outside its source root")
    if not source_root.is_dir():
        raise FileNotFoundError(f"plugin source root not found: {source_root}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{destination.name}.plugin-",
            dir=destination.parent,
        )
    )
    try:
        _copy_non_skill_plugin_assets(source_root, staging)
        _render_agent_definitions(staging / "agents", mcp_tool_prefix)
        skill_infos = _skill_sequence(catalog)
        documents = materialize_agent_skill_tree(staging / "skills", skill_infos, context)
        _replace_directory(staging, destination)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    manifest_path = destination.parent / f".{destination.name}.autoskillit-projection.json"
    manifest = {
        "schema_version": 1,
        "projection_version": context.projection_version,
        "skills": _projection_skills_manifest(skill_infos, documents),
    }
    write_versioned_json(manifest_path, manifest, schema_version=1)
    return manifest_path


def validate_sanitized_plugin_artifact(
    source_root: Path,
    public_root: Path,
    manifest_path: Path,
    skills_or_catalog: EffectiveSkillCatalogAuthority | Iterable[SkillContractRecord],
    *,
    require_sources_within_root: bool = True,
    manifest_schema_version: int = 1,
) -> tuple[str, ...]:
    """Return all integrity errors for a sanitized public plugin artifact."""
    errors: list[str] = []
    source_root = Path(source_root).resolve()
    public_root = Path(public_root)
    manifest_path = Path(manifest_path)
    try:
        manifest_path.resolve().relative_to(public_root.resolve())
    except ValueError:
        pass
    else:
        errors.append("projection manifest must be outside the public plugin root")
    infos = _skill_sequence(skills_or_catalog)
    expected: dict[str, SkillContractRecord] = {}
    for info in infos:
        if info.name in expected:
            errors.append(f"duplicate expected skill: {info.name}")
        expected[info.name] = info
        if require_sources_within_root and isinstance(info, SkillInfo):
            try:
                info.path.resolve().relative_to(source_root)
            except ValueError:
                errors.append(f"skill source is outside plugin source root: {info.name}")
        elif require_sources_within_root:
            errors.append(f"path-free catalog cannot prove source containment: {info.name}")

    manifest = read_versioned_json(manifest_path, manifest_schema_version)
    if manifest is None:
        return tuple([*errors, "projection manifest is unreadable or has an unsupported schema"])
    if manifest.get("schema_version") != manifest_schema_version:
        errors.append(f"projection manifest schema_version must be {manifest_schema_version}")
    projection_version = manifest.get("projection_version")
    if type(projection_version) is not int or projection_version < 1:
        errors.append("projection manifest projection_version must be a positive integer")
    manifest_skills = manifest.get("skills")
    if not isinstance(manifest_skills, dict):
        return tuple([*errors, "projection manifest skills must be a JSON object"])

    public_skills = public_root / "skills"
    actual_names: set[str] = set()
    if (public_root / "skills_extended").exists():
        errors.append("public plugin must not contain a canonical skills_extended tree")
    if public_root.is_dir():
        for entry in public_root.rglob("*"):
            if entry.is_symlink():
                errors.append(f"public plugin asset is a symlink: {entry}")
    if not public_skills.is_dir() or public_skills.is_symlink():
        errors.append("public plugin skills root is missing or is a symlink")
    else:
        for entry in public_skills.iterdir():
            if entry.is_symlink():
                errors.append(f"public skill entry is a symlink: {entry.name}")
                continue
            if not entry.is_dir():
                errors.append(f"public skills root contains a non-directory entry: {entry.name}")
                continue
            actual_names.add(entry.name)
            children = {child.name for child in entry.iterdir()}
            if children != {"SKILL.md"} or not (entry / "SKILL.md").is_file():
                errors.append(f"public skill directory must contain only SKILL.md: {entry.name}")

    expected_names = set(expected)
    manifest_names = {str(name) for name in manifest_skills}
    if actual_names != expected_names:
        errors.append(
            "public skill inventory mismatch: "
            f"missing={sorted(expected_names - actual_names)!r}, "
            f"unexpected={sorted(actual_names - expected_names)!r}"
        )
    if manifest_names != expected_names:
        errors.append(
            "manifest skill inventory mismatch: "
            f"missing={sorted(expected_names - manifest_names)!r}, "
            f"unexpected={sorted(manifest_names - expected_names)!r}"
        )

    for name in sorted(expected_names & actual_names & manifest_names):
        info = expected[name]
        skill_md = public_skills / name / "SKILL.md"
        if skill_md.is_symlink():
            errors.append(f"public SKILL.md is a symlink: {name}")
            continue
        try:
            content = skill_md.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            errors.append(f"public SKILL.md is unreadable for {name}: {exc}")
            continue
        parsed = parse_frontmatter_content(content)
        if not parsed.is_valid or parsed.data is None:
            errors.append(f"public SKILL.md frontmatter is invalid for {name}: {parsed.error}")
        else:
            leaked = sorted(MACHINE_ONLY_SKILL_FRONTMATTER_KEYS & parsed.data.keys())
            if leaked:
                errors.append(f"public SKILL.md exposes machine fields for {name}: {leaked!r}")

        entry = manifest_skills[name]
        if not isinstance(entry, dict):
            errors.append(f"manifest entry must be a JSON object for {name}")
            continue
        projected_digest = hashlib.sha256(content.encode()).hexdigest()
        canonical_digest = (
            info.canonical_digest or hashlib.sha256(info.canonical_content.encode()).hexdigest()
        )
        expected_entry = {
            "projected_digest": projected_digest,
            "canonical_digest": canonical_digest,
            "source": info.source.value,
            "logical_name": info.name,
            "search_dir": info.source_identity.search_dir,
            "precedence": info.source_identity.precedence,
            "uses_capabilities": sorted(info.uses_capabilities),
            "execution_role": (
                info.execution_role.value if info.execution_role is not None else None
            ),
            "activate_deps": list(info.activate_deps),
        }
        for field_name, value in expected_entry.items():
            if entry.get(field_name) != value:
                errors.append(
                    f"manifest {field_name} mismatch for {name}: "
                    f"expected {value!r}, got {entry.get(field_name)!r}"
                )
    return tuple(errors)
