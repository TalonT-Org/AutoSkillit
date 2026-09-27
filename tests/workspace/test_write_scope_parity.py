"""Interactive session folds and headless closure resolution admit the same directories."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from autoskillit.hooks._write_scope import (
    WriteScopeKind,
    decode_write_scope,
    encode_write_scope,
    fold_session_write_scopes,
)
from autoskillit.workspace.session_skills import compute_skill_closure, resolve_closure_write_dirs
from autoskillit.workspace.skills import DefaultSkillResolver, SkillInfo

pytestmark = [pytest.mark.layer("workspace"), pytest.mark.small]

_BUNDLED: dict[str, SkillInfo] = {info.name: info for info in DefaultSkillResolver().list_all()}


class _Catalog:
    def list_skills(self) -> list[SkillInfo]:
        return list(_BUNDLED.values())


def _interactive(members: list[SkillInfo], cwd: str) -> set[str]:
    scopes = []
    for member in members:
        assert member.write_scope is not None, member.name
        scopes.append((member.name, decode_write_scope(encode_write_scope(member.write_scope))))
    return {os.path.realpath(prefix) for prefix in fold_session_write_scopes(scopes, cwd).prefixes}


def _headless(members: list[SkillInfo], cwd: str) -> set[str]:
    return {os.path.realpath(path) for path in resolve_closure_write_dirs(tuple(members), cwd, [])}


_BOUNDED_NAMES = sorted(
    name
    for name, info in _BUNDLED.items()
    if info.write_scope is not None and info.write_scope.kind is WriteScopeKind.BOUNDED
)
_CLOSURE_ROOTS = sorted(name for name, info in _BUNDLED.items() if info.activate_deps)


@pytest.mark.parametrize("name", _BOUNDED_NAMES)
def test_single_bounded_skill_resolves_identically(name: str, tmp_path: Path) -> None:
    members = [_BUNDLED[name]]
    assert _interactive(members, str(tmp_path)) == _headless(members, str(tmp_path))


@pytest.mark.parametrize("name", _CLOSURE_ROOTS)
def test_activate_deps_closure_resolves_identically(name: str, tmp_path: Path) -> None:
    members = [_BUNDLED[member] for member in sorted(compute_skill_closure(name, _Catalog()))]
    assert _interactive(members, str(tmp_path)) == _headless(members, str(tmp_path))
