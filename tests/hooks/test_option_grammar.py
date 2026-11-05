"""Unit matrix for the parse-options engine in hooks/_classification/_option_grammar.py.

Members: _TABLE (a checkout-like fixture table), _CASES, test_parse_options_matrix,
test_last_option_prefers_the_final_occurrence_and_keeps_negation,
test_exact_name_wins_over_longer_prefix_candidates.
"""

from __future__ import annotations

import pytest

from autoskillit.hooks._classification._option_grammar import (
    OptionArity,
    OptionDef,
    last_option,
    parse_options,
)

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.small]

_NONE = OptionArity.NONE
_REQUIRED = OptionArity.REQUIRED
_OPTIONAL = OptionArity.OPTIONAL


def _row(
    name: str,
    arity: OptionArity = _NONE,
    *,
    short: str | None = None,
    negatable: bool = False,
    has_long: bool = True,
) -> OptionDef:
    return OptionDef(name, short, arity, negatable, has_long)


def _table(*rows: OptionDef) -> dict[str, OptionDef]:
    return {row.name: row for row in rows}


# Short-only rows are named by their letter; rows with a long spelling by it.
_TABLE = _table(
    _row("b", _REQUIRED, short="b", has_long=False),
    _row("B", _REQUIRED, short="B", has_long=False),
    _row("orphan", _REQUIRED),
    _row("force", short="f"),
    _row("quiet", short="q"),
    _row("track", _OPTIONAL, short="t"),
    _row("detach", short="d", negatable=True),
    _row("guess", negatable=True),
    _row("overlay"),
)

_OptionTuple = tuple[str, str | None, bool]


def _o(name: str, value: str | None = None, negated: bool = False) -> _OptionTuple:
    return (name, value, negated)


def _case(
    argv: str,
    options: tuple[_OptionTuple, ...] = (),
    *,
    operands: tuple[str, ...] = (),
    before_dashdash: int | None = None,
    unresolved: str | None = None,
) -> object:
    return pytest.param(argv, options, operands, before_dashdash, unresolved, id=argv)


_CASES = [
    _case("-fB x", (_o("force"), _o("B", "x"))),
    _case("-Bx", (_o("B", "x"),)),
    _case("-B -q", (_o("B", "-q"),)),
    _case("-qqq", (_o("quiet"),) * 3),
    _case("--orphan=x", (_o("orphan", "x"),)),
    _case("--orphan x", (_o("orphan", "x"),)),
    _case("--orph x", (_o("orphan", "x"),)),
    _case("--o x", unresolved="ambiguous_option"),
    _case("--no-guess", (_o("guess", negated=True),)),
    _case("--no-gu", (_o("guess", negated=True),)),
    _case("--no-orphan", unresolved="unknown_option"),
    _case("-tb", (_o("track", "b"),)),
    _case("--track", (_o("track"),)),
    _case("--track=direct", (_o("track", "direct"),)),
    _case("--track direct", (_o("track"),), operands=("direct",)),
    _case("-- -b x", operands=("-b", "x"), before_dashdash=0),
    _case("a -b x -- -q", (_o("b", "x"),), operands=("a", "-q"), before_dashdash=1),
    _case("-", operands=("-",)),
    _case("--bogus", unresolved="unknown_option"),
    _case("-z", unresolved="unknown_option"),
    _case("-qz", (_o("quiet"),), unresolved="unknown_option"),
    _case("--b x", unresolved="unknown_option"),
    _case("-b", unresolved="missing_value"),
    _case("--orphan", unresolved="missing_value"),
    _case("--force=1", unresolved="unexpected_value"),
    _case("--no-detach=1", unresolved="unexpected_value"),
    _case("--detach --no-detach", (_o("detach"), _o("detach", negated=True))),
    _case("-B$X", (_o("B", "$X"),)),
    _case("$X", operands=("$X",)),
]


@pytest.mark.parametrize(("argv", "options", "operands", "before_dashdash", "unresolved"), _CASES)
def test_parse_options_matrix(
    argv: str,
    options: tuple[_OptionTuple, ...],
    operands: tuple[str, ...],
    before_dashdash: int | None,
    unresolved: str | None,
) -> None:
    parsed = parse_options(argv.split(), _TABLE)

    assert tuple((o.name, o.value, o.negated) for o in parsed.options) == options
    assert parsed.operands == operands
    assert parsed.operands_before_dashdash == before_dashdash
    assert parsed.unresolved == unresolved


def test_last_option_prefers_the_final_occurrence_and_keeps_negation() -> None:
    parsed = parse_options(["--detach", "--no-detach"], _TABLE)

    final = last_option(parsed, "detach")

    assert final is not None
    assert tuple(final) == _o("detach", negated=True)
    assert last_option(parsed, "guess") is None


def test_exact_name_wins_over_longer_prefix_candidates() -> None:
    table = _table(_row("force", short="f"), _row("force-create", _REQUIRED, short="C"))

    exact = parse_options(["--force"], table)
    prefixed = parse_options(["--force-c", "x"], table)

    assert [tuple(o) for o in exact.options] == [_o("force")]
    assert exact.unresolved is None
    assert [tuple(o) for o in prefixed.options] == [_o("force-create", "x")]
    assert prefixed.unresolved is None
