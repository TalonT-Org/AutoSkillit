"""Typed skill write boundaries survive admission and projection."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from autoskillit.core import SKILL_CONTRACT_REMEDIATIONS, SkillInvalidityKind, SkillSource
from autoskillit.workspace._projected_artifact.materialization import (
    AgentSkillDocument,
    _projection_skills_manifest,
)
from autoskillit.workspace.session_skills import _parse_write_paths
from autoskillit.workspace.skills import _skill_info_from_frontmatter

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.small]


def _skill(tmp_path: Path, declaration: str):
    skill_path = tmp_path / "bounded" / "SKILL.md"
    skill_path.parent.mkdir(exist_ok=True)
    skill_path.write_text(
        "---\nname: bounded\ndescription: Bounded output.\n" + declaration + "---\n# Bounded\n",
        encoding="utf-8",
    )
    return _skill_info_from_frontmatter("bounded", SkillSource.PROJECT_LOCAL, skill_path)


def test_invalid_write_paths_has_typed_invalidity_and_remediation(tmp_path: Path) -> None:
    info = _skill(tmp_path, "write_paths: bad\n")

    assert info.write_paths is None
    assert any(
        invalidity.kind == SkillInvalidityKind.WRITE_BOUNDARY_INVALID
        for invalidity in info.invalidities
    )
    assert set(SKILL_CONTRACT_REMEDIATIONS) == set(SkillInvalidityKind)


@pytest.mark.parametrize(
    ("declaration", "expected"),
    [
        ("", None),
        ("write_paths: []\n", ()),
        ("write_paths: ['{{AUTOSKILLIT_TEMP}}/bounded/']\n", ("{{AUTOSKILLIT_TEMP}}/bounded/",)),
    ],
)
def test_write_boundary_states_survive_loader_and_manifest(
    tmp_path: Path, declaration: str, expected: tuple[str, ...] | None
) -> None:
    info = _skill(tmp_path, declaration)
    assert info.write_paths == expected
    assert info.frontmatter is not None
    assert info.frontmatter.write_paths == expected
    assert _parse_write_paths(info.frontmatter) == list(expected or ())

    digest = hashlib.sha256(info.canonical_content.encode()).hexdigest()
    document = AgentSkillDocument(
        content=info.canonical_content,
        projected_digest=digest,
        canonical_digest=info.canonical_digest,
        source_identity=info.source_identity,
    )
    entry = _projection_skills_manifest((info,), {info.name: document})[info.name]
    assert entry["write_paths"] == (list(expected) if expected is not None else None)
