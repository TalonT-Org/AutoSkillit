"""Versioned backend-neutral semantic declarations on skill sources."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from autoskillit.core import SkillSemanticOperation, SkillSource
from autoskillit.core.paths import pkg_root
from autoskillit.workspace.skill_format import read_skill_frontmatter
from autoskillit.workspace.skills import (
    _skill_info_from_frontmatter,
    render_skill_invalidities,
)

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.small]


_VALID_SEMANTICS = """semantic_version: 1
semantic_requirements:
  logical_roles:
    - name: reviewer
      purpose: review one independent concern
  child_spawns:
    - role: reviewer
      count: 1
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

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DECLARED_SKILL_ROOTS = (
    pkg_root() / "skills",
    pkg_root() / "skills_extended",
    _REPO_ROOT / ".autoskillit" / "skills",
    _REPO_ROOT / ".claude" / "skills",
)


def _declared_child_spawn_cases() -> tuple[object, ...]:
    cases: list[object] = []
    for root in _DECLARED_SKILL_ROOTS:
        if not root.is_dir():
            continue
        for skill_path in sorted(root.glob("*/SKILL.md")):
            parsed = read_skill_frontmatter(skill_path)
            requirements = (parsed.data or {}).get("semantic_requirements", {})
            if not isinstance(requirements, dict):
                continue
            spawns = requirements.get("child_spawns", ())
            if not isinstance(spawns, list):
                continue
            relative_path = skill_path.relative_to(_REPO_ROOT)
            for index, spawn in enumerate(spawns):
                role = spawn.get("role", "unknown") if isinstance(spawn, dict) else "unknown"
                cases.append(
                    pytest.param(
                        skill_path,
                        spawn,
                        id=f"{relative_path}::{role}[{index}]",
                    )
                )
    return tuple(cases)


@pytest.mark.parametrize(("skill_path", "spawn"), _declared_child_spawn_cases())
def test_declared_child_spawn_has_exactly_one_cardinality_authority(
    skill_path: Path,
    spawn: object,
) -> None:
    assert isinstance(spawn, dict), f"child spawn in {skill_path} must be a mapping"
    assert sum(authority in spawn for authority in ("count", "for_each")) == 1


_VARIABLE_CARDINALITY_SKILLS = {
    "analyze-prs": (("delegated-worker", "candidate_pr_numbers"),),
    "audit-claims": (("delegated-worker", "claim_analysis_responsibilities"),),
    "audit-impl": (
        ("audit-impl-deviation-evaluator", "deviation_entries"),
        ("audit-impl-slice-auditor", "audit_slices"),
    ),
    "audit-review-decisions": (("delegated-worker", "review_decision_batches"),),
    "build-execution-map": (("delegated-worker", "issue_numbers"),),
    "planner-assess-review-approach": (("delegated-worker", "wp_assessment_batches"),),
    "planner-consolidate-wps": (("delegated-worker", "phase_ids"),),
    "planner-extract-domain": (("delegated-worker", "selected_domain_exploration_task_ids"),),
    "planner-refine-assignments": (("delegated-worker", "assignment_ids"),),
    "planner-refine-phases": (("delegated-worker", "phase_ids"),),
    "planner-refine-wps": (("delegated-worker", "phase_ids"),),
    "planner-validate-task-alignment": (("delegated-worker", "alignment_responsibilities"),),
    "resolve-claims-review": (("delegated-worker", "intent_validation_groups"),),
    "resolve-research-review": (("delegated-worker", "intent_validation_groups"),),
    "resolve-review": (("delegated-worker", "intent_validation_groups"),),
    "review-design": (("delegated-worker", "selected_review_dimensions"),),
    "setup-project": (("delegated-worker", "project_exploration_responsibilities"),),
    "stage-data": (("delegated-worker", "stageable_data_entries"),),
    "triage-issues": (("delegated-worker", "issue_analysis_responsibilities"),),
    "verify-diag": (("delegated-worker", "diagram_paths"),),
}


@pytest.mark.parametrize("skill_name", _VARIABLE_CARDINALITY_SKILLS)
def test_variable_workflow_names_its_runtime_collection(skill_name: str) -> None:
    skill_path = pkg_root() / "skills_extended" / skill_name / "SKILL.md"
    parsed = read_skill_frontmatter(skill_path)
    requirements = (parsed.data or {})["semantic_requirements"]

    actual = tuple((spawn["role"], spawn["for_each"]) for spawn in requirements["child_spawns"])
    expected = _VARIABLE_CARDINALITY_SKILLS[skill_name]
    assert actual == expected
    assert all(collection in parsed.body for _, collection in expected)


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

    assert not info.invalidities
    assert info.semantic_plan is not None
    assert info.semantic_plan.operations == frozenset(SkillSemanticOperation)


