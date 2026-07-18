"""Contracts for the canonical output-discipline policy shared by cohort skills."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest

from autoskillit.core import (
    CODEX_INTAKE_DISCIPLINE_DIGEST,
    CODEX_INTAKE_DISCIPLINE_VERSION,
    OUTPUT_DISCIPLINE_BLOCK,
    OUTPUT_DISCIPLINE_BLOCK_SHA256,
    OUTPUT_DISCIPLINE_DIGEST,
    OUTPUT_DISCIPLINE_POLICY_VERSION,
    OUTPUT_DISCIPLINE_REQUIRED_SKILLS,
)
from autoskillit.core.paths import pkg_root

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.medium]

_BEGIN_MARKER = "<!-- output-discipline:begin -->"
_END_MARKER = "<!-- output-discipline:end -->"
_SKILL_ROOTS = (pkg_root() / "skills", pkg_root() / "skills_extended")
_EXPECTED_REQUIRED_SKILLS = frozenset({"investigate", "rectify", "audit-bugs", "audit-friction"})


def _skill_path(skill_name: str) -> Path:
    matches = [root / skill_name / "SKILL.md" for root in _SKILL_ROOTS]
    existing = [path for path in matches if path.is_file()]
    assert len(existing) == 1, (
        f"Expected exactly one bundled SKILL.md for {skill_name!r}, found {existing}"
    )
    return existing[0]


def _extract_policy_block(skill_text: str, *, skill_name: str) -> str:
    assert skill_text.count(_BEGIN_MARKER) == 1, (
        f"{skill_name}/SKILL.md must contain exactly one {_BEGIN_MARKER!r} marker"
    )
    assert skill_text.count(_END_MARKER) == 1, (
        f"{skill_name}/SKILL.md must contain exactly one {_END_MARKER!r} marker"
    )
    start = skill_text.index(f"{_BEGIN_MARKER}\n") + len(f"{_BEGIN_MARKER}\n")
    end = skill_text.index(f"\n{_END_MARKER}", start)
    return skill_text[start:end]


def test_canonical_policy_identity() -> None:
    assert OUTPUT_DISCIPLINE_POLICY_VERSION == 1
    assert f"v{OUTPUT_DISCIPLINE_POLICY_VERSION}" in OUTPUT_DISCIPLINE_BLOCK
    assert f"v{OUTPUT_DISCIPLINE_POLICY_VERSION}" in OUTPUT_DISCIPLINE_DIGEST
    assert (
        OUTPUT_DISCIPLINE_BLOCK_SHA256
        == sha256(OUTPUT_DISCIPLINE_BLOCK.encode("utf-8")).hexdigest()
    )


def test_required_skill_cohort_is_explicit_and_frozen() -> None:
    assert isinstance(OUTPUT_DISCIPLINE_REQUIRED_SKILLS, frozenset)
    assert OUTPUT_DISCIPLINE_REQUIRED_SKILLS == _EXPECTED_REQUIRED_SKILLS


@pytest.mark.parametrize("skill_name", sorted(_EXPECTED_REQUIRED_SKILLS))
def test_required_skill_contains_byte_identical_policy_block(skill_name: str) -> None:
    skill_text = _skill_path(skill_name).read_text(encoding="utf-8")
    assert _extract_policy_block(skill_text, skill_name=skill_name) == OUTPUT_DISCIPLINE_BLOCK


def test_policy_marker_has_no_unmanaged_skill_copies() -> None:
    marked_skills = {
        skill_md.parent.name
        for root in _SKILL_ROOTS
        for skill_md in root.glob("*/SKILL.md")
        if _BEGIN_MARKER in skill_md.read_text(encoding="utf-8")
    }
    assert marked_skills == OUTPUT_DISCIPLINE_REQUIRED_SKILLS


def test_digest_is_safe_for_agent_toml_multiline_literal_guard() -> None:
    assert "'''" not in OUTPUT_DISCIPLINE_DIGEST


def test_intake_digest_pins_numeric_rules() -> None:
    anchors = [
        "at most 2 files per exec command",
        "at most 250 lines",
        "max_output_tokens above 10000",
        "at most 2 of the listed files",
        "an index, not required reading",
        "fresh context",
        'fork_turns "none"',
        'defaults to "all"',
        "explicit narrow brief",
        "return a summary",
    ]
    for anchor in anchors:
        assert anchor in CODEX_INTAKE_DISCIPLINE_DIGEST, (
            f"Missing anchor in intake digest: {anchor!r}"
        )
    assert f"v{CODEX_INTAKE_DISCIPLINE_VERSION}" in CODEX_INTAKE_DISCIPLINE_DIGEST


def test_intake_digest_is_safe_for_agent_toml_multiline_literal() -> None:
    assert "'''" not in CODEX_INTAKE_DISCIPLINE_DIGEST
