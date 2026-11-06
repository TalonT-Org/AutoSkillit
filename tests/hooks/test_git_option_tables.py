"""Convention check for the option tables in hooks/_runtime/_git_option_tables.py.

Members: _spellings, _assert_row_spellings_parse and one parametrized test per
table: _GIT_CHECKOUT_FLAG_SPEC, _GIT_SWITCH_FLAG_SPEC, _GIT_BRANCH_FLAG_SPEC,
_GIT_SYMBOLIC_REF_FLAG_SPEC, _GIT_UPDATE_REF_FLAG_SPEC, _GIT_RESET_FLAG_SPEC,
_GIT_PUSH_FLAG_SPEC, _GIT_FETCH_FLAG_SPEC, _GIT_COMMIT_FLAG_SPEC,
_GIT_CLEAN_FLAG_SPEC and _SED_FLAG_SPEC.

Each parametrize iterates its table's own keys, so a new row is covered without
a test edit: every long, short and ``--no-`` spelling a row declares must parse
back to that row with no ``unresolved`` reason. Tables are imported by bare name
because tests/arch/test_guard_flag_spec_coverage.py looks for the table name
inside the parametrize call.
"""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from autoskillit.hooks._classification._option_grammar import (
    OptionArity,
    OptionDef,
    parse_options,
)
from autoskillit.hooks._runtime._git_option_tables import (
    _GIT_BRANCH_FLAG_SPEC,
    _GIT_CHECKOUT_FLAG_SPEC,
    _GIT_CLEAN_FLAG_SPEC,
    _GIT_COMMIT_FLAG_SPEC,
    _GIT_FETCH_FLAG_SPEC,
    _GIT_PUSH_FLAG_SPEC,
    _GIT_RESET_FLAG_SPEC,
    _GIT_SWITCH_FLAG_SPEC,
    _GIT_SYMBOLIC_REF_FLAG_SPEC,
    _GIT_UPDATE_REF_FLAG_SPEC,
    _SED_FLAG_SPEC,
)

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.small]

_VALUE = "VALUE"


def _spellings(row: OptionDef) -> list[tuple[list[str], str | None, bool]]:
    """Every argv that must parse to *row*, with the value and negation it carries."""
    spellings: list[tuple[list[str], str | None, bool]] = []
    if row.has_long:
        long = f"--{row.name}"
        if row.arity == OptionArity.NONE:
            spellings.append(([long], None, False))
        else:
            spellings.append(([f"{long}={_VALUE}"], _VALUE, False))
            if row.arity == OptionArity.REQUIRED:
                spellings.append(([long, _VALUE], _VALUE, False))
            else:
                spellings.append(([long], None, False))
        if row.negatable:
            spellings.append(([f"--no-{row.name}"], None, True))
    if row.short:
        short = f"-{row.short}"
        if row.arity == OptionArity.NONE:
            spellings.append(([short], None, False))
        else:
            spellings.append(([f"{short}{_VALUE}"], _VALUE, False))
            if row.arity == OptionArity.REQUIRED:
                spellings.append(([short, _VALUE], _VALUE, False))
            else:
                spellings.append(([short], None, False))
    return spellings


def _assert_row_spellings_parse(table: Mapping[str, OptionDef], name: str) -> None:
    row = table[name]
    assert row.name == name
    assert not name.startswith("-")
    # A short-only row is named by its letter.
    assert row.has_long or row.short == name
    spellings = _spellings(row)
    assert spellings, f"{name!r} declares no spelling"
    for argv, value, negated in spellings:
        parsed = parse_options(argv, table)
        assert parsed.unresolved is None, f"{argv}: {parsed.unresolved}"
        assert [tuple(option) for option in parsed.options] == [(name, value, negated)], argv
        assert parsed.operands == (), argv


@pytest.mark.parametrize("name", sorted(_GIT_CHECKOUT_FLAG_SPEC))
def test_checkout_row_spellings_parse(name: str) -> None:
    _assert_row_spellings_parse(_GIT_CHECKOUT_FLAG_SPEC, name)


@pytest.mark.parametrize("name", sorted(_GIT_SWITCH_FLAG_SPEC))
def test_switch_row_spellings_parse(name: str) -> None:
    _assert_row_spellings_parse(_GIT_SWITCH_FLAG_SPEC, name)


@pytest.mark.parametrize("name", sorted(_GIT_BRANCH_FLAG_SPEC))
def test_branch_row_spellings_parse(name: str) -> None:
    _assert_row_spellings_parse(_GIT_BRANCH_FLAG_SPEC, name)


@pytest.mark.parametrize("name", sorted(_GIT_SYMBOLIC_REF_FLAG_SPEC))
def test_symbolic_ref_row_spellings_parse(name: str) -> None:
    _assert_row_spellings_parse(_GIT_SYMBOLIC_REF_FLAG_SPEC, name)


@pytest.mark.parametrize("name", sorted(_GIT_UPDATE_REF_FLAG_SPEC))
def test_update_ref_row_spellings_parse(name: str) -> None:
    _assert_row_spellings_parse(_GIT_UPDATE_REF_FLAG_SPEC, name)


@pytest.mark.parametrize("name", sorted(_GIT_RESET_FLAG_SPEC))
def test_reset_row_spellings_parse(name: str) -> None:
    _assert_row_spellings_parse(_GIT_RESET_FLAG_SPEC, name)


@pytest.mark.parametrize("name", sorted(_GIT_PUSH_FLAG_SPEC))
def test_push_row_spellings_parse(name: str) -> None:
    _assert_row_spellings_parse(_GIT_PUSH_FLAG_SPEC, name)


@pytest.mark.parametrize("name", sorted(_GIT_FETCH_FLAG_SPEC))
def test_fetch_row_spellings_parse(name: str) -> None:
    _assert_row_spellings_parse(_GIT_FETCH_FLAG_SPEC, name)


@pytest.mark.parametrize("name", sorted(_GIT_COMMIT_FLAG_SPEC))
def test_commit_row_spellings_parse(name: str) -> None:
    _assert_row_spellings_parse(_GIT_COMMIT_FLAG_SPEC, name)


@pytest.mark.parametrize("name", sorted(_GIT_CLEAN_FLAG_SPEC))
def test_clean_row_spellings_parse(name: str) -> None:
    _assert_row_spellings_parse(_GIT_CLEAN_FLAG_SPEC, name)


@pytest.mark.parametrize("name", sorted(_SED_FLAG_SPEC))
def test_sed_row_spellings_parse(name: str) -> None:
    _assert_row_spellings_parse(_SED_FLAG_SPEC, name)
