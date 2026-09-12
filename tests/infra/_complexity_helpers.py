"""Shared test infra for scripts/check_complexity.py's behavioral, e2e, and ruff-parity suites.

Used by tests/infra/test_check_complexity.py, tests/infra/test_check_complexity_git_e2e.py,
tests/infra/test_check_complexity_ruff_parity.py, and tests/arch/test_complexity_limits.py.
Each caller keeps its own _CHECK_MODULE_NAME / module-loading call site (per-file
sys.modules isolation stays a parameter, not shared state) -- only the byte-identical
bodies live here, matching the tests/infra/_pretty_output_helpers.py convention of a
dedicated underscore-prefixed helper module for one feature's split test files.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import textwrap
from pathlib import Path

_CHECK_COMPLEXITY_GIT_TIMEOUT_SECONDS = 30


def load_check_script(name: str, path: Path):
    """Load one gate script fresh via importlib, registering it in sys.modules under *name*.

    Each caller passes its own unique *name* so dataclasses defined in the loaded script
    resolve string annotations through sys.modules[cls.__module__] without colliding with a
    sibling test file's copy of the same script (see
    tests/arch/test_acceptance_policy_relaxation_gate.py).
    """
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(repo),
        check=True,
        capture_output=True,
        text=True,
        timeout=_CHECK_COMPLEXITY_GIT_TIMEOUT_SECONDS,
    )


def _source_with_function(name: str, complexity: int) -> str:
    """A function with exactly *complexity*, via (complexity - 1) sibling `if` guards."""
    lines = [f"def {name}():"]
    if complexity <= 1:
        lines.append("    pass")
    else:
        for i in range(complexity - 1):
            lines.append(f"    if x{i}:")
            lines.append("        pass")
    return "\n".join(lines) + "\n"


_MINIMAL_LIMITS = "MAX_COMPLEXITY = 10\nMIN_RATIONALE_CHARS = 60\nCOMPLEXITY_EXEMPTIONS = {}\n"


def _snippet(source: str) -> str:
    return textwrap.dedent(source).strip("\n") + "\n"


# One entry per branch-type construct the counter must recognize; shared so
# test_check_complexity_ruff_parity.py's ruff-parity fixture stays in lockstep with
# test_check_complexity.py's own parametrized counter tests instead of drifting out of sync.
_CONSTRUCT_CASES = [
    ("plain", _snippet("def f():\n    pass\n"), 1),
    ("if", _snippet("def f():\n    if a:\n        pass\n"), 2),
    (
        "if_elif_else",
        _snippet(
            """
            def f():
                if a:
                    pass
                elif b:
                    pass
                else:
                    pass
            """
        ),
        3,
    ),
    (
        "for_else",
        _snippet("def f():\n    for x in y:\n        pass\n    else:\n        pass\n"),
        2,
    ),
    (
        "while_else",
        _snippet("def f():\n    while a:\n        pass\n    else:\n        pass\n"),
        2,
    ),
    (
        "try_2_handlers",
        _snippet(
            """
            def f():
                try:
                    pass
                except A:
                    pass
                except B:
                    pass
            """
        ),
        3,
    ),
    (
        "try_except_else",
        _snippet(
            """
            def f():
                try:
                    pass
                except A:
                    pass
                else:
                    pass
            """
        ),
        3,
    ),
    ("try_finally", _snippet("def f():\n    try:\n        pass\n    finally:\n        pass\n"), 1),
    ("with", _snippet("def f():\n    with a:\n        pass\n"), 1),
    (
        "match_2_literal_plus_wildcard",
        _snippet(
            """
            def f():
                match a:
                    case 1:
                        pass
                    case 2:
                        pass
                    case _:
                        pass
            """
        ),
        3,
    ),
    (
        "match_1_literal_plus_name",
        _snippet(
            """
            def f():
                match a:
                    case 1:
                        pass
                    case x:
                        pass
            """
        ),
        2,
    ),
    (
        "match_3_literal",
        _snippet(
            """
            def f():
                match a:
                    case 1:
                        pass
                    case 2:
                        pass
                    case 3:
                        pass
            """
        ),
        4,
    ),
    (
        "match_guarded_wildcard_last",
        _snippet(
            """
            def f():
                match a:
                    case 1:
                        pass
                    case _ if b:
                        pass
            """
        ),
        3,
    ),
    (
        "match_or_irrefutable_last",
        _snippet(
            """
            def f():
                match a:
                    case 1:
                        pass
                    case 2 | _:
                        pass
            """
        ),
        2,
    ),
    (
        "match_sequence_as_last",
        _snippet(
            """
            def f():
                match a:
                    case 1:
                        pass
                    case [x, y] as w:
                        pass
            """
        ),
        3,
    ),
    (
        "nested_def_with_if",
        _snippet("def f():\n    def g():\n        if a:\n            pass\n"),
        3,
    ),
    ("and_or", _snippet("def f():\n    x = a and b or c\n"), 1),
    ("ternary", _snippet("def f():\n    x = a if b else c\n"), 1),
    ("comprehension_with_filter", _snippet("def f():\n    x = [i for i in y if i]\n"), 1),
    ("assert_stmt", _snippet("def f():\n    assert a\n"), 1),
    ("async_for", _snippet("async def f():\n    async for x in y:\n        pass\n"), 2),
    ("async_with", _snippet("async def f():\n    async with a:\n        pass\n"), 1),
    (
        "class_in_function_method_with_if",
        _snippet(
            """
            def f():
                class C:
                    def m(self):
                        if a:
                            pass
            """
        ),
        3,
    ),
    (
        "except_star_x2",
        _snippet(
            """
            def f():
                try:
                    pass
                except* A:
                    pass
                except* B:
                    pass
            """
        ),
        3,
    ),
    (
        "if_nested_inside_else",
        _snippet(
            """
            def f():
                if a:
                    pass
                else:
                    if b:
                        pass
            """
        ),
        3,
    ),
    (
        "with_body_with_if",
        _snippet("def f():\n    with a:\n        if b:\n            pass\n"),
        2,
    ),
    (
        "try_finally_with_if_in_finally",
        _snippet(
            """
            def f():
                try:
                    pass
                finally:
                    if a:
                        pass
            """
        ),
        2,
    ),
]
