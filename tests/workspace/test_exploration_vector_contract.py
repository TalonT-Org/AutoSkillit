"""Backend-neutral exploration-vector authoring and marker contracts."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from autoskillit.core import (
    ExplorationVectorApplicabilityId,
    ExplorationVectorDisposition,
    RelationshipKind,
    RepositoryProfileId,
    SkillContractError,
    SkillExecutionRole,
    SkillSource,
)
from autoskillit.workspace import SkillProjectionContext, replace_exploration_vector_bodies
from autoskillit.workspace.skills import (
    EffectiveSkillCatalog,
    SkillCatalogEntry,
    SkillInfo,
    _parse_exploration_vector_frontmatter,
    _skill_info_from_frontmatter,
)

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.small]


_FRONTMATTER = """---
name: vector-skill
description: Exercises exploration vector authoring.
execution_role: session
exploration_vectors:
  - id: trace-consumers
    disposition: migrated
    rationale: Native semantic navigation covers the reviewed vector.
    applicability: always
    role: semantic-code-navigator
    profile: generic-python
    relationship_classes: [references, affects]
    task_id: trace-consumers-task
    frontier_item_id: trace-consumers-frontier
    depends_on: []
    scope: [src]
    max_results: 100
    max_report_bytes: 20000
    evidence_version: 1
    native_dispatch: true
  - id: inspect-release-notes
    disposition: retained
    rationale: Human interpretation remains clearer for this prose-only vector.
    applicability: always
    role: null
    profile: language-neutral
    relationship_classes: [references]
    task_id: inspect-release-notes-task
    frontier_item_id: inspect-release-notes-frontier
    depends_on: []
    scope: [CHANGELOG.md]
    max_results: 20
    max_report_bytes: 5000
    evidence_version: 1
    native_dispatch: false
---
"""

_BODY = """# Vector skill

<!-- autoskillit:exploration-vector id="trace-consumers" -->
Search definitions and their consumers manually.
<!-- /autoskillit:exploration-vector -->

