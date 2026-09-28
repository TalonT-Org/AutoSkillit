"""The typed write-scope authority: decode, encode, expansion, containment, and fold."""

from __future__ import annotations

import itertools
import os
from pathlib import Path

import pytest

from autoskillit.hooks._write_scope import (
    SessionScopeState,
    WriteScope,
    WriteScopeError,
    WriteScopeKind,
    bounded_scope_contains,
    decode_write_scope,
    encode_write_scope,
    expand_write_path,
    fold_session_write_scopes,
    temp_root_escape,
)

pytestmark = [pytest.mark.layer("infra"), pytest.mark.small]


def _bounded(*paths: str) -> WriteScope:
    return WriteScope(WriteScopeKind.BOUNDED, paths)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (["{{AUTOSKILLIT_TEMP}}/x/"], _bounded("{{AUTOSKILLIT_TEMP}}/x/")),
        ([".autoskillit/temp/x/"], _bounded(".autoskillit/temp/x/")),
        (
            ["{{AUTOSKILLIT_TEMP}}/a/", "{{AUTOSKILLIT_TEMP}}/b/"],
            _bounded("{{AUTOSKILLIT_TEMP}}/a/", "{{AUTOSKILLIT_TEMP}}/b/"),
        ),
        ("unrestricted", WriteScope(WriteScopeKind.UNRESTRICTED)),
        ("inherit", WriteScope(WriteScopeKind.INHERIT)),
    ],
)
def test_decode_accepts_the_three_kinds(raw: object, expected: WriteScope) -> None:
    assert decode_write_scope(raw) == expected
    assert encode_write_scope(decode_write_scope(raw)) == raw


@pytest.mark.parametrize(
    "raw",
    [
        None,
        [],
        "",
        "all",
        {},
        [None],
        [""],
        ["{{AUTOSKILLIT_TEMP}}/../etc/"],
        ["/abs/"],
        ["temp/x/"],
        "temp/x/",
        [3],
        ("{{AUTOSKILLIT_TEMP}}/x/",),
    ],
)
def test_decode_rejects_every_other_form(raw: object) -> None:
    with pytest.raises(WriteScopeError):
        decode_write_scope(raw)


def test_empty_list_rejection_steers_toward_inherit() -> None:
    with pytest.raises(WriteScopeError, match="use `inherit`"):
        decode_write_scope([])


@pytest.mark.parametrize(
    ("kind", "paths"),
    [
        (WriteScopeKind.BOUNDED, ()),
        (WriteScopeKind.UNRESTRICTED, ("{{AUTOSKILLIT_TEMP}}/x/",)),
        (WriteScopeKind.INHERIT, ("{{AUTOSKILLIT_TEMP}}/x/",)),
    ],
)
def test_write_scope_rejects_kind_path_mismatch(
    kind: WriteScopeKind, paths: tuple[str, ...]
) -> None:
    with pytest.raises(WriteScopeError, match="paths must be non-empty"):
        WriteScope(kind, paths)


def test_both_temp_spellings_expand_identically(tmp_path: Path) -> None:
    project = str(tmp_path)
    expected = os.path.join(project, ".autoskillit", "temp", "a") + "/"
    assert expand_write_path("{{AUTOSKILLIT_TEMP}}/a/", project) == expected
    assert expand_write_path(".autoskillit/temp/a/", project) == expected


def test_temp_root_escape_flags_a_declared_directory_symlinked_outside(tmp_path: Path) -> None:
    project = tmp_path / "project"
    temp = project / ".autoskillit" / "temp"
    temp.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (temp / "escape").symlink_to(outside, target_is_directory=True)

    cause = temp_root_escape(
        expand_write_path("{{AUTOSKILLIT_TEMP}}/escape/", str(project)), str(project)
    )

    assert cause is not None
    assert str(outside) in cause


def test_temp_root_escape_ignores_symlinks_inside_a_declared_directory(tmp_path: Path) -> None:
    project = tmp_path / "project"
    declared = project / ".autoskillit" / "temp" / "investigate"
    declared.mkdir(parents=True)
    (declared / "link").symlink_to(tmp_path, target_is_directory=True)

    assert temp_root_escape(str(declared) + "/", str(project)) is None


_A = ("a", _bounded("{{AUTOSKILLIT_TEMP}}/a/"))
_B = ("b", _bounded("{{AUTOSKILLIT_TEMP}}/b/"))
_INHERIT = ("quiet", WriteScope(WriteScopeKind.INHERIT))
_UNRESTRICTED = ("open", WriteScope(WriteScopeKind.UNRESTRICTED))


def test_fold_is_order_independent(tmp_path: Path) -> None:
    outcomes = {
        (boundary.state, frozenset(boundary.prefixes), boundary.unrestricted_by)
        for boundary in (
            fold_session_write_scopes(order, str(tmp_path))
            for order in itertools.permutations([_A, _B, _INHERIT, _UNRESTRICTED])
        )
    }
    assert len(outcomes) == 1
    ((state, _, unrestricted_by),) = outcomes
    assert state is SessionScopeState.UNRESTRICTED
    assert unrestricted_by == ("open",)


def test_fold_unions_bounded_scopes_and_records_contributors(tmp_path: Path) -> None:
    boundary = fold_session_write_scopes([_A, _INHERIT, _B], str(tmp_path))

    temp = os.path.join(str(tmp_path), ".autoskillit", "temp")
    assert boundary.state is SessionScopeState.BOUNDED
    assert set(boundary.prefixes) == {f"{temp}/a/", f"{temp}/b/"}
    assert boundary.contributors == (("a", (f"{temp}/a/",)), ("b", (f"{temp}/b/",)))
    assert boundary.unrestricted_by == ()


@pytest.mark.parametrize("scopes", [[], [_INHERIT], [_INHERIT, _INHERIT]])
def test_fold_without_bounded_or_unrestricted_is_none(
    tmp_path: Path, scopes: list[tuple[str, WriteScope]]
) -> None:
    assert fold_session_write_scopes(scopes, str(tmp_path)).state is SessionScopeState.NONE


@pytest.mark.parametrize(
    ("path", "contained"),
    [
        ("{{AUTOSKILLIT_TEMP}}/a/report.md", True),
        (".autoskillit/temp/a/nested/", True),
        ("{{AUTOSKILLIT_TEMP}}/b/report.md", False),
        ("{{AUTOSKILLIT_TEMP}}/a/../b/report.md", False),
        ("{{AUTOSKILLIT_TEMP}}/ab/report.md", False),
    ],
)
def test_bounded_scope_contains(tmp_path: Path, path: str, contained: bool) -> None:
    assert bounded_scope_contains(_A[1], path, str(tmp_path)) is contained


@pytest.mark.parametrize("scope", [_INHERIT[1], _UNRESTRICTED[1]])
def test_bounded_scope_contains_rejects_unbounded_scopes(
    tmp_path: Path, scope: WriteScope
) -> None:
    with pytest.raises(WriteScopeError):
        bounded_scope_contains(scope, "{{AUTOSKILLIT_TEMP}}/a/", str(tmp_path))
