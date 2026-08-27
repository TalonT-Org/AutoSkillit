"""Derived table-count contracts for prose that describes skill resources."""

from __future__ import annotations

import re

import pytest
from autoskillit.workspace.skill_resources import load_skill_resource

from autoskillit.core import pkg_root
from autoskillit.workspace.skills import DefaultSkillResolver

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.medium]

_GFM_ROW = re.compile(r"^\|.*\|\s*$")
_GFM_SEPARATOR = re.compile(r"^\|(?:\s*:?-{3,}:?\s*\|)+\s*$")
_AUTHORED_COUNT = re.compile(
    r"\b(?P<count>\d+)\s+(?:rows?|entries|constraints|criteria)\b", re.IGNORECASE
)


def _gfm_table_row_count(body: str) -> int | None:
    """Count data rows when a body contains exactly one GFM pipe table."""
    tables: list[list[str]] = []
    current: list[str] = []
    for line in body.splitlines():
        if _GFM_ROW.fullmatch(line):
            current.append(line)
        elif current:
            tables.append(current)
            current = []
    if current:
        tables.append(current)
    if len(tables) != 1:
        return None
    table = tables[0]
    if len(table) < 2 or not _GFM_SEPARATOR.fullmatch(table[1]):
        return None
    return len(table) - 2


def test_resource_table_row_counts_exclude_the_header_and_separator() -> None:
    """The registry's count derives only data rows from a resource's sole table."""
    table_resources = []
    for resource_path in sorted((pkg_root() / "skill_resources").glob("*.md")):
        resource = load_skill_resource(resource_path.stem)
        expected = _gfm_table_row_count(resource.body)
        if expected is not None:
            table_resources.append(resource)
            assert resource.table_row_count == expected, resource.id
    assert table_resources, "expected at least one single-table skill resource"


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
