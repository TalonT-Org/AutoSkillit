"""Per-flag arity classification shared by _flags.py and _interpreters.py.

Extracted from _flags.py (PR #4983 review) to break a bidirectional
bottom-bootstrap import that PR #4941 Part A/B introduced between _flags.py
(needing all_evaluated_segments/live_command_text from _interpreters.py) and
_interpreters.py (needing _FlagArity from _flags.py). Both siblings now
import this dependency-free leaf directly at the top of their files, leaving
only the _flags.py -> _interpreters.py edge -- the direction hooks/AGENTS.md's
classification-authority invariant actually sanctions.
"""

from __future__ import annotations

from enum import StrEnum, auto


class _FlagArity(StrEnum):
    """Per-flag arity classification used by every {flag: arity} spec table.

    BOOLEAN — the flag takes no value; the next token is its own argument.
    VALUE — the flag takes exactly one value in the next token (or joined via
    `=` for long forms, or glued onto a short form like -XPOST).

    A StrEnum, not a plain Enum: this module can be loaded under the dotted
    `autoskillit.hooks._classification._flag_arity` package name and the bare
    `_classification._flag_arity` standalone name. The two loads produce
    distinct `_FlagArity` class objects, so an `is`
    comparison between a value sourced from one and `_FlagArity.VALUE`
    sourced from the other silently fails even though both represent the
    same arity. StrEnum members compare equal by their underlying str value
    across class identities (`A.VALUE == B.VALUE` is True even when `A is
    not B`), so every comparison against `_FlagArity.VALUE`/`.BOOLEAN`
    anywhere in the codebase must use `==`, never `is`.
    """

    BOOLEAN = auto()
    VALUE = auto()
