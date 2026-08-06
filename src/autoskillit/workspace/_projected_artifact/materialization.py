"""Shared projected-plugin construction and validation primitives."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
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
    ResolvedSkillAuthority,
    SkillAuthority,
    SkillContractError,
    SkillSource,
    SkillSourceIdentity,
    atomic_write,
    destination_location,
    dump_yaml_str,
    read_versioned_json,
    temp_dir_display_str,
    write_versioned_json,
)
from autoskillit.workspace._projection_cache import is_projected_asset
from autoskillit.workspace.skill_format import parse_frontmatter_content
from autoskillit.workspace.skills import SkillInfo, render_skill_invalidities

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
    projection_version: int = SKILL_PROJECTION_VERSION

    def __post_init__(self) -> None:
        if type(self.projection_version) is not int or self.projection_version < 1:
            raise SkillContractError("projection version must be a positive integer")
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
