"""Typed skill write scopes survive admission and projection through one decoder."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from autoskillit.core import SKILL_CONTRACT_REMEDIATIONS, SkillInvalidityKind, SkillSource
from autoskillit.hooks._session_binding import manifest_skill_write_scope
from autoskillit.hooks._write_scope import WriteScope, WriteScopeKind
from autoskillit.workspace._projected_artifact.materialization import (
    AgentSkillDocument,
    _projection_skills_manifest,
)
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


def _kinds(info) -> set[SkillInvalidityKind]:
    return {invalidity.kind for invalidity in info.invalidities}


def test_absent_declaration_is_undeclared(tmp_path: Path) -> None:
    info = _skill(tmp_path, "")

    assert info.write_scope is None
    assert _kinds(info) == {SkillInvalidityKind.WRITE_BOUNDARY_UNDECLARED}
    assert set(SKILL_CONTRACT_REMEDIATIONS) == set(SkillInvalidityKind)


@pytest.mark.parametrize(
    ("declaration", "message"),
    [
        ("write_paths: bad\n", "'unrestricted' or 'inherit'"),
        ("write_paths: null\n", "non-empty list"),
        ("write_paths: []\n", "use `inherit`"),
        ("write_paths: ['/abs/']\n", "must start with"),
    ],
)
def test_invalid_declaration_has_typed_invalidity(
    tmp_path: Path, declaration: str, message: str
) -> None:
    info = _skill(tmp_path, declaration)

    assert info.write_scope is None
    assert _kinds(info) == {SkillInvalidityKind.WRITE_BOUNDARY_INVALID}
    assert message in info.invalidities[0].detail


@pytest.mark.parametrize(
    ("declaration", "expected"),
    [
        (
            "write_paths: ['{{AUTOSKILLIT_TEMP}}/bounded/']\n",
            WriteScope(WriteScopeKind.BOUNDED, ("{{AUTOSKILLIT_TEMP}}/bounded/",)),
        ),
        ("write_paths: unrestricted\n", WriteScope(WriteScopeKind.UNRESTRICTED)),
        ("write_paths: inherit\n", WriteScope(WriteScopeKind.INHERIT)),
    ],
)
def test_write_scope_survives_loader_manifest_and_hook_decoder(
    tmp_path: Path, declaration: str, expected: WriteScope
) -> None:
    info = _skill(tmp_path, declaration)
    assert not info.invalidities
    assert info.write_scope == expected
    assert info.frontmatter is not None
    assert info.frontmatter.write_scope == expected

    digest = hashlib.sha256(info.canonical_content.encode()).hexdigest()
    document = AgentSkillDocument(
        content=info.canonical_content,
        projected_digest=digest,
        canonical_digest=info.canonical_digest,
        source_identity=info.source_identity,
    )
    manifest = {"skills": _projection_skills_manifest((info,), {info.name: document})}
    assert manifest_skill_write_scope(manifest, info.name) == expected
