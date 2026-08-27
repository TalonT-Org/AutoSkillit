from __future__ import annotations

import ast
import hashlib
from pathlib import Path

import pytest

from autoskillit.core import write_versioned_json
from autoskillit.workspace import DefaultSkillResolver
from autoskillit.workspace._projected_artifact.materialization import (
    SANITIZED_PLUGIN_MANIFEST_SCHEMA_VERSION,
    AgentSkillDocument,
    _projection_skills_manifest,
    validate_sanitized_plugin_artifact,
)
from autoskillit.workspace._projection_cache import (
    PROJECTION_ARTIFACT_MANIFEST_SCHEMA_VERSION,
)

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.small]


def _skill_and_document():
    skill = DefaultSkillResolver().resolve("smoke-task")
    assert skill is not None
    content = skill.canonical_content
    digest = hashlib.sha256(content.encode()).hexdigest()
    semantic_digest = skill.semantic_plan.digest if skill.semantic_plan is not None else ""
    return skill, AgentSkillDocument(
        content=content,
        projected_digest=digest,
        canonical_digest=skill.canonical_digest or digest,
        source_identity=skill.source_identity,
        semantic_digest=semantic_digest,
        adaptation_digest="adaptation-digest",
    )


def _public_artifact(tmp_path: Path):
    skill, document = _skill_and_document()
    public_root = tmp_path / "public"
    skill_path = public_root / "skills" / skill.name / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text(document.content, encoding="utf-8")
    manifest_path = tmp_path / "manifest.json"
    manifest = {
        "schema_version": SANITIZED_PLUGIN_MANIFEST_SCHEMA_VERSION,
        "projection_version": 1,
        "skills": _projection_skills_manifest((skill,), {skill.name: document}),
    }
    return skill, public_root, manifest_path, manifest


def test_manifest_per_skill_entries_carry_no_artifact_identity_fields() -> None:
    skill, document = _skill_and_document()

    entry = _projection_skills_manifest((skill,), {skill.name: document})[skill.name]

    assert "artifact_digest" not in entry
    assert "artifact_incarnation" not in entry


@pytest.mark.parametrize("removed_field", ["artifact_digest", "artifact_incarnation"])
def test_manifest_validator_rejects_removed_per_skill_fields(
    tmp_path: Path,
    removed_field: str,
) -> None:
    skill, public_root, manifest_path, manifest = _public_artifact(tmp_path)
    manifest["skills"][skill.name][removed_field] = "legacy-value"
    write_versioned_json(
        manifest_path,
        manifest,
        schema_version=SANITIZED_PLUGIN_MANIFEST_SCHEMA_VERSION,
    )

    errors = validate_sanitized_plugin_artifact(
        skill.path.parents[2],
        public_root,
        manifest_path,
        (skill,),
    )

    assert any(removed_field in error and "unexpected fields" in error for error in errors)
    assert not any("adaptation_digest" in error for error in errors)


def test_sanitized_and_projection_manifest_schema_versions_are_distinct() -> None:
    assert SANITIZED_PLUGIN_MANIFEST_SCHEMA_VERSION == 1
    assert PROJECTION_ARTIFACT_MANIFEST_SCHEMA_VERSION == 2


def test_sanitized_manifest_schema_version_has_single_authority() -> None:
    import autoskillit.workspace._projected_artifact.materialization as materialization

    tree = ast.parse(Path(materialization.__file__).read_text(encoding="utf-8"))
    materialize = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "materialize_sanitized_plugin_root"
    )
    validate = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "validate_sanitized_plugin_artifact"
    )

    schema_values = [
        value
        for node in ast.walk(materialize)
        if isinstance(node, ast.Dict)
        for key, value in zip(node.keys, node.values, strict=True)
        if isinstance(key, ast.Constant) and key.value == "schema_version"
    ]
    schema_keywords = [
        keyword.value
        for node in ast.walk(materialize)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "write_versioned_json"
        for keyword in node.keywords
        if keyword.arg == "schema_version"
    ]
    default_by_name = dict(zip(validate.args.kwonlyargs, validate.args.kw_defaults, strict=True))

    for value in [
        *schema_values,
        *schema_keywords,
        default_by_name[
            next(arg for arg in default_by_name if arg.arg == "manifest_schema_version")
        ],
    ]:
        assert isinstance(value, ast.Name)
        assert value.id == "SANITIZED_PLUGIN_MANIFEST_SCHEMA_VERSION"
