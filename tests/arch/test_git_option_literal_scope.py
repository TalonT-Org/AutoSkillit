"""Git-classification code reads option semantics from tables, not raw option tokens.

Members: _violations, test_git_classification_code_has_no_raw_option_token_tests and
the canary tests that prove both detectors fire.

Git, the shells and the wrappers accept bundled, stuck, ``=``-joined, abbreviated
and reordered spellings, so a membership test, index lookup or prefix test against
one literal spelling (``"-B" in args``, ``args.index("--force")``,
``token.startswith("--force")``) silently allows every other spelling. Option
semantics are decided through ``_option_grammar`` tables instead.

Detectors, over ``guards/git_ops_guard.py`` plus every ``hooks/_runtime/_git_*.py``
sibling, so a new Git-classification module is covered by naming it ``_git_*.py``
(the shell walk and the generic command classifier are out of scope by design):

(a) a ``Compare`` using ``in``/``not in``/``==``/``!=`` against a string literal that
    looks like an option (or a tuple/set/list literal containing one);
(b) a call to ``.index``/``.count``/``.startswith``/``.endswith``/``.removeprefix``/
    ``.partition`` whose first argument is such a literal.

The bare ``-``, ``--`` and ``+`` are exempt, as are dict-literal keys (table dispatch).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_HOOKS_ROOT = Path(__file__).resolve().parents[2] / "src" / "autoskillit" / "hooks"
_SCOPED_FILES = (
    _HOOKS_ROOT / "guards" / "git_ops_guard.py",
    *sorted((_HOOKS_ROOT / "_runtime").glob("_git_*.py")),
)
_OPTION_TOKEN_RE = re.compile(r"^[-+]{1,2}[A-Za-z0-9?]")
_TOKEN_METHODS = frozenset(
    {"index", "count", "startswith", "endswith", "removeprefix", "partition"}
)
_COMPARE_OPS = (ast.In, ast.NotIn, ast.Eq, ast.NotEq)


def _is_option_token(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and _OPTION_TOKEN_RE.match(node.value) is not None
    )


def _contains_option_token(node: ast.expr) -> bool:
    if isinstance(node, (ast.Tuple, ast.Set, ast.List)):
        return any(_is_option_token(element) for element in node.elts)
    return _is_option_token(node)


def _violations(source: str) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Compare) and any(isinstance(op, _COMPARE_OPS) for op in node.ops):
            if any(_contains_option_token(operand) for operand in [node.left, *node.comparators]):
                found.append((node.lineno, "comparison against a raw option token"))
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in _TOKEN_METHODS
            and node.args
            and _contains_option_token(node.args[0])
        ):
            found.append((node.lineno, f".{node.func.attr}() with a raw option token"))
    return found


@pytest.mark.parametrize("path", _SCOPED_FILES, ids=lambda path: path.name)
def test_git_classification_code_has_no_raw_option_token_tests(path: Path) -> None:
    violations = _violations(path.read_text(encoding="utf-8"))
    assert not violations, (
        f"{path.name} tests raw option spellings; decide option semantics through an "
        f"_option_grammar table instead:\n"
        + "\n".join(f"  line {line}: {reason}" for line, reason in violations)
    )


@pytest.mark.parametrize(
    "snippet",
    [
        'args.index("-B")',
        '"--force" in args',
        't.startswith("--force")',
        'token not in ("-f", "--force")',
        'args.count("-q")',
    ],
)
def test_detectors_fire_on_raw_option_token_tests(snippet: str) -> None:
    assert _violations(snippet)


@pytest.mark.parametrize(
    "snippet",
    [
        'token == "--"',
        'token == "-"',
        'refspec.startswith("+")',
        '{"--git-dir": 1}[key]',
        'verb in ("checkout", "branch")',
    ],
)
def test_detectors_ignore_exempt_forms(snippet: str) -> None:
    assert not _violations(snippet)