def test_for_each_child_spawn_parses_without_a_fixed_count(tmp_path: Path) -> None:
    skill_md = tmp_path / "dynamic" / "SKILL.md"
    declarations = """semantic_version: 1
semantic_requirements:
  logical_roles:
    - name: researcher
      purpose: research one topic
  child_spawns:
    - role: researcher
      for_each: research_topics
"""
    _write_skill(skill_md, declarations=declarations)

    info = _skill_info_from_frontmatter("dynamic", SkillSource.PROJECT_LOCAL, skill_md)

    assert not info.invalidities
    assert info.semantic_plan is not None
    assert info.semantic_plan.child_spawns[0].for_each == "research_topics"


@pytest.mark.parametrize("for_each", ["false", "0", "[topic_a, topic_b]"])
def test_for_each_child_spawn_rejects_non_string_yaml_values(
    tmp_path: Path, for_each: str
) -> None:
    skill_md = tmp_path / "malformed-dynamic" / "SKILL.md"
    declarations = f"""semantic_version: 1
semantic_requirements:
  logical_roles:
    - name: researcher
      purpose: research one topic
  child_spawns:
    - role: researcher
      for_each: {for_each}
"""
    _write_skill(skill_md, declarations=declarations)

    info = _skill_info_from_frontmatter("malformed-dynamic", SkillSource.PROJECT_LOCAL, skill_md)

    assert info.semantic_plan is None
    assert {item.kind.value for item in info.invalidities} == {
        "semantic_child_cardinality_invalid"
    }
    assert "for_each must be a non-empty runtime collection name" in render_skill_invalidities(
        info.invalidities
    )


@pytest.mark.parametrize(
    "cardinality",
    [
        "",
        "      count: null\n",
        "      count: true\n",
        "      count: 1.5\n",
        '      count: "1"\n',
        "      count: 1\n      for_each: research_topics\n",
    ],
)
def test_child_spawn_rejects_invalid_cardinality_authority(
    tmp_path: Path, cardinality: str
) -> None:
    skill_md = tmp_path / "invalid-cardinality" / "SKILL.md"
    declarations = f"""semantic_version: 1
semantic_requirements:
  logical_roles:
    - name: researcher
      purpose: research one topic
  child_spawns:
    - role: researcher
{cardinality}"""
    _write_skill(skill_md, declarations=declarations)

    info = _skill_info_from_frontmatter("invalid-cardinality", SkillSource.PROJECT_LOCAL, skill_md)

    assert info.semantic_plan is None
    assert {item.kind.value for item in info.invalidities} == {
        "semantic_child_cardinality_invalid"
    }
    reason = render_skill_invalidities(info.invalidities)
    assert "semantic_requirements.child_spawns cardinality" in reason
    assert "count: <positive integer> or for_each: <runtime collection>" in reason


def test_child_model_policy_rejects_unregistered_logical_class(tmp_path: Path) -> None:
    skill_md = tmp_path / "unknown-model-class" / "SKILL.md"
    declarations = """semantic_version: 1
semantic_requirements:
  logical_roles:
    - name: reviewer
      purpose: review one independent concern
  child_model_policies:
    - role: reviewer
      model_class: vendor-native-model
"""
    _write_skill(skill_md, declarations=declarations)

    info = _skill_info_from_frontmatter(
        "unknown-model-class",
        SkillSource.PROJECT_LOCAL,
        skill_md,
    )

    assert info.semantic_plan is None
    assert info.invalidities
    reason = render_skill_invalidities(info.invalidities)
    assert "unknown semantic model class 'vendor-native-model'" in reason
    assert "['haiku', 'opus', 'sonnet']" in reason


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

    assert bad.invalidities
    reason = render_skill_invalidities(bad.invalidities)
    assert str(bad_path) in reason
    assert "schema version" in reason
    assert offending in reason
    assert replacement in reason
    assert not good.invalidities


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

    assert info.invalidities
    reason = render_skill_invalidities(info.invalidities)
    assert str(skill_md) in reason
    assert "schema version 1" in reason
    assert repr(token) in reason
    assert replacement in reason


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
