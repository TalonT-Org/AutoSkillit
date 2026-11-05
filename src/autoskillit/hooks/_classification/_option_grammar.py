"""Git parse-options engine over per-subcommand option tables.

Members: OptionArity, OptionDef, ParsedOption, ParsedArgv, parse_options,
last_option.

Models what a Git subcommand's argv means rather than which marker tokens it
contains: interleaved options and operands, ``--`` and the ``-`` operand,
bundled short options, stuck short values (``-Bx``), ``--opt=value``, unique
long-option prefixes and ``--no-`` negation. Abbreviations are always on
because the guard must over-approximate Git.

Tables map a canonical dashless name to an ``OptionDef``. A row that has a
long spelling is named by it; a short-only row is named by its letter.

``OptionArity`` is a ``StrEnum`` and ``OptionDef`` / ``ParsedArgv`` are
``NamedTuple``s. A module loaded under both its dotted and bare names yields
two classes, so compare members with ``==``, never ``is`` or ``isinstance``.

Stdlib-only leaf: imports nothing from the package.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import NamedTuple


class OptionArity(StrEnum):
    NONE = "none"
    REQUIRED = "required"
    # A value arrives only via ``--opt=v`` or a stuck ``-Xv``; the next token
    # is never consumed.
    OPTIONAL = "optional"


class OptionDef(NamedTuple):
    name: str
    short: str | None
    arity: OptionArity
    negatable: bool
    has_long: bool


class ParsedOption(NamedTuple):
    name: str
    value: str | None
    negated: bool


class ParsedArgv(NamedTuple):
    options: tuple[ParsedOption, ...]
    operands: tuple[str, ...]
    operands_before_dashdash: int | None
    # unknown_option | ambiguous_option | missing_value | unexpected_value
    unresolved: str | None


def _match_long(spelling: str, longs: Mapping[str, OptionDef]) -> tuple[OptionDef, bool] | str:
    """Return ``(row, negated)`` for *spelling*, or an ``unresolved`` reason."""
    candidates: list[tuple[OptionDef, bool, str]] = []
    for definition in longs.values():
        candidates.append((definition, False, definition.name))
        if definition.negatable:
            candidates.append((definition, True, f"no-{definition.name}"))
    for definition, negated, full in candidates:
        if full == spelling:
            return definition, negated
    matches = {(d.name, n): (d, n) for d, n, full in candidates if full.startswith(spelling)}
    if not spelling or not matches:
        return "unknown_option"
    if len(matches) > 1:
        return "ambiguous_option"
    return next(iter(matches.values()))


def _resolve_value(
    arity: OptionArity, inline: str | None, args: Sequence[str], index: int
) -> tuple[str | None, int, str | None]:
    """Value, next index and ``unresolved`` reason for one matched option."""
    if arity == OptionArity.NONE:
        return None, index, ("unexpected_value" if inline is not None else None)
    if inline is not None or arity == OptionArity.OPTIONAL:
        return inline, index, None
    if index < len(args):
        return args[index], index + 1, None
    return None, index, "missing_value"


def _consume_long(
    token: str,
    args: Sequence[str],
    index: int,
    longs: Mapping[str, OptionDef],
    options: list[ParsedOption],
) -> tuple[int, str | None]:
    spelling, has_value, value = token[2:].partition("=")
    matched = _match_long(spelling, longs)
    if isinstance(matched, str):
        return index, matched
    definition, negated = matched
    arity = OptionArity.NONE if negated else definition.arity
    resolved, index, unresolved = _resolve_value(arity, value if has_value else None, args, index)
    if unresolved is None:
        options.append(ParsedOption(definition.name, resolved, negated))
    return index, unresolved


def _consume_short_bundle(
    token: str,
    args: Sequence[str],
    index: int,
    shorts: Mapping[str, OptionDef],
    options: list[ParsedOption],
) -> tuple[int, str | None]:
    for position in range(1, len(token)):
        definition = shorts.get(token[position])
        if definition is None:
            return index, "unknown_option"
        if definition.arity == OptionArity.NONE:
            options.append(ParsedOption(definition.name, None, False))
            continue
        resolved, index, unresolved = _resolve_value(
            definition.arity, token[position + 1 :] or None, args, index
        )
        if unresolved is None:
            options.append(ParsedOption(definition.name, resolved, False))
        return index, unresolved
    return index, None


def parse_options(args: Sequence[str], table: Mapping[str, OptionDef]) -> ParsedArgv:
    longs = {name: definition for name, definition in table.items() if definition.has_long}
    shorts = {definition.short: definition for definition in table.values() if definition.short}
    options: list[ParsedOption] = []
    operands: list[str] = []
    before_dashdash: int | None = None
    unresolved: str | None = None
    index = 0
    while index < len(args) and unresolved is None:
        token = args[index]
        index += 1
        if token == "--":
            before_dashdash = len(operands)
            operands.extend(args[index:])
            break
        if token.startswith("--"):
            index, unresolved = _consume_long(token, args, index, longs, options)
        elif token.startswith("-") and len(token) > 1:
            index, unresolved = _consume_short_bundle(token, args, index, shorts, options)
        else:
            operands.append(token)
    return ParsedArgv(tuple(options), tuple(operands), before_dashdash, unresolved)


def last_option(parsed: ParsedArgv, name: str) -> ParsedOption | None:
    for option in reversed(parsed.options):
        if option.name == name:
            return option
    return None
