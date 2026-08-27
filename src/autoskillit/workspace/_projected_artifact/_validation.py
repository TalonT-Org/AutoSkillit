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
    EffectiveSkillCatalogAuthority,
    TreeVanishedError,
    read_versioned_json,
    strict_walk,
)
from autoskillit.workspace._projected_artifact._documents import SkillContractRecord
from autoskillit.workspace._projected_artifact._publication import (
    SANITIZED_PLUGIN_MANIFEST_SCHEMA_VERSION,
    _skill_sequence,
)
from autoskillit.workspace.skill_format import parse_frontmatter_content
from autoskillit.workspace.skills import SkillInfo


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
        try:
            for tree_entry in strict_walk(public_root):
                if tree_entry.kind == "l":
                    errors.append(
                        "public plugin asset is a symlink: "
                        f"{public_root / tree_entry.relative_path}"
                    )
        except TreeVanishedError as exc:
            errors.append(f"public plugin tree enumeration raced with a mutation: {exc}")
        except OSError as exc:
            errors.append(f"public plugin tree cannot be read during validation: {exc}")
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
        expected_entry: dict[str, object] = {
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
        expected_entry["semantic_digest"] = (
            semantic_plan.digest if semantic_plan is not None else ""
        )
        # adaptation_digest is validated downstream by re-parsing the projected artifact.
        allowed_fields = {*expected_entry, "adaptation_digest"}
        unexpected_fields = sorted(set(entry) - allowed_fields)
        if unexpected_fields:
            errors.append(
                f"manifest entry has unexpected fields for {name}: {unexpected_fields!r}"
            )
        for field_name, value in expected_entry.items():
            if entry.get(field_name) != value:
                errors.append(
                    f"manifest {field_name} mismatch for {name}: "
                    f"expected {value!r}, got {entry.get(field_name)!r}"
                )
    return tuple(errors)


__all__ = ["validate_sanitized_plugin_artifact"]
