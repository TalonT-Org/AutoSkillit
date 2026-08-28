"""Derived table-count contracts for prose that describes skill resources."""

from __future__ import annotations

import re

import pytest

from autoskillit.workspace.skill_resources import load_skill_resource
from autoskillit.workspace.skills import DefaultSkillResolver

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.medium]

_AUTHORED_COUNT = re.compile(
    r"\b(?P<count>\d+)\s+(?:rows?|entries|constraints|criteria)\b", re.IGNORECASE
)


@pytest.mark.parametrize(
    ("resource_id", "expected_count"),
    [
        ("arch-constraint-catalog", 67),
        ("review-approach-criteria", None),
    ],
)
def test_resource_table_row_counts_match_registered_content(
    resource_id: str, expected_count: int | None
) -> None:
    """Registered counts are checked against an independent, explicit oracle."""
    assert load_skill_resource(resource_id).table_row_count == expected_count


def test_resource_consumer_prose_counts_match_the_derived_table_count() -> None:
    """Only explicit resource-naming prose claims are checked for stale counts."""
    failures: list[str] = []
    for skill in DefaultSkillResolver().list_all():
        body = skill.frontmatter.body if skill.frontmatter is not None else skill.canonical_content
        for resource_id in skill.required_resources:
            resource = load_skill_resource(resource_id)
            if resource.table_row_count is None:
                continue
            for paragraph in re.split(r"\n\s*\n", body):
                if resource_id not in paragraph and resource.title not in paragraph:
                    continue
                for match in _AUTHORED_COUNT.finditer(paragraph):
                    actual = int(match.group("count"))
                    if actual != resource.table_row_count:
                        failures.append(
                            f"{skill.name}: {resource_id} prose claims {actual}, but its "
                            f"resource has {resource.table_row_count} data rows"
                        )
    assert not failures, "Stale resource table-count prose:\n" + "\n".join(failures)
