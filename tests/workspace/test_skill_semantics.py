"""Versioned backend-neutral semantic declarations on skill sources."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from autoskillit.core import SkillSemanticOperation, SkillSource
from autoskillit.workspace.skills import _skill_info_from_frontmatter

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.small]


_VALID_SEMANTICS = """semantic_version: 1
semantic_requirements:
  logical_roles:
    - name: reviewer
      purpose: review one independent concern
  child_spawns:
    - role: reviewer
  concurrency:
    required: true
  join:
    required: true
  evidence:
    required: true
    independent: true
  child_model_policies:
    - role: reviewer
      model_class: opus
      reasoning_effort: high
  sibling_skills:
    - name: investigate
  git_metadata_writes:
    - purpose: create the requested commit
"""


def _write_skill(path: Path, *, declarations: str = _VALID_SEMANTICS, body: str = "Body.") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: semantic-test\ndescription: semantic fixture\n{declarations}---\n{body}\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    ("relative_path", "origin"),
    [
        ("bundled/semantic-test/SKILL.md", SkillSource.BUNDLED),
        (".claude/skills/semantic-test/SKILL.md", SkillSource.PROJECT_LOCAL),
        (".autoskillit/skills/semantic-test/SKILL.md", SkillSource.PROJECT_LOCAL),
        (".codex/skills/semantic-test/SKILL.md", SkillSource.PROJECT_LOCAL),
        (".agents/skills/semantic-test/SKILL.md", SkillSource.PROJECT_LOCAL),
    ],
)
def test_all_skill_sources_parse_the_same_semantic_plan(
    tmp_path: Path,
    relative_path: str,
    origin: SkillSource,
) -> None:
    skill_md = tmp_path / relative_path
    _write_skill(skill_md)

    info = _skill_info_from_frontmatter("semantic-test", origin, skill_md)

    assert info.invalid_reason is None
    assert info.semantic_plan is not None
    assert info.semantic_plan.operations == frozenset(SkillSemanticOperation)


@pytest.mark.parametrize(
    ("declarations", "offending", "replacement"),
    [
        (
            "semantic_version: 0\nsemantic_requirements: {}\n",
            "semantic_version: 0",
            "semantic_version: 1",
        ),
        (
            "semantic_version: 1\nsemantic_requirements:\n  agent_model: opus\n",
            "agent_model",
            "child_model_policies",
        ),
        (
            "uses_capabilities: [agent_subagent]\n",
            "agent_subagent",
            "semantic_requirements.child_spawns",
        ),
    ],
)
def test_unknown_or_retired_semantic_declaration_rejects_only_that_skill(
    tmp_path: Path,
    declarations: str,
    offending: str,
    replacement: str,
) -> None:
    bad_path = tmp_path / "bad" / "SKILL.md"
    good_path = tmp_path / "good" / "SKILL.md"
    _write_skill(bad_path, declarations=declarations)
    _write_skill(good_path)

    bad = _skill_info_from_frontmatter("bad", SkillSource.PROJECT_LOCAL, bad_path)
    good = _skill_info_from_frontmatter("good", SkillSource.PROJECT_LOCAL, good_path)

    assert bad.invalid_reason is not None
    assert str(bad_path) in bad.invalid_reason
    assert "schema version" in bad.invalid_reason
    assert offending in bad.invalid_reason
    assert replacement in bad.invalid_reason
    assert good.invalid_reason is None


@pytest.mark.parametrize(
    ("token", "replacement"),
    [
        ("Agent(", "semantic_requirements.child_spawns"),
        ("Task(", "semantic_requirements.child_spawns"),
        ("spawn_agent", "semantic_requirements.child_spawns"),
        ("send_message", "semantic_requirements.join"),
        ("wait_agent", "semantic_requirements.join"),
        ("subagent_type=", "semantic_requirements.logical_roles"),
        ("gpt-5.6-sol", "semantic_requirements.child_model_policies.model_class"),
    ],
)
def test_raw_backend_native_portable_syntax_is_rejected_per_skill(
    tmp_path: Path,
    token: str,
    replacement: str,
) -> None:
    skill_md = tmp_path / "raw" / "SKILL.md"
    _write_skill(skill_md, body=f"Use {token} here.")

    info = _skill_info_from_frontmatter("raw", SkillSource.PROJECT_LOCAL, skill_md)

    assert info.invalid_reason is not None
    assert str(skill_md) in info.invalid_reason
    assert "schema version 1" in info.invalid_reason
    assert repr(token) in info.invalid_reason
    assert replacement in info.invalid_reason


def test_projection_appends_backend_native_semantic_instructions(tmp_path: Path) -> None:
    from autoskillit.core import (
        BackendConventions,
        SkillExecutionRole,
        SkillSemanticAdaptationResult,
    )
    from autoskillit.workspace import (
        EffectiveSkillCatalog,
        SkillCatalogEntry,
        SkillProjectionContext,
        project_agent_skill_document,
    )

    skill_md = tmp_path / "semantic-test" / "SKILL.md"
    _write_skill(skill_md)
    info = _skill_info_from_frontmatter("semantic-test", SkillSource.BUNDLED, skill_md)
    entry = SkillCatalogEntry.from_skill_info(info)
    catalog = EffectiveSkillCatalog(
        skills=(entry,),
        execution_role=SkillExecutionRole.SESSION,
    )
    backend = SimpleNamespace(
        name="test-backend",
        conventions=BackendConventions(skills_subdir=Path("skills")),
        adapt_skill_semantics=lambda _plan: SkillSemanticAdaptationResult(
            instruction_fragments=("Use native-child-call for reviewer.",),
            logical_role_mapping={"reviewer": "reviewer"},
            sibling_skill_targets={"investigate": "native:investigate"},
            model_effort_policy={"reviewer": ("native-model", "high")},
        ),
    )

    document = project_agent_skill_document(
        entry,
        SkillProjectionContext(cwd=tmp_path, catalog=catalog, backend=backend),
    )

    assert "## Backend-adapted semantic execution contract" in document.content
    assert "- Use native-child-call for reviewer." in document.content
    assert document.semantic_digest
    assert document.adaptation_digest
    assert document.semantic_payload["schema_version"] == 1
    assert document.adaptation_payload["instruction_fragments"] == (
        "Use native-child-call for reviewer.",
    )
    assert document.semantic_digest in document.content
    assert document.adaptation_digest in document.content


def test_session_catalog_filters_unsupported_semantics_with_exact_metadata(
    tmp_path: Path,
) -> None:
    from autoskillit.core import (
        SkillExecutionRole,
        SkillSemanticAdaptationResult,
        SkillSemanticOperation,
    )
    from autoskillit.workspace import (
        EffectiveSkillCatalog,
        SkillCatalogEntry,
        compile_session_skill_catalog,
    )

    portable_path = tmp_path / "portable" / "SKILL.md"
    plain_path = tmp_path / "plain" / "SKILL.md"
    _write_skill(portable_path)
    _write_skill(plain_path, declarations="", body="Plain body.")
    portable = _skill_info_from_frontmatter("portable", SkillSource.PROJECT_LOCAL, portable_path)
    plain = _skill_info_from_frontmatter("plain", SkillSource.PROJECT_LOCAL, plain_path)
    catalog = EffectiveSkillCatalog(
        skills=(
            SkillCatalogEntry.from_skill_info(portable),
            SkillCatalogEntry.from_skill_info(plain),
        ),
        execution_role=SkillExecutionRole.SESSION,
    )
    backend = SimpleNamespace(
        name="limited",
        adapt_skill_semantics=lambda _plan: SkillSemanticAdaptationResult.unsupported(
            backend="limited",
            operation=SkillSemanticOperation.CHILD_SPAWN,
        ),
    )

    compilation = compile_session_skill_catalog(catalog, backend)

    assert tuple(skill.name for skill in compilation.catalog.skills) == ("plain",)
    assert compilation.unavailability_payload == {
        "backend": "limited",
        "unavailable": (
            {
                "skill": "portable",
                "backend": "limited",
                "operation": "child_spawn",
                "diagnostic": (
                    "backend 'limited' does not support skill semantic operation 'child_spawn'"
                ),
            },
        ),
    }
