"""Projection of internal skill contracts into agent-visible documents.

Two module-wide policies, stated once so call sites cannot drift:

**The projection source is always ``pkg_root()``.** It is the code currently
executing, so it cannot be stale. Nothing here ever reads a plugin root out of
third-party-owned mutable state (``installed_plugins.json``, the Claude Code
plugin cache): such a path is a *derived copy* that its owner is free to
version, relocate, or garbage-collect, and treating it as a source silently
produces mixed-version sessions — old recipes/agents/hooks against new code.

**A projection destination's prior content is always replaced; only its
location is contract-relevant.** Containment checks therefore use
``destination_location()``, never ``Path.resolve()`` — resolving follows a
final-component symlink and turns "where may I write?" into "what does this
currently point at?", which is a different and wrong question.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, assert_never, cast

import regex as re

from autoskillit.core import (
    MACHINE_ONLY_SKILL_FRONTMATTER_KEYS,
    SKILL_PROJECTION_VERSION,
    ArtifactLease,
    ArtifactLeaseContention,
    BackendConventions,
    CodingAgentBackend,
    DirectInstall,
    EffectiveSkillCatalogAuthority,
    EffectiveSkillInvocationAuthority,
    PluginArtifactContentionError,
    PluginArtifactIdentity,
    PluginArtifactPublicationError,
    PluginArtifactValidationError,
    PluginLaunchBinding,
    PluginLoadMode,
    ResolvedSkillAuthority,
    SkillAuthority,
    SkillContractError,
    SkillExecutionRole,
    SkillResolver,
    SkillSource,
    SkillSourceIdentity,
    SkillSourceRef,
    SkillVisibilitySpec,
    _InstallLock,
    atomic_write,
    destination_location,
    dump_yaml_str,
    pkg_root,
    read_versioned_json,
    temp_dir_display_str,
    write_versioned_json,
)
from autoskillit.workspace._projection_cache import (
    ProjectedPluginRetirementOwner,
    ProjectionCacheKey,
    is_projected_asset,
    public_plugin_asset_digest,
)
from autoskillit.workspace.skill_format import parse_frontmatter_content
from autoskillit.workspace.skills import (
    EffectiveSkillCatalog,
    SkillCatalogEntry,
    SkillInfo,
    _skill_info_from_frontmatter,
)

__all__ = [
    "AgentSkillDocument",
    "EffectiveSkillDispatchContract",
    "EffectiveSkillDispatchPreparation",
    "ProjectedPluginArtifactAuthority",
    "ProjectedPluginRetirementOwner",
    "SkillProjectionContext",
    "build_effective_skill_dispatch_contract",
    "finalize_effective_skill_dispatch",
    "materialize_agent_skill_tree",
    "materialize_sanitized_plugin_root",
    "prepare_catalog_skill_dispatch",
    "prepare_effective_skill_dispatch",
    "project_agent_skill_document",
    "project_default_plugin_authority",
    "project_direct_install_authority",
    "validate_sanitized_plugin_artifact",
]
_SKILL_NAMESPACE_REF_RE = re.compile(r"/autoskillit:([a-z][a-z0-9-]*)")
_PLUGIN_ARTIFACT_MANIFEST_SCHEMA_VERSION = 2


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
    projection_version: int = SKILL_PROJECTION_VERSION

    def __post_init__(self) -> None:
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
        substitutions={
            "{{AUTOSKILLIT_TEMP}}": temp_dir_display_str(None),
            "{{AUTOSKILLIT_SCRIPTS}}": str(destination / "recipes" / "scripts"),
            "{{DEFAULT_BASE_BRANCH}}": default_base_branch,
        },
        projection_version=projection_version,
    )


@dataclass(frozen=True, slots=True)
class EffectiveSkillDispatchContract:
    """Immutable execution-bound contract carried through headless dispatch."""

    resolved_command: str
    projection_context: SkillProjectionContext
    invocation: EffectiveSkillInvocationAuthority | None
    catalog: EffectiveSkillCatalogAuthority | None
    root_name: str | None
    member_names: tuple[str, ...]
    execution_role: SkillExecutionRole
    capability_union: frozenset[str]
    source_identities: Mapping[str, SkillSourceIdentity]
    canonical_digests: Mapping[str, str]
    projected_digests: Mapping[str, str]
    projected_artifacts: Mapping[str, str]
    projection_version: int
    project_root: str | None
    cwd: str
    backend: str | None
    artifact_paths: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.resolved_command:
            raise SkillContractError("effective dispatch requires a resolved command")
        if (self.invocation is None) == (self.catalog is None):
            raise SkillContractError(
                "effective dispatch must bind exactly one invocation or catalog"
            )
        expected_names = tuple(skill.name for skill in self.projection_context.skills)
        if self.member_names != expected_names:
            raise SkillContractError("effective dispatch member order does not match projection")
        expected_set = set(expected_names)
        for field_name, mapping in (
            ("source identities", self.source_identities),
            ("canonical digests", self.canonical_digests),
            ("projected digests", self.projected_digests),
            ("projected artifacts", self.projected_artifacts),
        ):
            if set(mapping) != expected_set:
                raise SkillContractError(
                    f"effective dispatch {field_name} do not match its members"
                )
            object.__setattr__(self, field_name.replace(" ", "_"), MappingProxyType(dict(mapping)))


@dataclass(frozen=True, slots=True)
class EffectiveSkillDispatchPreparation:
    """Backend-neutral semantic inputs awaiting one exact launch binding."""

    resolved_command: str
    cwd: Path
    project_root: Path | None
    default_base_branch: str
    catalog: EffectiveSkillCatalogAuthority | None = None
    invocation: EffectiveSkillInvocationAuthority | None = None

    def __post_init__(self) -> None:
        if not self.resolved_command:
            raise SkillContractError("dispatch preparation requires a resolved command")
        if (self.catalog is None) == (self.invocation is None):
            raise SkillContractError(
                "dispatch preparation must bind exactly one catalog or invocation"
            )
        object.__setattr__(self, "cwd", Path(self.cwd))
        if self.project_root is not None:
            object.__setattr__(self, "project_root", Path(self.project_root))

    def finalize(
        self,
        *,
        backend: CodingAgentBackend,
        binding: PluginLaunchBinding,
    ) -> EffectiveSkillDispatchContract:
        return _finalize_effective_skill_dispatch(
            self,
            backend=backend,
            binding=binding,
        )


def build_effective_skill_dispatch_contract(
    resolved_command: str,
    projection_context: SkillProjectionContext,
    *,
    artifact_paths: Iterable[str] = (),
) -> EffectiveSkillDispatchContract:
    """Freeze role, identity, digests, and projected bytes for executor handoff."""
    documents = {
        skill.name: project_agent_skill_document(skill, projection_context)
        for skill in projection_context.skills
    }
    invocation = projection_context.invocation
    catalog = projection_context.catalog
    if invocation is not None:
        execution_role = invocation.execution_role
    else:
        assert catalog is not None
        execution_role = catalog.execution_role
    capability_union = frozenset().union(
        *(skill.uses_capabilities for skill in projection_context.skills)
    )
    backend = projection_context.backend
    return EffectiveSkillDispatchContract(
        resolved_command=resolved_command,
        projection_context=projection_context,
        invocation=invocation,
        catalog=catalog,
        root_name=invocation.root.name if invocation is not None else None,
        member_names=tuple(skill.name for skill in projection_context.skills),
        execution_role=execution_role,
        capability_union=capability_union,
        source_identities={name: document.source_identity for name, document in documents.items()},
        canonical_digests={
            name: document.canonical_digest for name, document in documents.items()
        },
        projected_digests={
            name: document.projected_digest for name, document in documents.items()
        },
        projected_artifacts={name: document.content for name, document in documents.items()},
        projection_version=projection_context.projection_version,
        project_root=(
            str(projection_context.project_root)
            if projection_context.project_root is not None
            else None
        ),
        cwd=str(projection_context.cwd),
        backend=backend.name if backend is not None else None,
        artifact_paths=tuple(artifact_paths),
    )


def _finalize_effective_skill_dispatch(
    preparation: EffectiveSkillDispatchPreparation,
    *,
    backend: CodingAgentBackend,
    binding: PluginLaunchBinding,
) -> EffectiveSkillDispatchContract:
    """Bind backend conventions only after the launch artifact is reader-owned."""
    if binding.closed:
        raise PluginArtifactValidationError(
            "cannot finalize dispatch with a closed plugin launch binding"
        )
    if binding.plugin_dir is not None and binding.plugin_dir != binding.identity.managed_path:
        raise PluginArtifactValidationError(
            "plugin launch path does not match its leased artifact identity"
        )
    destination = binding.plugin_dir or binding.identity.managed_path
    context = SkillProjectionContext(
        cwd=preparation.cwd,
        project_root=preparation.project_root,
        catalog=preparation.catalog,
        invocation=preparation.invocation,
        backend=backend,
        conventions=backend.conventions,
        substitutions={
            "{{AUTOSKILLIT_TEMP}}": temp_dir_display_str(None),
            "{{AUTOSKILLIT_SCRIPTS}}": str(destination / "recipes" / "scripts"),
            "{{DEFAULT_BASE_BRANCH}}": preparation.default_base_branch,
        },
    )
    return build_effective_skill_dispatch_contract(
        preparation.resolved_command,
        context,
        artifact_paths=(),
    )


def finalize_effective_skill_dispatch(
    preparation: EffectiveSkillDispatchPreparation,
    *,
    backend: CodingAgentBackend,
    binding: PluginLaunchBinding,
) -> EffectiveSkillDispatchContract:
    """Workspace convenience wrapper around preparation-owned finalization."""
    return preparation.finalize(backend=backend, binding=binding)


def project_agent_skill_document(
    skill_info: SkillContractRecord,
    context: SkillProjectionContext,
) -> AgentSkillDocument:
    """Remove machine authority fields while preserving public YAML and body."""
    if skill_info.invalid_reason is not None:
        raise SkillContractError(
            f"cannot project invalid contract for {skill_info.name!r}: {skill_info.invalid_reason}"
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


def materialize_sanitized_plugin_root(
    source_root: Path,
    destination: Path,
    catalog: EffectiveSkillCatalogAuthority | Iterable[SkillContractRecord],
    context: SkillProjectionContext,
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
    if not isinstance(manifest.get("projection_version"), int):
        errors.append("projection manifest projection_version must be an integer")
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
        for field, value in expected_entry.items():
            if entry.get(field) != value:
                errors.append(
                    f"manifest {field} mismatch for {name}: "
                    f"expected {value!r}, got {entry.get(field)!r}"
                )
    return tuple(errors)


@dataclass(frozen=True, slots=True)
class _ProjectedArtifactPlan:
    source_root: Path
    destination: Path
    manifest_path: Path
    lease_path: Path
    semantic_key: str
    catalog: EffectiveSkillCatalogAuthority
    validation_catalog: tuple[SkillContractRecord, ...] | EffectiveSkillCatalogAuthority
    require_sources_within_root: bool
    context: SkillProjectionContext


@dataclass(frozen=True, slots=True)
class _StagedProjectedArtifact:
    root: Path
    manifest: Path
    identity: PluginArtifactIdentity


def _projected_plugin_artifact_digest(public_root: Path) -> str:
    """Digest the complete published tree, including its inventory."""
    public_root = Path(public_root)
    if not public_root.is_dir() or public_root.is_symlink():
        raise PluginArtifactValidationError(
            f"projected plugin root is missing or is not a directory: {public_root}"
        )
    digest = hashlib.sha256()
    try:
        entries = sorted(
            public_root.rglob("*"),
            key=lambda path: path.relative_to(public_root).as_posix(),
        )
        for entry in entries:
            relative = entry.relative_to(public_root).as_posix()
            if entry.is_symlink():
                raise PluginArtifactValidationError(
                    f"projected plugin artifact contains a symlink: {entry}"
                )
            if entry.is_dir():
                kind = b"d"
                payload_digest = b""
            elif entry.is_file():
                kind = b"f"
                with entry.open("rb") as handle:
                    payload_digest = hashlib.file_digest(handle, "sha256").digest()
            else:
                raise PluginArtifactValidationError(
                    f"projected plugin artifact contains a special file: {entry}"
                )
            digest.update(kind)
            digest.update(b"\0")
            digest.update(relative.encode())
            digest.update(b"\0")
            digest.update(payload_digest)
            digest.update(b"\0")
    except PluginArtifactValidationError:
        raise
    except OSError as exc:
        raise PluginArtifactValidationError(
            f"projected plugin artifact cannot be digested: {public_root}"
        ) from exc
    return digest.hexdigest()


def _stage_projected_plugin_artifact(
    plan: _ProjectedArtifactPlan,
) -> _StagedProjectedArtifact:
    """Build one complete, unpublished artifact incarnation."""
    staging_root = Path(
        tempfile.mkdtemp(
            prefix=f".{plan.destination.name}.plugin-",
            dir=plan.destination.parent,
        )
    )
    staging_manifest = plan.destination.parent / (
        f".{plan.destination.name}.manifest-{uuid.uuid4()}.json"
    )
    try:
        _copy_non_skill_plugin_assets(plan.source_root, staging_root)
        skill_infos = _skill_sequence(plan.catalog)
        documents = materialize_agent_skill_tree(
            staging_root / "skills",
            skill_infos,
            plan.context,
        )
        artifact_digest = _projected_plugin_artifact_digest(staging_root)
        identity = PluginArtifactIdentity(
            semantic_key=plan.semantic_key,
            incarnation_id=str(uuid.uuid4()),
            manifest_schema_version=_PLUGIN_ARTIFACT_MANIFEST_SCHEMA_VERSION,
            artifact_digest=artifact_digest,
            managed_path=plan.destination,
            manifest_path=plan.manifest_path,
        )
        write_versioned_json(
            staging_manifest,
            {
                "schema_version": identity.manifest_schema_version,
                "projection_version": plan.context.projection_version,
                "semantic_key": identity.semantic_key,
                "incarnation_id": identity.incarnation_id,
                "artifact_digest": identity.artifact_digest,
                "skills": _projection_skills_manifest(skill_infos, documents),
            },
            schema_version=identity.manifest_schema_version,
            strict_durability=True,
        )
        return _StagedProjectedArtifact(
            root=staging_root,
            manifest=staging_manifest,
            identity=identity,
        )
    except BaseException:
        shutil.rmtree(staging_root, ignore_errors=True)
        if staging_manifest.is_symlink() or staging_manifest.is_file():
            staging_manifest.unlink()
        raise


def _publish_projected_plugin_root(
    staged: _StagedProjectedArtifact,
    destination: Path,
) -> None:
    """Atomically publish staged public bytes at their stable semantic path."""
    _replace_directory(staged.root, destination)


def _publish_projected_plugin_manifest(
    staged: _StagedProjectedArtifact,
    manifest_path: Path,
) -> None:
    """Publish identity last, so incomplete root publication is never trusted."""
    if manifest_path.exists() and not (manifest_path.is_file() or manifest_path.is_symlink()):
        raise PluginArtifactPublicationError(
            f"projected plugin manifest destination is not a file: {manifest_path}"
        )
    os.replace(staged.manifest, manifest_path)


def _manifest_identity(plan: _ProjectedArtifactPlan) -> PluginArtifactIdentity:
    if plan.manifest_path.is_symlink() or not plan.manifest_path.is_file():
        raise PluginArtifactValidationError(
            f"projected plugin identity manifest is not a regular file: {plan.manifest_path}"
        )
    manifest = read_versioned_json(
        plan.manifest_path,
        _PLUGIN_ARTIFACT_MANIFEST_SCHEMA_VERSION,
    )
    if manifest is None:
        raise PluginArtifactValidationError(
            f"projected plugin identity manifest is unreadable: {plan.manifest_path}"
        )
    semantic_key = manifest.get("semantic_key")
    incarnation_id = manifest.get("incarnation_id")
    artifact_digest = manifest.get("artifact_digest")
    if semantic_key != plan.semantic_key:
        raise PluginArtifactValidationError(
            f"projected plugin semantic key mismatch: {plan.manifest_path}"
        )
    if not isinstance(incarnation_id, str):
        raise PluginArtifactValidationError(
            f"projected plugin incarnation is missing: {plan.manifest_path}"
        )
    try:
        parsed_incarnation = uuid.UUID(incarnation_id)
    except ValueError as exc:
        raise PluginArtifactValidationError(
            f"projected plugin incarnation is invalid: {plan.manifest_path}"
        ) from exc
    if str(parsed_incarnation) != incarnation_id:
        raise PluginArtifactValidationError(
            f"projected plugin incarnation is not canonical: {plan.manifest_path}"
        )
    if not isinstance(artifact_digest, str) or len(artifact_digest) != 64:
        raise PluginArtifactValidationError(
            f"projected plugin digest is invalid: {plan.manifest_path}"
        )
    if manifest.get("projection_version") != plan.context.projection_version:
        raise PluginArtifactValidationError(
            f"projected plugin version mismatch: {plan.manifest_path}"
        )
    return PluginArtifactIdentity(
        semantic_key=semantic_key,
        incarnation_id=incarnation_id,
        manifest_schema_version=_PLUGIN_ARTIFACT_MANIFEST_SCHEMA_VERSION,
        artifact_digest=artifact_digest,
        managed_path=plan.destination,
        manifest_path=plan.manifest_path,
    )


def _validate_published_plugin_artifact(
    plan: _ProjectedArtifactPlan,
    *,
    expected_identity: PluginArtifactIdentity | None = None,
) -> PluginArtifactIdentity:
    """Validate both semantic content and exact physical incarnation."""
    identity = _manifest_identity(plan)
    errors = validate_sanitized_plugin_artifact(
        plan.source_root,
        plan.destination,
        plan.manifest_path,
        plan.validation_catalog,
        require_sources_within_root=plan.require_sources_within_root,
        manifest_schema_version=_PLUGIN_ARTIFACT_MANIFEST_SCHEMA_VERSION,
    )
    if errors:
        raise PluginArtifactValidationError(
            "projected plugin content validation failed: " + "; ".join(errors)
        )
    actual_digest = _projected_plugin_artifact_digest(plan.destination)
    if actual_digest != identity.artifact_digest:
        raise PluginArtifactValidationError(
            "projected plugin digest mismatch: "
            f"expected {identity.artifact_digest}, got {actual_digest}"
        )
    if _manifest_identity(plan) != identity:
        raise PluginArtifactValidationError("projected plugin identity changed during validation")
    if expected_identity is not None and identity != expected_identity:
        raise PluginArtifactValidationError(
            "projected plugin incarnation changed before reader lease acquisition"
        )
    return identity


def _try_validate_published_plugin_artifact(
    plan: _ProjectedArtifactPlan,
) -> PluginArtifactIdentity | None:
    try:
        return _validate_published_plugin_artifact(plan)
    except PluginArtifactValidationError:
        return None
    except Exception as exc:
        raise PluginArtifactValidationError(
            f"projected plugin validation failed: {plan.semantic_key}"
        ) from exc


@dataclass(frozen=True, slots=True)
class ProjectedPluginArtifactAuthority:
    """Lazy owner of projected plugin publication and per-launch reader leases."""

    direct_install: DirectInstall
    projection_version: int = SKILL_PROJECTION_VERSION
    base_branch: str | None = None
    catalog: EffectiveSkillCatalogAuthority | None = None
    namespace_sources: Mapping[str, SkillSource] | None = None
    cwd: Path | None = None

    def __post_init__(self) -> None:
        if self.projection_version < 1:
            raise ValueError("projection_version must be positive")
        if self.namespace_sources is not None:
            object.__setattr__(
                self,
                "namespace_sources",
                MappingProxyType(dict(self.namespace_sources)),
            )
        if self.cwd is not None:
            object.__setattr__(self, "cwd", Path(self.cwd))

    def _plan(self, backend: CodingAgentBackend) -> _ProjectedArtifactPlan:
        source_root = self.direct_install.plugin_dir.resolve()
        bundled_root = source_root / "skills"
        if not source_root.is_dir():
            raise PluginArtifactPublicationError(
                f"direct plugin root does not exist: {source_root}"
            )
        if self.catalog is None and (not bundled_root.is_dir() or bundled_root.is_symlink()):
            raise PluginArtifactPublicationError(
                f"direct plugin has no canonical bundled skill root: {source_root}"
            )
        source_infos = (
            tuple()
            if self.catalog is not None
            else tuple(
                info
                for entry in sorted(bundled_root.iterdir(), key=lambda item: item.name)
                if (
                    not entry.is_symlink()
                    and entry.is_dir()
                    and not (entry / "SKILL.md").is_symlink()
                    and (entry / "SKILL.md").is_file()
                )
                and (
                    info := _skill_info_from_frontmatter(
                        entry.name,
                        SkillSource.BUNDLED,
                        entry / "SKILL.md",
                        source_ref=SkillSourceRef(
                            origin=SkillSource.BUNDLED,
                            logical_name=entry.name,
                            skill_path=entry / "SKILL.md",
                        ),
                    )
                ).execution_role
                is SkillExecutionRole.SESSION
            )
        )
        namespace_sources = (
            self.namespace_sources
            if self.namespace_sources is not None
            else (self.catalog.namespace_sources if self.catalog is not None else {})
        )
        catalog = self.catalog or EffectiveSkillCatalog(
            skills=tuple(SkillCatalogEntry.from_skill_info(info) for info in source_infos),
            execution_role=SkillExecutionRole.SESSION,
            namespace_sources=namespace_sources,
        )
        if self.catalog is not None and self.namespace_sources is not None:
            catalog = EffectiveSkillCatalog(
                skills=cast(tuple[SkillCatalogEntry, ...], tuple(self.catalog.skills)),
                execution_role=self.catalog.execution_role,
                namespace_sources=self.namespace_sources,
            )
        if not catalog.skills:
            raise PluginArtifactPublicationError(
                f"direct plugin has no bundled skills: {source_root}"
            )
        skill_identity = "\n".join(
            f"{info.name}:{info.canonical_digest}"
            for info in sorted(catalog.skills, key=lambda skill: skill.name)
        )
        namespace_identity = "\n".join(
            f"{name}:{source.value}" for name, source in sorted(catalog.namespace_sources.items())
        )
        semantic_key = ProjectionCacheKey(
            source_root=str(source_root),
            backend_name=backend.name,
            projection_version=self.projection_version,
            default_base_branch=_default_base_branch(self.base_branch),
            skill_identity=skill_identity,
            namespace_identity=namespace_identity,
            asset_digest=public_plugin_asset_digest(source_root),
        ).digest()
        projections_root = Path.home() / ".autoskillit" / "plugin-projections"
        destination = (projections_root / semantic_key).absolute()
        context = _direct_install_projection_context(
            cwd=self.cwd or Path.cwd(),
            project_root=None,
            catalog=catalog,
            backend=backend,
            destination=destination,
            default_base_branch=_default_base_branch(self.base_branch),
            projection_version=self.projection_version,
        )
        return _ProjectedArtifactPlan(
            source_root=source_root,
            destination=destination,
            manifest_path=(
                projections_root / f".{semantic_key}.autoskillit-projection.json"
            ).absolute(),
            lease_path=(projections_root / ".artifact-leases" / f"{semantic_key}.lock").absolute(),
            semantic_key=semantic_key,
            catalog=catalog,
            validation_catalog=source_infos if source_infos else catalog,
            require_sources_within_root=bool(source_infos),
            context=context,
        )

    def acquire_launch_binding(
        self,
        *,
        backend: CodingAgentBackend,
        load_mode: PluginLoadMode,
    ) -> PluginLaunchBinding:
        if not load_mode.consumes_artifact:
            raise ValueError(
                f"projected plugin authority cannot bind load mode {load_mode.value!r}"
            )
        try:
            plan = self._plan(backend)
        except PluginArtifactPublicationError:
            raise
        except Exception as exc:
            raise PluginArtifactPublicationError(
                "projected plugin publication planning failed"
            ) from exc
        try:
            reader = ArtifactLease.acquire_shared(plan.lease_path)
        except Exception as exc:
            raise PluginArtifactPublicationError(
                f"projected plugin reader lease acquisition failed: {plan.semantic_key}"
            ) from exc
        try:
            identity = _try_validate_published_plugin_artifact(plan)
        except BaseException:
            reader.close()
            raise
        if identity is not None:
            return self._binding(load_mode, plan, identity, reader)
        reader.close()

        try:
            writer = ArtifactLease.acquire_exclusive(plan.lease_path, blocking=False)
        except ArtifactLeaseContention as exc:
            raise PluginArtifactContentionError(
                f"projected plugin mutation is contended: {plan.semantic_key}"
            ) from exc
        try:
            with _InstallLock():
                identity = _try_validate_published_plugin_artifact(plan)
                if identity is None:
                    plan.destination.parent.mkdir(parents=True, exist_ok=True)
                    staged: _StagedProjectedArtifact | None = None
                    try:
                        staged = _stage_projected_plugin_artifact(plan)
                        _publish_projected_plugin_root(staged, plan.destination)
                        _publish_projected_plugin_manifest(staged, plan.manifest_path)
                    except (
                        PluginArtifactPublicationError,
                        PluginArtifactValidationError,
                    ):
                        raise
                    except BaseException as exc:
                        raise PluginArtifactPublicationError(
                            f"projected plugin publication failed: {plan.semantic_key}"
                        ) from exc
                    finally:
                        if staged is not None:
                            shutil.rmtree(staged.root, ignore_errors=True)
                            if staged.manifest.is_symlink() or staged.manifest.is_file():
                                staged.manifest.unlink()
                    assert staged is not None
                    identity = _validate_published_plugin_artifact(
                        plan,
                        expected_identity=staged.identity,
                    )
        finally:
            writer.close()

        try:
            reader = ArtifactLease.acquire_shared(plan.lease_path)
        except Exception as exc:
            raise PluginArtifactPublicationError(
                f"projected plugin reader lease acquisition failed: {plan.semantic_key}"
            ) from exc
        try:
            identity = _validate_published_plugin_artifact(
                plan,
                expected_identity=identity,
            )
        except BaseException:
            reader.close()
            raise
        return self._binding(load_mode, plan, identity, reader)

    @staticmethod
    def _binding(
        load_mode: PluginLoadMode,
        plan: _ProjectedArtifactPlan,
        identity: PluginArtifactIdentity,
        reader: ArtifactLease,
    ) -> PluginLaunchBinding:
        owner = ProjectedPluginRetirementOwner(plan.destination.parent)
        owner.cancel_obsolete_retirements(identity)
        from autoskillit.workspace._projection_cache import prune_stale_projections

        prune_stale_projections(
            plan.destination.parent,
            active_key=plan.semantic_key,
        )
        return PluginLaunchBinding(
            load_mode=load_mode,
            plugin_dir=(
                None if load_mode is PluginLoadMode.IMPLICIT_INSTALLED else plan.destination
            ),
            identity=identity,
            inherited_fds=reader.inherited_fds,
            _lease=reader,
        )


def project_direct_install_authority(
    direct_install: DirectInstall,
    *,
    projection_version: int = SKILL_PROJECTION_VERSION,
    base_branch: str | None = None,
    catalog: EffectiveSkillCatalogAuthority | None = None,
    namespace_sources: Mapping[str, SkillSource] | None = None,
    cwd: Path | None = None,
) -> ProjectedPluginArtifactAuthority:
    return ProjectedPluginArtifactAuthority(
        direct_install=direct_install,
        projection_version=projection_version,
        base_branch=base_branch,
        catalog=catalog,
        namespace_sources=namespace_sources,
        cwd=cwd,
    )


def project_default_plugin_authority(
    *,
    projection_version: int = SKILL_PROJECTION_VERSION,
    base_branch: str | None = None,
    catalog: EffectiveSkillCatalogAuthority | None = None,
    namespace_sources: Mapping[str, SkillSource] | None = None,
    cwd: Path | None = None,
) -> ProjectedPluginArtifactAuthority:
    return project_direct_install_authority(
        DirectInstall(plugin_dir=pkg_root()),
        projection_version=projection_version,
        base_branch=base_branch,
        catalog=catalog,
        namespace_sources=namespace_sources,
        cwd=cwd,
    )


def prepare_catalog_skill_dispatch(
    *,
    resolved_command: str,
    cwd: Path,
    catalog: EffectiveSkillCatalogAuthority,
    default_base_branch: str,
    project_root: Path | None = None,
) -> tuple[ProjectedPluginArtifactAuthority, EffectiveSkillDispatchPreparation]:
    """Prepare semantic dispatch state without acquiring or publishing an artifact."""
    default_base_branch = _default_base_branch(default_base_branch)
    authority = project_default_plugin_authority(
        cwd=cwd,
        base_branch=default_base_branch,
        catalog=catalog,
    )
    preparation = EffectiveSkillDispatchPreparation(
        resolved_command=resolved_command,
        cwd=cwd,
        project_root=project_root,
        default_base_branch=default_base_branch,
        catalog=catalog,
    )
    return authority, preparation


def prepare_effective_skill_dispatch(
    *,
    resolved_command: str,
    project_root: Path,
    cwd: Path,
    resolver: SkillResolver,
    visibility: SkillVisibilitySpec | None,
    default_base_branch: str | None,
    recipe_packs: frozenset[str] | None,
    recipe_features: frozenset[str] | None,
) -> tuple[ProjectedPluginArtifactAuthority, EffectiveSkillDispatchPreparation]:
    """Resolve visible orchestrator skills into a backend-neutral preparation."""
    catalog = resolver.list_effective(
        project_root,
        SkillExecutionRole.ORCHESTRATOR,
        visibility=visibility,
        recipe_packs=recipe_packs,
        recipe_features=recipe_features,
    )
    return prepare_catalog_skill_dispatch(
        resolved_command=resolved_command,
        cwd=cwd,
        catalog=catalog,
        default_base_branch=_default_base_branch(default_base_branch),
        project_root=project_root,
    )
