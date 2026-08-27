"""Projected-artifact publication — sanitized-plugin staging and tree materialization.

Single owner of:

- ``SANITIZED_PLUGIN_MANIFEST_SCHEMA_VERSION``
- ``_skill_sequence``, ``_manifest_skill_entry``, ``_projection_skills_manifest``
- ``_replace_directory``
- ``materialize_agent_skill_tree``
- ``_copy_non_skill_plugin_assets``
- ``write_generated_hooks_json`` (registered durable writer)
- ``_render_agent_definitions``
- ``materialize_sanitized_plugin_root``

Per-skill manifest entries are free of the retired ``artifact_digest`` and
``artifact_incarnation`` fields fixed by issue #4847. Rendered agent
definitions are derived from the admitted catalog as fixed by issue #4715.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from autoskillit.core import (
    EffectiveSkillCatalogAuthority,
    ResolvedSkillAuthority,
    SkillContractError,
    SkillSemanticAdaptationResult,
    atomic_write,
    destination_location,
    load_agent_definition,
    project_agent_tool_name,
    validate_agent_tool_canonical,
    write_versioned_json,
)
from autoskillit.hook_registry import render_hooks_json_text
from autoskillit.workspace._projected_artifact._documents import (
    AgentSkillDocument,
    SkillContractRecord,
    SkillProjectionContext,
    project_agent_skill_document,
)
from autoskillit.workspace._projection_cache import is_projected_asset
from autoskillit.workspace._shared_asset_store import (
    link_or_copy_asset,
    resolve_shared_asset_store_root,
)

SANITIZED_PLUGIN_MANIFEST_SCHEMA_VERSION = 1


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
    semantic_adaptations: Mapping[str, SkillSemanticAdaptationResult] | None = None,
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
            document = project_agent_skill_document(
                skill,
                context,
                (semantic_adaptations or {}).get(skill.name),
            )
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
    _store_root: Path | None = None,
) -> None:
    """Copy the verbatim (non-skill) plugin asset tree, sharing bytes via a hardlink
    store where possible (S3-1).

    `_store_root` is resolved automatically on the top-level call (from
    `destination.parent`, which is `projections_root` at that point -- see
    `_stage_projected_plugin_artifact`) and threaded through recursive calls
    unchanged; callers never pass it explicitly.
    """
    if top_level:
        _store_root = resolve_shared_asset_store_root(destination.parent)
    for entry in source_root.iterdir():
        if not is_projected_asset(entry, top_level=top_level):
            continue
        if entry.is_symlink():
            raise SkillContractError(f"plugin asset must not be a symlink: {entry}")
        target = destination / entry.name
        if entry.is_dir():
            target.mkdir()
            _copy_non_skill_plugin_assets(entry, target, top_level=False, _store_root=_store_root)
        elif entry.is_file():
            link_or_copy_asset(entry, target, store_root=_store_root)
        else:
            raise SkillContractError(f"plugin asset must be a regular file or directory: {entry}")


def write_generated_hooks_json(plugin_root: Path) -> None:
    """Write a freshly rendered ``hooks/hooks.json`` into *plugin_root*.

    Only writes when the hooks directory already exists (meaning hook scripts
    were copied from the source root).  This is the single named operation
    for "publish current hook manifest into a plugin root" — used by
    projection staging, marketplace publication, and self-heal republish.
    """
    hooks_dir = plugin_root / "hooks"
    if not hooks_dir.is_dir():
        return
    atomic_write(hooks_dir / "hooks.json", render_hooks_json_text())


def _manifest_skill_entry(
    skill: SkillContractRecord,
    document: AgentSkillDocument,
) -> dict[str, Any]:
    role = skill.execution_role
    semantic_plan = skill.semantic_plan
    join_required = bool(
        semantic_plan is not None
        and semantic_plan.join is not None
        and semantic_plan.join.required
    )
    child_cardinality: dict[str, int | str] = {}
    if semantic_plan is not None:
        for spawn in semantic_plan.child_spawns:
            if spawn.count is not None:
                child_cardinality[spawn.role] = int(spawn.count)
            elif spawn.for_each is not None:
                child_cardinality[spawn.role] = str(spawn.for_each)
    entry: dict[str, Any] = {
        "canonical_digest": document.canonical_digest,
        "projected_digest": document.projected_digest,
        "source": document.source_identity.origin.value,
        "logical_name": document.source_identity.logical_name,
        "search_dir": document.source_identity.search_dir,
        "precedence": document.source_identity.precedence,
        "uses_capabilities": sorted(skill.uses_capabilities),
        "execution_role": role.value if role is not None else None,
        "activate_deps": list(skill.activate_deps),
        "join_required": join_required,
        "child_spawn_cardinality": dict(sorted(child_cardinality.items())),
        "semantic_digest": document.semantic_digest,
        "adaptation_digest": document.adaptation_digest,
    }
    return entry


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
        content = path.read_bytes().decode("utf-8")
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
        original_tools_line = lines[tools_line_idx]
        indent = original_tools_line[
            : len(original_tools_line) - len(original_tools_line.lstrip())
        ]
        line_ending = (
            "\r\n"
            if original_tools_line.endswith("\r\n")
            else "\n"
            if original_tools_line.endswith("\n")
            else ""
        )
        lines[tools_line_idx] = f"{indent}tools: {new_tools_value}{line_ending}"
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
        if projected_tools != rendered_def.tools:
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
        "schema_version": SANITIZED_PLUGIN_MANIFEST_SCHEMA_VERSION,
        "projection_version": context.projection_version,
        "skills": _projection_skills_manifest(skill_infos, documents),
    }
    write_versioned_json(
        manifest_path,
        manifest,
        schema_version=SANITIZED_PLUGIN_MANIFEST_SCHEMA_VERSION,
    )
    return manifest_path


__all__ = [
    "SANITIZED_PLUGIN_MANIFEST_SCHEMA_VERSION",
    "_copy_non_skill_plugin_assets",
    "_manifest_skill_entry",
    "_projection_skills_manifest",
    "_replace_directory",
    "_render_agent_definitions",
    "_skill_sequence",
    "materialize_agent_skill_tree",
    "materialize_sanitized_plugin_root",
    "write_generated_hooks_json",
]
