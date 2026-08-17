"""Skill-text helpers for tests/skills/.

Centralized helpers for asserting Step 7 self-check instructions across the
three bundled validate-* skills (validate-audit, validate-review-decisions,
validate-test-audit).

Kept out of ``tests/skills/conftest.py`` so callers can import them without
triggering pytest's plugin/fixture registration, and so the
``@functools.cache`` on ``resolve_skill_text`` is only paid when one of these
helpers is actually invoked by an imported test module.
"""

from __future__ import annotations

import functools
import re

from autoskillit.workspace.skills import DefaultSkillResolver

#: Canonical skill for the Step 7 self-check block. ``validate-test-audit``
#: was the first one written and is byte-equal to the other two; the
#: ``tests/skills/test_ticket_grouper_rules_sync.py`` test enforces that.
CANONICAL_TICKET_GROUPER_SKILL = "validate-test-audit"


@functools.cache
def resolve_skill_text(skill_name: str) -> str:
    """Read a bundled skill's ``SKILL.md`` as UTF-8 text, caching by name.

    The cache is intentional: tests assert byte-stable invariants on bundled
    skills, which are read-only under the test harness. Callers MUST NOT
    mutate the resolved skill file mid-session — the cached copy will go
    stale and downstream callers will keep reading the original bytes.
    """
    info = DefaultSkillResolver().resolve(skill_name)
    assert info is not None, f"{skill_name} skill not found"
    return info.path.read_text(encoding="utf-8")


def extract_step7_grouper_block(text: str) -> str:
    """Return the Step 7 '**From Ticket Grouper:**' block, up to ``### Step 8``.

    Anchors (``**From Ticket Grouper:**`` then ``### Step 8``) are ordered so
    that ``start < end`` always holds in the current SKILL.md layout. Returns
    the empty string if either anchor is missing.
    """
    start = text.find("**From Ticket Grouper:**")
    end = text.find("### Step 8 ")
    if start == -1 or end == -1:
        return ""
    return text[start:end]


def _extract_pre_step7_grouper_section(text: str) -> str:
    """Return the Subagent B 'Ticket Grouper' instructions up through ``### Step 7``.

    Includes the 'Subagent B — Ticket Grouper' heading, the prose instructions,
    and the '## Grouping Manifest' output format spec. Stops before the Step 7
    cross-validation body so the effort-based-splitting assertions below scan
    only the planning section.
    """
    start = text.find("**Subagent B — Ticket Grouper**")
    if start == -1:
        return ""
    end = text.find("### Step 7")
    if end == -1:
        return text[start:]
    return text[start:end]


def assert_ticket_grouper_has_minimum_group_floor(text: str) -> None:
    """Assert the Ticket Grouper instructions enforce a minimum group count floor."""
    grouper_section = _extract_pre_step7_grouper_section(text)
    has_floor = bool(
        re.search(
            r"(?:minimum|at least|floor|must produce)",
            grouper_section,
            re.IGNORECASE,
        )
    )
    assert has_floor, (
        "Ticket Grouper instructions must enforce a minimum group count "
        "floor to prevent single-group mega-issues"
    )


def assert_ticket_grouper_has_effort_based_splitting(text: str) -> None:
    """Assert the Ticket Grouper instructions include effort-based splitting rules."""
    grouper_section = _extract_pre_step7_grouper_section(text)
    has_effort = bool(
        re.search(
            r"(?:effort-based|line count|high effort|medium effort)",
            grouper_section,
            re.IGNORECASE,
        )
    )
    assert has_effort, (
        "Ticket Grouper instructions must include effort-based splitting rules "
        "for findings that enumerate multiple files"
    )
