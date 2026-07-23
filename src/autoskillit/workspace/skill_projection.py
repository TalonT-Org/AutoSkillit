"""Projection of internal skill contracts into agent-visible documents."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from autoskillit.core import (
    DirectInstall,
    MarketplaceInstall,
    PluginSource,
    SkillContractError,
    SkillSource,
    SkillSourceRef,
    atomic_write,
    dump_yaml_str,
    read_versioned_json,
    temp_dir_display_str,
    write_versioned_json,
)
from autoskillit.workspace.skill_format import parse_frontmatter_content
from autoskillit.workspace.skills import (
    EffectiveSkillCatalog,
    SkillInfo,
    _skill_info_from_frontmatter,
)

if TYPE_CHECKING:
    from autoskillit.core import CodingAgentBackend

__all__ = [
    "AgentSkillDocument",
    "SkillProjectionContext",
    "materialize_agent_skill_tree",
    "materialize_sanitized_plugin_root",
    "project_agent_skill_document",
    "project_direct_install",
    "project_plugin_source",
    "validate_sanitized_plugin_artifact",
]

_MACHINE_ONLY_KEYS = frozenset({"uses_capabilities", "execution_role", "backend_requirements"})
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


def _skill_sequence(
    skills_or_catalog: EffectiveSkillCatalog | Iterable[SkillInfo],
) -> tuple[SkillInfo, ...]:
    if isinstance(skills_or_catalog, EffectiveSkillCatalog):
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
    skills_or_catalog: EffectiveSkillCatalog | Iterable[SkillInfo],
    context: SkillProjectionContext,
) -> dict[str, AgentSkillDocument]:
    """Replace *destination* with an exact tree of agent-safe skill projections."""
    destination = Path(destination)
    if not destination.name:
        raise SkillContractError("projected skill destination must not be a filesystem root")
    skills = _skill_sequence(skills_or_catalog)
    resolved_destination = destination.resolve()
    for skill in skills:
        if resolved_destination in skill.path.resolve().parents:
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
    skill: SkillInfo,
    document: AgentSkillDocument,
) -> dict[str, Any]:
    role = skill.execution_role
    return {
        "canonical_digest": document.canonical_digest,
        "projected_digest": document.projected_digest,
        "source": document.source_ref.origin.value,
        "source_path": str(document.source_ref.skill_path),
        "uses_capabilities": sorted(skill.uses_capabilities),
        "execution_role": role.value if role is not None else None,
    }


def materialize_sanitized_plugin_root(
    source_root: Path,
    destination: Path,
    catalog: EffectiveSkillCatalog | Iterable[SkillInfo],
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
    skills_or_catalog: EffectiveSkillCatalog | Iterable[SkillInfo],
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
    expected: dict[str, SkillInfo] = {}
    for info in infos:
        if info.name in expected:
            errors.append(f"duplicate expected skill: {info.name}")
        expected[info.name] = info
        try:
            info.path.resolve().relative_to(source_root)
        except ValueError:
            errors.append(f"skill source is outside plugin source root: {info.name}")

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
            leaked = sorted(_MACHINE_ONLY_KEYS & parsed.data.keys())
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
            "source": info.source_ref.origin.value if info.source_ref is not None else None,
            "source_path": (
                str(info.source_ref.skill_path) if info.source_ref is not None else None
            ),
            "uses_capabilities": sorted(info.uses_capabilities),
            "execution_role": (
                info.execution_role.value if info.execution_role is not None else None
            ),
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
) -> DirectInstall:
    """Return a stable, validated public projection for a direct plugin install."""
    source_root = source.plugin_dir.resolve()
    bundled_root = source_root / "skills"
    if not bundled_root.is_dir() or bundled_root.is_symlink():
        raise SkillContractError(
            f"direct plugin has no canonical bundled skill root: {source_root}"
        )
    skill_infos: tuple[SkillInfo, ...] = tuple(
        _skill_info_from_frontmatter(
            entry.name,
            SkillSource.BUNDLED,
            entry / "SKILL.md",
            source_ref=SkillSourceRef(
                origin=SkillSource.BUNDLED,
                logical_name=entry.name,
                skill_path=entry / "SKILL.md",
            ),
        )
        for entry in sorted(bundled_root.iterdir(), key=lambda item: item.name)
        if (
            not entry.is_symlink()
            and entry.is_dir()
            and not (entry / "SKILL.md").is_symlink()
            and (entry / "SKILL.md").is_file()
        )
    )
    if not skill_infos:
        raise SkillContractError(f"direct plugin has no bundled skills: {source_root}")
    identity = "\n".join(
        f"{info.name}:{info.canonical_digest}"
        for info in sorted(skill_infos, key=lambda s: s.name)
    )
    cache_key = hashlib.sha256(f"{source_root}\0{backend.name}\0{identity}".encode()).hexdigest()[
        :24
    ]
    destination = Path.home() / ".autoskillit" / "plugin-projections" / cache_key
    context = SkillProjectionContext(
        execution_cwd=Path(cwd).resolve(),
        backend=backend,
        conventions=backend.conventions,
        substitutions={
            "{{AUTOSKILLIT_TEMP}}": temp_dir_display_str(None),
            "{{AUTOSKILLIT_SCRIPTS}}": str(destination / "recipes" / "scripts"),
            "{{DEFAULT_BASE_BRANCH}}": "main",
        },
    )
    manifest_path = materialize_sanitized_plugin_root(
        source_root,
        destination,
        skill_infos,
        context,
    )
    errors = validate_sanitized_plugin_artifact(
        source_root,
        destination,
        manifest_path,
        skill_infos,
    )
    if errors:
        shutil.rmtree(destination, ignore_errors=True)
        manifest_path.unlink(missing_ok=True)
        raise SkillContractError(
            "direct plugin projection validation failed: " + "; ".join(errors)
        )
    return DirectInstall(plugin_dir=destination)


def project_plugin_source(
    source: PluginSource,
    *,
    cwd: Path,
    backend: CodingAgentBackend,
) -> PluginSource:
    """Project direct installs while preserving marketplace source semantics."""
    if isinstance(source, MarketplaceInstall):
        return source
    return project_direct_install(source, cwd=cwd, backend=backend)
