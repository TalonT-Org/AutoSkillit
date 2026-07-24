"""Projection of internal skill contracts into agent-visible documents."""

from __future__ import annotations

import fcntl
import hashlib
import os
import shutil
import tempfile
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, assert_never

import regex as re

from autoskillit.core import (
    MACHINE_ONLY_SKILL_FRONTMATTER_KEYS,
    SKILL_PROJECTION_VERSION,
    BackendConventions,
    CodingAgentBackend,
    DirectInstall,
    MarketplaceInstall,
    PluginSource,
    SkillContractError,
    SkillExecutionRole,
    SkillResolver,
    SkillSource,
    SkillSourceIdentity,
    SkillSourceRef,
    atomic_write,
    dump_yaml_str,
    pkg_root,
    read_versioned_json,
    temp_dir_display_str,
    write_versioned_json,
)
from autoskillit.workspace.skill_format import parse_frontmatter_content
from autoskillit.workspace.skills import (
    EffectiveSkillCatalog,
    EffectiveSkillInvocation,
    SkillCatalogEntry,
    SkillInfo,
    _skill_info_from_frontmatter,
)

__all__ = [
    "AgentSkillDocument",
    "EffectiveSkillDispatchContract",
    "SkillProjectionContext",
    "build_effective_skill_dispatch_contract",
    "materialize_agent_skill_tree",
    "materialize_sanitized_plugin_root",
    "prepare_catalog_skill_dispatch",
    "prepare_effective_skill_dispatch",
    "project_agent_skill_document",
    "project_default_plugin_source",
    "project_direct_install",
    "project_plugin_source",
    "validate_sanitized_plugin_artifact",
]

_SKILL_NAMESPACE_REF_RE = re.compile(r"/autoskillit:([a-z][a-z0-9-]*)")
_CANONICAL_SKILL_DIRS = frozenset({"skills", "skills_extended"})
_PUBLIC_PLUGIN_ASSET_NAMES = frozenset(
    {
        ".claude-plugin",
        ".mcp.json",
        "agents",
        "assets",
        "commands",
        "hooks",
        "recipes",
        "scripts",
        "settings.json",
    }
)


SkillContractRecord = SkillInfo | SkillCatalogEntry


def _source_identity(skill: SkillContractRecord) -> SkillSourceIdentity:
    if isinstance(skill, SkillCatalogEntry):
        return skill.source_identity
    if skill.source_ref is None:
        raise SkillContractError(f"skill {skill.name!r} has no effective source identity")
    return skill.source_ref.identity


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
    catalog: EffectiveSkillCatalog | None = None
    invocation: EffectiveSkillInvocation | None = None
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


@dataclass(frozen=True, slots=True)
class EffectiveSkillDispatchContract:
    """Immutable execution-bound contract carried through headless dispatch."""

    resolved_command: str
    projection_context: SkillProjectionContext
    invocation: EffectiveSkillInvocation | None
    catalog: EffectiveSkillCatalog | None
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
        return f"/{_agent_skill_namespace(source)}{match.group(1)}"

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
    skills_or_catalog: EffectiveSkillCatalog | Iterable[SkillContractRecord],
) -> tuple[SkillContractRecord, ...]:
    if isinstance(skills_or_catalog, EffectiveSkillCatalog):
        return skills_or_catalog.skills
    return tuple(skills_or_catalog)


def _replace_directory(staging: Path, destination: Path) -> None:
    if destination.is_symlink() or (destination.exists() and not destination.is_dir()):
        destination.unlink()
    elif destination.exists():
        shutil.rmtree(destination)
    os.replace(staging, destination)


