from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("skills"), pytest.mark.medium]

SKILL_PATH = (
    Path(__file__).parents[2]
    / "src"
    / "autoskillit"
    / "skills_extended"
    / "audit-docs"
    / "SKILL.md"
)


def test_audit_docs_routes_exactly_ten_closed_world_evidence_vectors() -> None:
    text = SKILL_PATH.read_text(encoding="utf-8")
    vector_ids = re.findall(r'autoskillit:exploration-vector id="([^"]+)"', text)

    assert len(vector_ids) == 10
    assert len(set(vector_ids)) == 10
    # Strip the YAML frontmatter so the structural contract is checked against
    # the prose body only — the rectified semantic_requirements block declares
    # the authoritative logical role (`delegated-worker`) and the role-spawn
    # mapping, but the "no prose 'delegated-worker' as a role" rule applies to
    # the body, not the structured frontmatter.
    body = text.split("---", 2)[-1] if text.startswith("---") else text
    assert "delegated-worker" not in body
    for vector_id in vector_ids:
        body = text.split(f'<!-- autoskillit:exploration-vector id="{vector_id}" -->', maxsplit=1)[
            1
        ].split("<!-- /autoskillit:exploration-vector -->", maxsplit=1)[0]
        for field in (
            "**Objective:**",
            "**Entry point:**",
            "**Tool/source guidance:**",
            "**Scope boundary:**",
            "**Ignore list:**",
            "**Expected typed output:**",
            "answered | partial | blocked",
        ):
            assert field in body, f"{vector_id} is missing {field}"


def test_audit_docs_keeps_judgment_and_writes_in_parent() -> None:
    text = SKILL_PATH.read_text(encoding="utf-8")
    assert "assemble the doc inventory" in text
    assert "secondary absence checks" in text
    assert "deduplicate by file:line" in text
    assert "Self-validation pass (parent only)" in text
    assert "Write report (parent only)" in text
    assert "Every mutation remains in the parent" in text
