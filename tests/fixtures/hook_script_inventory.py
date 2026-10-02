"""Live inventory of public hook scripts and their session-scope prologues."""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

from autoskillit.hook_registry import HOOKS_DIR


def _hook_scripts() -> Iterator[tuple[str, Path]]:
    """Yield public hook scripts, including a stray script with a prologue."""
    for path in HOOKS_DIR.rglob("*.py"):
        relative = path.relative_to(HOOKS_DIR).as_posix()
        if relative == "_dispatch.py" or relative.startswith("_runtime/"):
            continue
        if any(part.startswith("_") for part in Path(relative).parts):
            continue
        yield relative, path


def _literal_strings(node: ast.expr) -> frozenset[str] | None:
    if isinstance(node, (ast.Set, ast.List, ast.Tuple)):
        values = node.elts
    else:
        return None
    result: set[str] = set()
    for value in values:
        if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
            return None
        result.add(value.value)
    return frozenset(result)


def _literal_exempt_tiers(node: ast.expr) -> frozenset[str] | None:
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
        return None
    if node.func.id != "frozenset" or node.keywords:
        return None
    if not node.args:
        return frozenset()
    if len(node.args) != 1:
        return None
    return _literal_strings(node.args[0])


def _scope_prologue(path: Path) -> tuple[str, frozenset[str]] | None:
    """Read the literal scope call that must be the first statement in ``main``."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    mains = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "main"
    ]
    if not mains:
        return None
    assert len(mains) == 1, f"{path} declares more than one main()"
    main = mains[0]
    if not main.body:
        return None
    # The hook script may consume the scope via either API shape:
    #  - ``enforce_session_scope("headless_only")`` (develop's literal overload)
    #  - ``enforce_script_session_scope(__file__)`` (worktree's script-identity
    #    overload that resolves script -> scope via the generated table)
    # The call may also be wrapped in ``if not <call>(): sys.exit(0)`` —
    # descend into the If's test expression to find it. Recognize either by
    # parsing the first statement; if it's a Call whose function name matches
    # either helper, unwrap the relevant literal args.
    first_statement = main.body[0]
    if isinstance(first_statement, ast.If) and first_statement.test:
        unwrapped = first_statement.test
    elif isinstance(first_statement, ast.Expr):
        unwrapped = first_statement.value
    else:
        return None
    # Guard may also wrap the call in ``not <call>()`` — descend through Not.
    if isinstance(unwrapped, ast.UnaryOp) and isinstance(unwrapped.op, ast.Not):
        unwrapped = unwrapped.operand
    if not isinstance(unwrapped, ast.Call) or not isinstance(unwrapped.func, ast.Name):
        return None
    call = unwrapped
    if call.func.id not in {
        "enforce_session_scope",
        "enforce_script_session_scope",
    }:
        return None
    if call.func.id == "enforce_session_scope":
        assert len(call.args) == 1 and not any(
            keyword.arg not in {"exempt_tiers"} for keyword in call.keywords
        ), f"{path}: enforce_session_scope arguments must be literal scope declarations"
        scope = call.args[0]
        exempt_keywords = [keyword for keyword in call.keywords if keyword.arg == "exempt_tiers"]
    else:
        # enforce_script_session_scope(__file__) — no literal scope string;
        # the caller asserts the call exists, scope is implied by __file__ +
        # the registry mapping.
        assert len(call.args) == 1 and not call.keywords, (
            f"{path}: enforce_script_session_scope needs a single __file__ arg"
        )
        scope = ast.Constant(value="<identity>")
        exempt_keywords = []
    assert isinstance(scope, ast.Constant) and isinstance(scope.value, str), (
        f"{path}: session scope must be a string literal"
    )
    assert len(exempt_keywords) <= 1, f"{path}: duplicate exempt_tiers declaration"
    exempt_tiers = (
        frozenset() if not exempt_keywords else _literal_exempt_tiers(exempt_keywords[0].value)
    )
    assert exempt_tiers is not None, f"{path}: exempt_tiers must be a literal frozenset"
    return scope.value, exempt_tiers


def identity_scope_callers() -> tuple[str, ...]:
    """Return hooks-relative scripts whose prologue is ``enforce_script_session_scope``."""
    callers = tuple(
        sorted(
            relative
            for relative, path in _hook_scripts()
            if (prologue := _scope_prologue(path)) is not None and prologue[0] == "<identity>"
        )
    )
    assert callers, "no hook script uses the script-identity scope prologue"
    return callers
