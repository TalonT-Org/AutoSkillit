"""Contract: the UNAFFECTED-skill registry stays in sync with reality
(#4684 AC6, Step 1.16).

Issue #4684 AC6 requires "No new failure mode is introduced for the
unaffected skills (regression test on each)." A hardcoded count assertion
breaks on any new SKILL.md addition; KNOWN_UNAFFECTED_SKILL_IDS
(core/types/_type_constants.py, alongside RETIRED_SKILL_NAMES) follows the
codebase's retirement-registry discipline instead (tests/AGENTS.md §
Retirement Registries).

Lives in core/types/_type_constants.py rather than a bare module under
src/autoskillit/skills/ — that directory is a namespace package holding
only SKILL.md content today (no __init__.py, no other .py files); adding
importable code there means any import creates a skills/__pycache__/
directory, which scripts/check_doc_counts.py's naive "every directory
under skills/ is a skill" counter would then miscount as an extra skill.
Confirmed reproducing locally before choosing this location instead.

_discover_unaffected_skills() mirrors the negative predicate used by the
investigation and by tests/skills/test_exploration_vector_preflight.py's
marker discovery: a skill is UNAFFECTED iff its SKILL.md carries no
``<!-- autoskillit:exploration-vector id="..." -->`` marker, regardless of
whether any for_each fan-out over exploration vectors is actively wired.
"""

from __future__ import annotations

import re

import pytest

from autoskillit.core import KNOWN_UNAFFECTED_SKILL_IDS, pkg_root

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]

_MARKER_RE = re.compile(r'<!--\s*autoskillit:exploration-vector\s+id="')


def _discover_unaffected_skills() -> frozenset[str]:
    roots = [pkg_root() / "skills", pkg_root() / "skills_extended"]
    unaffected: set[str] = set()
    for root in roots:
        if not root.exists():
            continue
        for skill_md in sorted(root.glob("*/SKILL.md")):
            text = skill_md.read_text(encoding="utf-8")
            if not _MARKER_RE.search(text):
                unaffected.add(skill_md.parent.name)
    return frozenset(unaffected)


def _read_skill_md(skill_id: str) -> str:
    for root in (pkg_root() / "skills", pkg_root() / "skills_extended"):
        candidate = root / skill_id / "SKILL.md"
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8")
    raise FileNotFoundError(
        f"KNOWN_UNAFFECTED_SKILL_IDS entry {skill_id!r} has no SKILL.md under "
        "skills/ or skills_extended/ — retired or renamed skills must be removed "
        "from the registry in the same PR."
    )


def test_unaffected_skill_set_is_stable() -> None:
    actual = _discover_unaffected_skills()
    new_in_actual = actual - KNOWN_UNAFFECTED_SKILL_IDS
    removed_from_registry = KNOWN_UNAFFECTED_SKILL_IDS - actual
    assert not new_in_actual, (
        f"New UNAFFECTED skill(s) added without registry update: {sorted(new_in_actual)}"
    )
    assert not removed_from_registry, (
        f"Skill(s) left the UNAFFECTED set but are still in the registry: "
        f"{sorted(removed_from_registry)}"
    )


@pytest.mark.parametrize("skill_id", sorted(KNOWN_UNAFFECTED_SKILL_IDS))
def test_no_new_broker_coupling_in_unaffected_skills(skill_id: str) -> None:
    """UNAFFECTED skills must remain free of broker coupling — catches both
    new exploration-vector markers AND new ad-hoc enable_exploration
    references (the indirect-coupling failure mode AC6 guards against)."""
    text = _read_skill_md(skill_id)
    assert "<!-- autoskillit:exploration-vector id=" not in text, (
        f"{skill_id} acquired an exploration-vector marker; update "
        "KNOWN_UNAFFECTED_SKILL_IDS or move the skill to the BLOCKED/DEGRADED "
        "bucket via a tracking issue."
    )
    assert "enable_exploration" not in text, (
        f"{skill_id} acquired broker coupling without a marker; this is the "
        "indirect-coupling failure mode AC6 guards against."
    )


def test_discovery_predicate_matches_a_known_blocked_and_unaffected_skill() -> None:
    """Sanity: the predicate correctly classifies one known-affected and one
    known-unaffected skill, so a broken predicate can't pass vacuously."""
    discovered = _discover_unaffected_skills()
    assert "arch-lens-c4-container" not in discovered, (
        "arch-lens-c4-container has a for_each: exploration_vectors fan-out and "
        "must be classified as affected, not unaffected"
    )
    assert "open-kitchen" in discovered


def test_registry_is_lowercase() -> None:
    """Skill directory names (and therefore registry entries) are lowercase,
    matching the RETIRED_SKILL_NAMES precedent's own invariant."""
    non_lowercase = sorted(s for s in KNOWN_UNAFFECTED_SKILL_IDS if s != s.lower())
    assert not non_lowercase, (
        f"KNOWN_UNAFFECTED_SKILL_IDS entries must be lowercase: {non_lowercase}"
    )
