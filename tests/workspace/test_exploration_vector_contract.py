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
    _parse_exploration_sidecar,
    _skill_info_from_frontmatter,
)

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.small]


_FRONTMATTER = """---
name: vector-skill
description: Exercises exploration vector authoring.
execution_role: session
---
"""

_SIDECAR = """vectors:
- id: trace-consumers
  role: semantic-code-navigator
  relationship_classes: [references, affects]
  rationale: Native semantic navigation covers the reviewed vector.
  applicability: always
retained:
- id: inspect-release-notes
  rationale: Human interpretation remains clearer for this prose-only vector.
"""

_BODY = """# Vector skill

<!-- autoskillit:exploration-vector id="trace-consumers" -->
Search definitions and their consumers manually.
<!-- /autoskillit:exploration-vector -->

<!-- autoskillit:exploration-vector id="inspect-release-notes" -->
Read the release notes and explain the relevant historical intent.
<!-- /autoskillit:exploration-vector -->
"""


def _parse(
    tmp_path: Path,
    content: str = _FRONTMATTER + _BODY,
    sidecar: str | None = _SIDECAR,
) -> SkillInfo:
    path = tmp_path / "SKILL.md"
    path.write_text(content, encoding="utf-8")
    if sidecar is not None:
        (tmp_path / "exploration.yaml").write_text(sidecar, encoding="utf-8")
    return _skill_info_from_frontmatter("vector-skill", SkillSource.PROJECT_LOCAL, path)


def _raw_vector(**changes: object) -> dict[str, object]:
    vector: dict[str, object] = {
        "id": "trace-consumers",
        "role": "semantic-code-navigator",
        "relationship_classes": ["references"],
        "rationale": "Inspect consumers.",
        "applicability": "always",
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
    assert migrated.profile is RepositoryProfileId.AUTO
    assert migrated.task.profile is RepositoryProfileId.AUTO
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
    sidecar = _SIDECAR.replace(
        "  applicability: always\n",
        "  applicability: planner-extract-domain-deep\n",
        1,
    )

    info = _parse(tmp_path, sidecar=sidecar)

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


def test_sidecar_vector_schema_rejects_unknown_keys(tmp_path: Path) -> None:
    sidecar = _SIDECAR.replace(
        "  applicability: always\n",
        "  applicability: always\n  arbitrary_condition: branch == 'develop'\n",
        1,
    )

    info = _parse(tmp_path, sidecar=sidecar)

    assert info.invalid_reason is not None
    assert "unknown keys" in info.invalid_reason


def test_sidecar_vector_parser_requires_a_mapping_with_valid_entries() -> None:
    assert _parse_exploration_sidecar(None, "vector-skill") == ()
    assert _parse_exploration_sidecar({}, "vector-skill") == ()
    with pytest.raises(SkillContractError, match="must be a YAML mapping"):
        _parse_exploration_sidecar(["not-a-mapping"], "vector-skill")
    with pytest.raises(SkillContractError, match="unknown top-level keys"):
        _parse_exploration_sidecar({"unexpected": []}, "vector-skill")
    with pytest.raises(SkillContractError, match="vectors\\[0\\] must be a mapping"):
        _parse_exploration_sidecar({"vectors": ["not-a-mapping"]}, "vector-skill")
    with pytest.raises(SkillContractError, match="retained\\[0\\] must be a mapping"):
        _parse_exploration_sidecar({"retained": ["not-a-mapping"]}, "vector-skill")
    with pytest.raises(SkillContractError, match="unknown keys"):
        _parse_exploration_sidecar({"vectors": [_raw_vector(extra="unexpected")]}, "vector-skill")
    missing = _raw_vector()
    del missing["role"]
    with pytest.raises(SkillContractError, match="contains an invalid value"):
        _parse_exploration_sidecar({"vectors": [missing]}, "vector-skill")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", 7),
        ("role", 7),
        ("rationale", ""),
        ("applicability", "unknown"),
        ("relationship_classes", [1]),
    ],
)
def test_sidecar_vector_parser_rejects_invalid_migrated_field_values(
    field: str,
    value: object,
) -> None:
    with pytest.raises(SkillContractError, match="contains an invalid value"):
        _parse_exploration_sidecar({"vectors": [_raw_vector(**{field: value})]}, "vector-skill")


def test_sidecar_vector_parser_accepts_valid_values_and_rejects_duplicate_ids() -> None:
    parsed = _parse_exploration_sidecar({"vectors": [_raw_vector()]}, "vector-skill")

    assert parsed[0].id == "trace-consumers"
    with pytest.raises(SkillContractError, match="ids must be unique"):
        _parse_exploration_sidecar(
            {"vectors": [_raw_vector(), _raw_vector()]},
            "vector-skill",
        )


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