@contextmanager
def _projection_publication_lock(destination: Path) -> Iterator[None]:
    """Serialize first publication for one content-addressed projection."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    lock_path = destination.parent / f".{destination.name}.lock"
    with lock_path.open("a", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def materialize_agent_skill_tree(
    destination: Path,
    skills_or_catalog: EffectiveSkillCatalog | Iterable[SkillContractRecord],
    context: SkillProjectionContext,
) -> dict[str, AgentSkillDocument]:
    """Replace *destination* with an exact tree of agent-safe skill projections."""
    destination = Path(destination)
    if not destination.name:
        raise SkillContractError("projected skill destination must not be a filesystem root")
    skills = _skill_sequence(skills_or_catalog)
    resolved_destination = destination.resolve()
    for skill in skills:
        if isinstance(skill, SkillInfo) and resolved_destination in skill.path.resolve().parents:
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
        if entry.name in _CANONICAL_SKILL_DIRS:
            continue
        if top_level and entry.name not in _PUBLIC_PLUGIN_ASSET_NAMES:
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


def materialize_sanitized_plugin_root(
    source_root: Path,
    destination: Path,
    catalog: EffectiveSkillCatalog | Iterable[SkillContractRecord],
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
    resolved_destination = destination.resolve()
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
    skill_by_name = {skill.name: skill for skill in skill_infos}
    manifest = {
        "schema_version": 1,
        "projection_version": context.projection_version,
        "skills": {
            name: _manifest_skill_entry(skill_by_name[name], document)
            for name, document in documents.items()
        },
    }
    write_versioned_json(manifest_path, manifest, schema_version=1)
    return manifest_path


def validate_sanitized_plugin_artifact(
    source_root: Path,
    public_root: Path,
    manifest_path: Path,
    skills_or_catalog: EffectiveSkillCatalog | Iterable[SkillContractRecord],
    *,
    require_sources_within_root: bool = True,
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

    manifest = read_versioned_json(manifest_path, 1)
    if manifest is None:
        return tuple([*errors, "projection manifest is unreadable or has an unsupported schema"])
    if manifest.get("schema_version") != 1:
        errors.append("projection manifest schema_version must be 1")
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
            "search_dir": (
                info.source_ref.search_dir
                if isinstance(info, SkillInfo) and info.source_ref is not None
                else info.source_identity.search_dir
                if isinstance(info, SkillCatalogEntry)
                else None
            ),
            "precedence": (
                info.source_ref.precedence
                if isinstance(info, SkillInfo) and info.source_ref is not None
                else info.source_identity.precedence
                if isinstance(info, SkillCatalogEntry)
                else None
            ),
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


def project_direct_install(
    source: DirectInstall,
    *,
    cwd: Path,
    backend: CodingAgentBackend,
    skill_catalog: EffectiveSkillCatalog | None = None,
) -> DirectInstall:
    """Return a stable, validated public projection for a direct plugin install."""
    source_root = source.plugin_dir.resolve()
    bundled_root = source_root / "skills"
    if not source_root.is_dir():
        raise SkillContractError(f"direct plugin root does not exist: {source_root}")
    if skill_catalog is None and (not bundled_root.is_dir() or bundled_root.is_symlink()):
        raise SkillContractError(
            f"direct plugin has no canonical bundled skill root: {source_root}"
        )
    source_infos = (
        tuple()
        if skill_catalog is not None
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
    catalog = skill_catalog or EffectiveSkillCatalog(
        skills=tuple(SkillCatalogEntry.from_skill_info(info) for info in source_infos),
        execution_role=SkillExecutionRole.SESSION,
    )
    if not catalog.skills:
        raise SkillContractError(f"direct plugin has no bundled skills: {source_root}")
    identity = "\n".join(
        f"{info.name}:{info.canonical_digest}"
        for info in sorted(catalog.skills, key=lambda s: s.name)
    )
    cache_key = hashlib.sha256(f"{source_root}\0{backend.name}\0{identity}".encode()).hexdigest()[
        :24
    ]
    destination = Path.home() / ".autoskillit" / "plugin-projections" / cache_key
    context = SkillProjectionContext(
        cwd=Path(cwd).resolve(),
        catalog=catalog,
        backend=backend,
        conventions=backend.conventions,
        substitutions={
            "{{AUTOSKILLIT_TEMP}}": temp_dir_display_str(None),
            "{{AUTOSKILLIT_SCRIPTS}}": str(destination / "recipes" / "scripts"),
            "{{DEFAULT_BASE_BRANCH}}": "main",
        },
    )
    manifest_path = destination.parent / f".{destination.name}.autoskillit-projection.json"
    with _projection_publication_lock(destination):
        if destination.is_symlink() or (destination.exists() and not destination.is_dir()):
            raise SkillContractError(
                f"direct plugin projection cache is not a directory: {destination}"
            )
        if not destination.exists():
            manifest_path = materialize_sanitized_plugin_root(
                source_root,
                destination,
                catalog,
                context,
            )
        errors = validate_sanitized_plugin_artifact(
            source_root,
            destination,
            manifest_path,
            source_infos if source_infos else catalog,
            require_sources_within_root=bool(source_infos),
        )
        if errors:
            raise SkillContractError(
                "direct plugin projection validation failed: " + "; ".join(errors)
            )
    return DirectInstall(plugin_dir=destination)


def project_plugin_source(
    source: PluginSource,
    *,
    cwd: Path,
    backend: CodingAgentBackend,
    skill_catalog: EffectiveSkillCatalog | None = None,
) -> PluginSource:
    """Project every plugin source into one sanitized direct-install tree."""
    if isinstance(source, MarketplaceInstall):
        source = DirectInstall(plugin_dir=source.cache_path)
    return project_direct_install(
        source,
        cwd=cwd,
        backend=backend,
        skill_catalog=skill_catalog,
    )


def project_default_plugin_source(
    *,
    cwd: Path,
    backend: CodingAgentBackend,
    skill_catalog: EffectiveSkillCatalog | None = None,
) -> DirectInstall:
    """Project the installed package without exposing its canonical root."""
    return project_direct_install(
        DirectInstall(plugin_dir=pkg_root()),
        cwd=cwd,
        backend=backend,
        skill_catalog=skill_catalog,
    )


def prepare_catalog_skill_dispatch(
    *,
    resolved_command: str,
    cwd: Path,
    backend: CodingAgentBackend,
    catalog: EffectiveSkillCatalog,
    project_root: Path | None = None,
) -> tuple[DirectInstall, EffectiveSkillDispatchContract]:
    """Project a visible catalog and bind the artifact to executor authority."""
    plugin_source = project_default_plugin_source(
        cwd=cwd,
        backend=backend,
        skill_catalog=catalog,
    )
    contract = build_effective_skill_dispatch_contract(
        resolved_command,
        SkillProjectionContext(
            cwd=cwd,
            project_root=project_root,
            catalog=catalog,
            backend=backend,
            conventions=backend.conventions,
            gating=False,
        ),
        artifact_paths=(str(plugin_source.plugin_dir),),
    )
    return plugin_source, contract


def prepare_effective_skill_dispatch(
    *,
    resolved_command: str,
    project_root: Path,
    cwd: Path,
    backend: CodingAgentBackend,
    resolver: SkillResolver,
    config: Any,
    recipe_packs: frozenset[str] | None,
    recipe_features: frozenset[str] | None,
) -> tuple[DirectInstall, EffectiveSkillDispatchContract]:
    """Resolve visible orchestrator skills, project them, and bind dispatch authority."""
    catalog = resolver.list_effective(
        project_root,
        SkillExecutionRole.ORCHESTRATOR,
        config=config,
        recipe_packs=recipe_packs,
        recipe_features=recipe_features,
    )
    return prepare_catalog_skill_dispatch(
        resolved_command=resolved_command,
        cwd=cwd,
        backend=backend,
        catalog=catalog,
        project_root=project_root,
    )