<!-- autoskillit:exploration-vector id="inspect-release-notes" -->
Read the release notes and explain the relevant historical intent.
<!-- /autoskillit:exploration-vector -->
"""


def _parse(tmp_path: Path, content: str = _FRONTMATTER + _BODY) -> SkillInfo:
    path = tmp_path / "SKILL.md"
    path.write_text(content, encoding="utf-8")
    return _skill_info_from_frontmatter("vector-skill", SkillSource.PROJECT_LOCAL, path)


def _raw_vector(**changes: object) -> dict[str, object]:
    vector: dict[str, object] = {
        "id": "trace-consumers",
        "disposition": "migrated",
        "rationale": "Inspect consumers.",
        "applicability": "always",
        "role": "semantic-code-navigator",
        "profile": "generic-python",
        "relationship_classes": ["references"],
        "task_id": "trace-consumers-task",
        "frontier_item_id": "trace-consumers-frontier",
        "depends_on": [],
        "scope": ["src"],
        "max_results": 100,
        "max_report_bytes": 20_000,
        "evidence_version": 1,
        "native_dispatch": True,
    }
    vector.update(changes)
    return vector


def test_vector_contract_builds_phase_b_task_and_survives_catalog_projection(
    tmp_path: Path,
) -> None:
    info = _parse(tmp_path)

    assert info.invalid_reason is None
    assert len(info.exploration_vectors) == 2
    migrated = info.exploration_vectors[0]
    assert migrated.disposition is ExplorationVectorDisposition.MIGRATED
    assert migrated.profile is RepositoryProfileId.GENERIC_PYTHON
    assert migrated.task.profile is RepositoryProfileId.GENERIC_PYTHON
    assert migrated.relationship_classes == (
        RelationshipKind.REFERENCES,
        RelationshipKind.AFFECTS,
    )
    assert migrated.body == "Search definitions and their consumers manually."
    assert len(migrated.digest) == 64
    assert SkillCatalogEntry.from_skill_info(info).exploration_vectors == info.exploration_vectors


def test_planner_extract_domain_deep_is_the_exact_closed_applicability_value(
    tmp_path: Path,
) -> None:
    content = (_FRONTMATTER + _BODY).replace(
        "    applicability: always\n",
        "    applicability: planner-extract-domain-deep\n",
        1,
    )

    info = _parse(tmp_path, content)

    assert info.invalid_reason is None
    assert (
        info.exploration_vectors[0].applicability
        is ExplorationVectorApplicabilityId.PLANNER_EXTRACT_DOMAIN_DEEP
    )
    assert info.exploration_vectors[0].applicability.value == "planner-extract-domain-deep"


def test_projection_context_vectors_keep_every_bound_skill_key(tmp_path: Path) -> None:
    vector_info = _parse(tmp_path)
    empty_content = (
        "---\nname: empty-skill\ndescription: No vectors.\nexecution_role: session\n---\n"
    )
    empty_info = SkillInfo(
        name="empty-skill",
        source=SkillSource.PROJECT_LOCAL,
        path=tmp_path / "empty/SKILL.md",
        execution_role=SkillExecutionRole.SESSION,
        canonical_content=empty_content,
    )
    catalog = EffectiveSkillCatalog(
        skills=(
            SkillCatalogEntry.from_skill_info(vector_info),
            SkillCatalogEntry.from_skill_info(empty_info),
        ),
        execution_role=SkillExecutionRole.SESSION,
    )

    vectors = SkillProjectionContext(cwd=tmp_path, catalog=catalog).exploration_vectors

    assert set(vectors) == {"vector-skill", "empty-skill"}
    assert vectors["empty-skill"] == ()


def test_replacement_changes_exactly_migrated_body_and_retains_reviewed_prose(
    tmp_path: Path,
) -> None:
    info = _parse(tmp_path)

    projected = replace_exploration_vector_bodies(
        info.canonical_content,
        info.exploration_vectors,
        {"trace-consumers": "Dispatch the canonical semantic navigation task packet."},
    )

    assert "Search definitions and their consumers manually." not in projected
    assert "Dispatch the canonical semantic navigation task packet." in projected
    assert "Read the release notes and explain the relevant historical intent." in projected
    assert projected.count('id="trace-consumers"') == 1


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        (
            _BODY.replace(
                '<!-- autoskillit:exploration-vector id="inspect-release-notes" -->\n',
                "",
            ).replace(
                "Read the release notes and explain the relevant historical intent.\n"
                "<!-- /autoskillit:exploration-vector -->\n",
                "",
                1,
            ),
            "missing exploration vector markers",
        ),
        (
            _BODY.replace(
                '<!-- autoskillit:exploration-vector id="trace-consumers" -->',
                '<!-- autoskillit:exploration-vector id="unknown-vector" -->',
            ),
            "unknown exploration vector marker",
        ),
        (
            _BODY.replace(
                "Search definitions and their consumers manually.",
                '<!-- autoskillit:exploration-vector id="trace-consumers" -->',
            ),
            "cannot be nested",
        ),
        (
            _BODY.replace(
                '<!-- autoskillit:exploration-vector id="trace-consumers" -->',
                '\\<!-- autoskillit:exploration-vector id="trace-consumers" -->',
            ),
            "malformed or embedded",
        ),
        (
            _BODY
            + '\n<!-- autoskillit:exploration-vector id="trace-consumers" -->\n'
            + "Duplicate body.\n<!-- /autoskillit:exploration-vector -->\n",
            "duplicate exploration vector marker",
        ),
        (
            "<!-- /autoskillit:exploration-vector -->\n" + _BODY,
            "mismatched exploration vector closing marker",
        ),
    ],
)
def test_marker_contract_rejects_missing_unknown_nested_and_escaped_tokens(
    tmp_path: Path,
    body: str,
    reason: str,
) -> None:
    info = _parse(tmp_path, _FRONTMATTER + body)

    assert info.invalid_reason is not None
    assert reason in info.invalid_reason
    assert info.exploration_vectors == ()


def test_frontmatter_vector_schema_rejects_unknown_keys(tmp_path: Path) -> None:
    content = (_FRONTMATTER + _BODY).replace(
        "    native_dispatch: true\n",
        "    native_dispatch: true\n    arbitrary_condition: branch == 'develop'\n",
        1,
    )

    info = _parse(tmp_path, content)

    assert info.invalid_reason is not None
    assert "exactly the registered keys" in info.invalid_reason


def test_frontmatter_vector_parser_requires_a_list_of_closed_mappings() -> None:
    with pytest.raises(SkillContractError, match="must be a list"):
        _parse_exploration_vector_frontmatter({})
    with pytest.raises(SkillContractError, match="exactly the registered keys"):
        _parse_exploration_vector_frontmatter(["not-a-mapping"])
    with pytest.raises(SkillContractError, match="exactly the registered keys"):
        _parse_exploration_vector_frontmatter([_raw_vector(extra="unexpected")])
    missing = _raw_vector()
    missing.pop("task_id")
    with pytest.raises(SkillContractError, match="exactly the registered keys"):
        _parse_exploration_vector_frontmatter([missing])


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("disposition", "unknown"),
        ("profile", "unknown"),
        ("applicability", "unknown"),
        ("relationship_classes", [1]),
        ("scope", [1]),
        ("max_results", True),
        ("max_report_bytes", False),
        ("evidence_version", True),
        ("native_dispatch", "true"),
        ("role", 7),
    ],
)
def test_frontmatter_vector_parser_rejects_invalid_closed_schema_values(
    field: str,
    value: object,
) -> None:
    with pytest.raises(SkillContractError):
        _parse_exploration_vector_frontmatter([_raw_vector(**{field: value})])


def test_frontmatter_vector_parser_accepts_valid_values_and_rejects_duplicate_ids() -> None:
    parsed = _parse_exploration_vector_frontmatter([_raw_vector()])

    assert parsed[0].id == "trace-consumers"
    with pytest.raises(SkillContractError, match="ids must be unique"):
        _parse_exploration_vector_frontmatter([_raw_vector(), _raw_vector()])


def test_replacement_requires_complete_migrated_set_and_rejects_marker_injection(
    tmp_path: Path,
) -> None:
    info = _parse(tmp_path)

    with pytest.raises(SkillContractError, match="exactly match migrated marker ids"):
        replace_exploration_vector_bodies(info.canonical_content, info.exploration_vectors, {})
    with pytest.raises(SkillContractError, match="contains a marker token"):
        replace_exploration_vector_bodies(
            info.canonical_content,
            info.exploration_vectors,
            {"trace-consumers": ('<!-- autoskillit:exploration-vector id="injected" -->')},
        )
    stale_vectors = (
        replace(info.exploration_vectors[0], body="Different canonical prose."),
        info.exploration_vectors[1],
    )
    with pytest.raises(SkillContractError, match="differs from its canonical parsed authority"):
        replace_exploration_vector_bodies(
            info.canonical_content,
            stale_vectors,
            {"trace-consumers": "Dispatch the typed task."},
        )
