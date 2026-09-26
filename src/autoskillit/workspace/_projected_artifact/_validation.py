"""Projected-artifact validation — exact-incarnation validator.

Single owner of ``validate_sanitized_plugin_artifact`` and its
validation-only helpers.

The validator reconstructs the expected manifest independently of the
producer in ``_publication.py`` rather than reusing the producer's builder —
sharing one builder would mask producer bugs the validator exists to catch.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from pathlib import Path

from autoskillit.core import (
    MACHINE_ONLY_SKILL_FRONTMATTER_KEYS,
    VANISHED_ERRORS,
    AgentDefinitionError,
    EffectiveSkillCatalogAuthority,
    TreeVanishedError,
    YAMLError,
    load_agent_definition,
    read_claude_plugin_tool_prefix,
    read_versioned_json,
    scan_observed,
    strict_walk,
    validate_agent_tool_short_name,
)
from autoskillit.workspace._projected_artifact._documents import SkillContractRecord
from autoskillit.workspace._projected_artifact._publication import (
    SANITIZED_PLUGIN_MANIFEST_SCHEMA_VERSION,
    _skill_sequence,
)
from autoskillit.workspace.skills import SkillInfo
from autoskillit.workspace.skills._format import parse_frontmatter_content


def _collect_expected_contracts(
    source_root: Path,
    infos: tuple[SkillContractRecord, ...],
    *,
    require_sources_within_root: bool,
    errors: list[str],
) -> dict[str, SkillContractRecord]:
    """Collect expected records while reporting duplicate and source-boundary findings."""
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
    return expected


def _read_manifest_skills(
    manifest_path: Path,
    manifest_schema_version: int,
    errors: list[str],
) -> dict[str, object] | None:
    """Admit the manifest header and return its skills mapping when well-shaped."""
    manifest = read_versioned_json(manifest_path, manifest_schema_version)
    if manifest is None:
        errors.append("projection manifest is unreadable or has an unsupported schema")
        return None
    if manifest.get("schema_version") != manifest_schema_version:
        errors.append(f"projection manifest schema_version must be {manifest_schema_version}")
    projection_version = manifest.get("projection_version")
    if type(projection_version) is not int or projection_version < 1:
        errors.append("projection manifest projection_version must be a positive integer")
    manifest_skills = manifest.get("skills")
    if not isinstance(manifest_skills, dict):
        errors.append("projection manifest skills must be a JSON object")
        return None
    return manifest_skills


def _observe_public_tree(
    public_root: Path,
    errors: list[str],
) -> tuple[list[tuple[str, str]], bool]:
    """Observe the public tree exactly once for symlinks and skill-tree entries."""
    public_skill_tree_entries: list[tuple[str, str]] = []
    if not public_root.is_dir():
        return public_skill_tree_entries, False
    try:
        for tree_entry in strict_walk(public_root):
            if tree_entry.kind == "l":
                errors.append(
                    f"public plugin asset is a symlink: {public_root / tree_entry.relative_path}"
                )
            if tree_entry.relative_path.startswith("skills/"):
                public_skill_tree_entries.append(
                    (tree_entry.relative_path.removeprefix("skills/"), tree_entry.kind)
                )
    except TreeVanishedError as exc:
        errors.append(f"public plugin tree enumeration raced with a mutation: {exc}")
        return public_skill_tree_entries, False
    except OSError as exc:
        errors.append(f"public plugin tree cannot be read during validation: {exc}")
        return public_skill_tree_entries, False
    return public_skill_tree_entries, True


def _derive_public_skill_names(
    public_skills: Path,
    public_skill_tree_entries: list[tuple[str, str]],
    public_tree_complete: bool,
    errors: list[str],
) -> set[str]:
    """Derive public skill directories solely from the strict-walk observation."""
    actual_names: set[str] = set()
    if not public_skills.is_dir() or public_skills.is_symlink():
        errors.append("public plugin skills root is missing or is a symlink")
        return actual_names
    if not public_tree_complete:
        return actual_names

    children_by_skill: dict[str, set[str]] = {}
    regular_skill_markdown: set[str] = set()
    for relative_path, kind in public_skill_tree_entries:
        entry_name, separator, child_path = relative_path.partition("/")
        if not separator:
            if kind == "l":
                errors.append(f"public skill entry is a symlink: {entry_name}")
            elif kind != "d":
                errors.append(f"public skills root contains a non-directory entry: {entry_name}")
            else:
                actual_names.add(entry_name)
            continue
        child_name = child_path.split("/", maxsplit=1)[0]
        children_by_skill.setdefault(entry_name, set()).add(child_name)
        if child_path == "SKILL.md" and kind == "f":
            regular_skill_markdown.add(entry_name)
    for entry_name in actual_names:
        if (
            children_by_skill.get(entry_name) != {"SKILL.md"}
            or entry_name not in regular_skill_markdown
        ):
            errors.append(f"public skill directory must contain only SKILL.md: {entry_name}")
    return actual_names


def _reconcile_public_inventories(
    actual_names: set[str],
    expected_names: set[str],
    manifest_skills: dict[str, object],
    errors: list[str],
) -> set[str]:
    """Report public and manifest inventory drift before document validation."""
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
    return manifest_names


def _validate_public_skill_document(
    skill_md: Path,
    name: str,
    errors: list[str],
) -> str | None:
    """Return readable document content after recording its public-only violations."""
    if skill_md.is_symlink():
        errors.append(f"public SKILL.md is a symlink: {name}")
        return None
    try:
        content = skill_md.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        errors.append(f"public SKILL.md is unreadable for {name}: {exc}")
        return None
    parsed = parse_frontmatter_content(content)
    if not parsed.is_valid or parsed.data is None:
        errors.append(f"public SKILL.md frontmatter is invalid for {name}: {parsed.error}")
    else:
        leaked = sorted(MACHINE_ONLY_SKILL_FRONTMATTER_KEYS & parsed.data.keys())
        if leaked:
            errors.append(f"public SKILL.md exposes machine fields for {name}: {leaked!r}")
    return content


def _expected_manifest_entry(info: SkillContractRecord, content: str) -> dict[str, object]:
    """Reconstruct one expected manifest entry without using the producer builder."""
    canonical_digest = (
        info.canonical_digest or hashlib.sha256(info.canonical_content.encode()).hexdigest()
    )
    expected_entry: dict[str, object] = {
        "projected_digest": hashlib.sha256(content.encode()).hexdigest(),
        "canonical_digest": canonical_digest,
        "source": info.source.value,
        "logical_name": info.name,
        "search_dir": info.source_identity.search_dir,
        "precedence": info.source_identity.precedence,
        "uses_capabilities": sorted(info.uses_capabilities),
        "execution_role": (info.execution_role.value if info.execution_role is not None else None),
        "activate_deps": list(info.activate_deps),
        "write_paths": list(info.write_paths) if info.write_paths is not None else None,
    }
    semantic_plan = info.semantic_plan
    expected_entry["join_required"] = bool(
        semantic_plan is not None
        and semantic_plan.join is not None
        and semantic_plan.join.required
    )
    cardinality: dict[str, int | str] = {}
    if semantic_plan is not None:
        for spawn in semantic_plan.child_spawns:
            if spawn.count is not None:
                cardinality[spawn.role] = int(spawn.count)
            elif spawn.for_each is not None:
                cardinality[spawn.role] = str(spawn.for_each)
    expected_entry["child_spawn_cardinality"] = dict(sorted(cardinality.items()))
    expected_entry["semantic_digest"] = semantic_plan.digest if semantic_plan is not None else ""
    return expected_entry


def _validate_manifest_entry(
    entry: object,
    info: SkillContractRecord,
    content: str,
    errors: list[str],
) -> None:
    """Compare one manifest entry against its independently reconstructed contract."""
    name = info.name
    if not isinstance(entry, dict):
        errors.append(f"manifest entry must be a JSON object for {name}")
        return
    expected_entry = _expected_manifest_entry(info, content)
    # adaptation_digest is validated downstream by re-parsing the projected artifact.
    allowed_fields = {*expected_entry, "adaptation_digest"}
    unexpected_fields = sorted(set(entry) - allowed_fields)
    if unexpected_fields:
        errors.append(f"manifest entry has unexpected fields for {name}: {unexpected_fields!r}")
    for field_name, value in expected_entry.items():
        if entry.get(field_name) != value:
            errors.append(
                f"manifest {field_name} mismatch for {name}: "
                f"expected {value!r}, got {entry.get(field_name)!r}"
            )


def _rendered_agent_mcp_tools(agents_dir: Path, errors: list[str]) -> list[tuple[str, str]]:
    """Return ``(file name, tool)`` for every MCP tool the rendered agents declare.

    A vanished ``agents/`` directory declares nothing; every unreadable or
    invalid definition is recorded in *errors*.
    """
    if not agents_dir.is_dir():
        return []
    try:
        entries = sorted(scan_observed(agents_dir), key=lambda entry: entry.name)
    except VANISHED_ERRORS:
        return []
    except OSError as exc:
        errors.append(f"rendered agents cannot be listed during validation: {exc}")
        return []
    mcp_tools: list[tuple[str, str]] = []
    for entry in entries:
        if entry.is_dir or entry.path.suffix != ".md" or entry.name in {"AGENTS.md", "CLAUDE.md"}:
            continue
        try:
            definition = load_agent_definition(entry.path)
        except (AgentDefinitionError, OSError, UnicodeDecodeError, YAMLError) as exc:
            errors.append(f"rendered agent {entry.name} cannot be loaded: {exc}")
            continue
        mcp_tools.extend(
            (entry.name, tool) for tool in definition.tools if tool.startswith("mcp__")
        )
    return mcp_tools


def validate_rendered_agent_tool_namespace(public_root: Path) -> tuple[str, ...]:
    """Return errors for rendered agent MCP tools that lack the plugin's own namespace.

    The expected namespace is re-derived from the artifact's own
    ``.claude-plugin/plugin.json`` and ``.mcp.json``. Only ``tools`` is checked:
    ``reader_tools`` is the Codex-only allowlist and stays DIRECT-canonical.
    Never raises; every failure becomes an error string.
    """
    errors: list[str] = []
    mcp_tools = _rendered_agent_mcp_tools(Path(public_root) / "agents", errors)
    if not mcp_tools:
        return tuple(errors)
    try:
        expected = read_claude_plugin_tool_prefix(Path(public_root))
    except ValueError as exc:
        errors.append(
            f"rendered agents declare MCP tools but the plugin namespace is underivable: {exc}"
        )
        return tuple(errors)
    for agent_name, tool in mcp_tools:
        if not tool.startswith(expected):
            errors.append(
                f"rendered agent {agent_name} tool {tool!r} does not carry the plugin "
                f"namespace {expected!r}"
            )
            continue
        try:
            validate_agent_tool_short_name(tool[len(expected) :])
        except ValueError as exc:
            errors.append(f"rendered agent {agent_name} tool {tool!r} is not admissible: {exc}")
    return tuple(errors)


def validate_sanitized_plugin_artifact(
    source_root: Path,
    public_root: Path,
    manifest_path: Path,
    skills_or_catalog: EffectiveSkillCatalogAuthority | Iterable[SkillContractRecord],
    *,
    require_sources_within_root: bool = True,
    manifest_schema_version: int = SANITIZED_PLUGIN_MANIFEST_SCHEMA_VERSION,
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
    errors.extend(validate_rendered_agent_tool_namespace(public_root))
    infos = _skill_sequence(skills_or_catalog)
    expected = _collect_expected_contracts(
        source_root,
        infos,
        require_sources_within_root=require_sources_within_root,
        errors=errors,
    )
    manifest_skills = _read_manifest_skills(manifest_path, manifest_schema_version, errors)
    if manifest_skills is None:
        return tuple(errors)

    public_skills = public_root / "skills"
    if (public_root / "skills_extended").exists():
        errors.append("public plugin must not contain a canonical skills_extended tree")
    public_skill_tree_entries, public_tree_complete = _observe_public_tree(public_root, errors)
    actual_names = _derive_public_skill_names(
        public_skills,
        public_skill_tree_entries,
        public_tree_complete,
        errors,
    )

    expected_names = set(expected)
    manifest_names = _reconcile_public_inventories(
        actual_names,
        expected_names,
        manifest_skills,
        errors,
    )

    for name in sorted(expected_names & actual_names & manifest_names):
        info = expected[name]
        skill_md = public_skills / name / "SKILL.md"
        content = _validate_public_skill_document(skill_md, name, errors)
        if content is not None:
            _validate_manifest_entry(manifest_skills[name], info, content, errors)
    return tuple(errors)


__all__ = ["validate_sanitized_plugin_artifact"]
