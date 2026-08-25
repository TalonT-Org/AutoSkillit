"""Contract: the BLOCKED/DEGRADED-skill registries stay in sync with reality
(#4684 AC7, Step 5).

Completes the skill-impact matrix that tests/contracts/test_unaffected_skill_registry.py
started: KNOWN_UNAFFECTED_SKILL_IDS was the only registry-pinned bucket before this
file. A skill is BLOCKED iff its SKILL.md carries the exploration-vector marker AND
declares a `for_each: exploration_vectors` child-spawn fan-out; DEGRADED iff it carries
the marker but no such fan-out. Mirrors _discover_unaffected_skills()'s predicate and
roots exactly, so the three buckets partition the same population.
"""

from __future__ import annotations

import pytest

from autoskillit.core import (
    KNOWN_BLOCKED_SKILL_IDS,
    KNOWN_DEGRADED_SKILL_IDS,
    KNOWN_UNAFFECTED_SKILL_IDS,
    pkg_root,
)
from tests.contracts._skill_discovery import FOR_EACH_RE, MARKER_RE, iter_skill_md_files

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]


def _discover_skill_impact_buckets() -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    """Return (blocked, degraded, unaffected) discovered live over skills/ + skills_extended/."""
    blocked: set[str] = set()
    degraded: set[str] = set()
    unaffected: set[str] = set()
    for skill_md in iter_skill_md_files():
        text = skill_md.read_text(encoding="utf-8")
        name = skill_md.parent.name
        if not MARKER_RE.search(text):
            unaffected.add(name)
        elif FOR_EACH_RE.search(text):
            blocked.add(name)
        else:
            degraded.add(name)
    return frozenset(blocked), frozenset(degraded), frozenset(unaffected)


def _read_skill_md(skill_id: str) -> str:
    for root in (pkg_root() / "skills", pkg_root() / "skills_extended"):
        candidate = root / skill_id / "SKILL.md"
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8")
    raise FileNotFoundError(
        f"registry entry {skill_id!r} has no SKILL.md under skills/ or "
        "skills_extended/ — retired or renamed skills must be removed from the "
        "registry in the same PR."
    )


def test_blocked_skill_set_is_stable() -> None:
    actual, _degraded, _unaffected = _discover_skill_impact_buckets()
    new_in_actual = actual - KNOWN_BLOCKED_SKILL_IDS
    removed_from_registry = KNOWN_BLOCKED_SKILL_IDS - actual
    assert not new_in_actual, (
        f"New BLOCKED skill(s) added without registry update: {sorted(new_in_actual)}"
    )
    assert not removed_from_registry, (
        f"Skill(s) left the BLOCKED set but are still in the registry: "
        f"{sorted(removed_from_registry)}"
    )


def test_degraded_skill_set_is_stable() -> None:
    _blocked, actual, _unaffected = _discover_skill_impact_buckets()
    new_in_actual = actual - KNOWN_DEGRADED_SKILL_IDS
    removed_from_registry = KNOWN_DEGRADED_SKILL_IDS - actual
    assert not new_in_actual, (
        f"New DEGRADED skill(s) added without registry update: {sorted(new_in_actual)}"
    )
    assert not removed_from_registry, (
        f"Skill(s) left the DEGRADED set but are still in the registry: "
        f"{sorted(removed_from_registry)}"
    )


def test_buckets_are_pairwise_disjoint() -> None:
    blocked_degraded = KNOWN_BLOCKED_SKILL_IDS & KNOWN_DEGRADED_SKILL_IDS
    blocked_unaffected = KNOWN_BLOCKED_SKILL_IDS & KNOWN_UNAFFECTED_SKILL_IDS
    degraded_unaffected = KNOWN_DEGRADED_SKILL_IDS & KNOWN_UNAFFECTED_SKILL_IDS
    assert not blocked_degraded, f"BLOCKED/DEGRADED overlap: {sorted(blocked_degraded)}"
    assert not blocked_unaffected, f"BLOCKED/UNAFFECTED overlap: {sorted(blocked_unaffected)}"
    assert not degraded_unaffected, f"DEGRADED/UNAFFECTED overlap: {sorted(degraded_unaffected)}"


def test_buckets_partition_the_combined_skill_population() -> None:
    """|BLOCKED| + |DEGRADED| + |UNAFFECTED| == the live discovered population, self-adjusting
    as skills are added with a conscious registry edit rather than pinning a bare count."""
    blocked, degraded, unaffected = _discover_skill_impact_buckets()
    discovered_total = len(blocked) + len(degraded) + len(unaffected)
    registry_total = (
        len(KNOWN_BLOCKED_SKILL_IDS)
        + len(KNOWN_DEGRADED_SKILL_IDS)
        + len(KNOWN_UNAFFECTED_SKILL_IDS)
    )
    assert registry_total == discovered_total, (
        f"registry total {registry_total} != discovered population {discovered_total}"
    )


def test_discovery_predicate_matches_a_known_blocked_and_degraded_skill() -> None:
    """Sanity: the predicate correctly classifies one known-BLOCKED and one
    known-DEGRADED skill, so a broken predicate can't pass vacuously."""
    blocked, degraded, _unaffected = _discover_skill_impact_buckets()
    assert "arch-lens-c4-container" in blocked, (
        "arch-lens-c4-container has a for_each: exploration_vectors fan-out and "
        "must be classified BLOCKED"
    )
    assert "scope" in degraded, (
        "scope carries the exploration-vector marker (12 retained: vectors) with "
        "no for_each fan-out and must be classified DEGRADED"
    )


@pytest.mark.parametrize("skill_id", sorted(KNOWN_BLOCKED_SKILL_IDS | KNOWN_DEGRADED_SKILL_IDS))
def test_registry_entry_still_carries_the_exploration_vector_marker(skill_id: str) -> None:
    text = _read_skill_md(skill_id)
    assert MARKER_RE.search(text), (
        f"{skill_id} lost its exploration-vector marker; it should move to "
        "KNOWN_UNAFFECTED_SKILL_IDS via a tracking issue."
    )


def test_registries_are_lowercase() -> None:
    for name, registry in (
        ("KNOWN_BLOCKED_SKILL_IDS", KNOWN_BLOCKED_SKILL_IDS),
        ("KNOWN_DEGRADED_SKILL_IDS", KNOWN_DEGRADED_SKILL_IDS),
    ):
        non_lowercase = sorted(s for s in registry if s != s.lower())
        assert not non_lowercase, f"{name} entries must be lowercase: {non_lowercase}"
