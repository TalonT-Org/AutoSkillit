import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("skills"), pytest.mark.medium]

RESOLVE_SKILL_MD = (
    Path(__file__).parent.parent.parent
    / "src"
    / "autoskillit"
    / "skills_extended"
    / "resolve-review"
    / "SKILL.md"
).read_text()

REVIEW_PR_SKILL_MD = (
    Path(__file__).parent.parent.parent
    / "src"
    / "autoskillit"
    / "skills_extended"
    / "review-pr"
    / "SKILL.md"
).read_text()


def _extract_section(text: str, heading: str) -> str:
    pattern = re.compile(
        r"###\s+" + re.escape(heading) + r"\b(.*?)(?=\n###|\Z)",
        re.DOTALL,
    )
    m = pattern.search(text)
    assert m, f"Section not found: {heading!r}"
    return m.group(1)


def _extract_graphql_block(section_text: str) -> str:
    m = re.search(r"```graphql\n(.*?)```", section_text, re.DOTALL)
    if m:
        return m.group(1)
    # Fall back to any triple-backtick block containing 'query' or 'mutation'
    for block_match in re.finditer(r"```[^\n]*\n(.*?)```", section_text, re.DOTALL):
        content = block_match.group(1)
        if "query" in content or "mutation" in content:
            return content
    return ""


def test_step2_graphql_fetches_five_comments_with_body() -> None:
    step2_section = _extract_section(RESOLVE_SKILL_MD, "Step 2: Fetch Review Comments")
    assert "comments(first:5)" in step2_section, "Step 2 GraphQL must use comments(first:5)"
    graphql_block = _extract_graphql_block(step2_section)
    assert "body" in graphql_block, "Step 2 GraphQL comment nodes must include body field"


def test_step2_defines_already_replied_ids() -> None:
    step2_section = _extract_section(RESOLVE_SKILL_MD, "Step 2: Fetch Review Comments")
    assert "already_replied_ids" in step2_section
    assert "autoskillit:resolved" in step2_section


def test_step2_skip_log_message() -> None:
    step2_section = _extract_section(RESOLVE_SKILL_MD, "Step 2: Fetch Review Comments")
    assert "already resolved by prior resolve-review run" in step2_section


def test_step3_skips_already_replied_ids() -> None:
    step3_section = _extract_section(RESOLVE_SKILL_MD, "Step 3: Parse and Classify Findings")
    assert "already_replied_ids" in step3_section


def test_step15_graphql_fetches_five_comments() -> None:
    step15_section = _extract_section(
        REVIEW_PR_SKILL_MD, "Step 1.5: Fetch Prior Review Thread Context"
    )
    assert "comments(first:5)" in step15_section, "Step 1.5 GraphQL must use comments(first:5)"


def test_step15_graphql_includes_body_field() -> None:
    step15_section = _extract_section(
        REVIEW_PR_SKILL_MD, "Step 1.5: Fetch Prior Review Thread Context"
    )
    graphql_block = _extract_graphql_block(step15_section)
    assert "body" in graphql_block


def test_step15_graphql_includes_database_id() -> None:
    step15_section = _extract_section(
        REVIEW_PR_SKILL_MD, "Step 1.5: Fetch Prior Review Thread Context"
    )
    graphql_block = _extract_graphql_block(step15_section)
    assert "databaseId" in graphql_block


def test_step15_marker_bearing_threads_are_resolved() -> None:
    step15_section = _extract_section(
        REVIEW_PR_SKILL_MD, "Step 1.5: Fetch Prior Review Thread Context"
    )
    assert "autoskillit:resolved" in step15_section
    assert "prior_resolved_findings" in step15_section
    assert "has_marker_reply" in step15_section or "marker" in step15_section.lower()


def test_legacy_marker_reads_remain_compatible_across_skills() -> None:
    review_mentions = REVIEW_PR_SKILL_MD.count("autoskillit:resolved")
    assert "autoskillit:resolved" in RESOLVE_SKILL_MD
    assert "marker-bearing" in RESOLVE_SKILL_MD
    assert "may be read" in RESOLVE_SKILL_MD
    assert review_mentions >= 3, "review-pr must reference the marker at least 3 times"
