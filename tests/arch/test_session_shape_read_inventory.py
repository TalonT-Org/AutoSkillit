"""Keep each hook's registry scope identical to its shared prologue."""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest

from autoskillit.hook_registry import HOOK_REGISTRY, HOOKS_DIR

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


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
    first_statement = main.body[0]
    if not isinstance(first_statement, ast.Expr) or not isinstance(
        first_statement.value, ast.Call
    ):
        return None
    call = first_statement.value
    if not isinstance(call.func, ast.Name) or call.func.id != "enforce_session_scope":
        return None
    assert len(call.args) == 1 and not any(
        keyword.arg not in {"exempt_tiers"} for keyword in call.keywords
    ), f"{path}: enforce_session_scope arguments must be literal scope declarations"
    scope = call.args[0]
    assert isinstance(scope, ast.Constant) and isinstance(scope.value, str), (
        f"{path}: session scope must be a string literal"
    )
    exempt_keywords = [keyword for keyword in call.keywords if keyword.arg == "exempt_tiers"]
    assert len(exempt_keywords) <= 1, f"{path}: duplicate exempt_tiers declaration"
    exempt_tiers = (
        frozenset() if not exempt_keywords else _literal_exempt_tiers(exempt_keywords[0].value)
    )
    assert exempt_tiers is not None, f"{path}: exempt_tiers must be a literal frozenset"
    return scope.value, exempt_tiers


def test_script_scope_prologue_matches_registry() -> None:
    """Registry scope and first-statement hook prologue have one source of truth."""
    declared_by_script: dict[str, set[tuple[str, frozenset[str]]]] = {}
    for hookdef in HOOK_REGISTRY:
        declaration = (hookdef.session_scope, hookdef.exempt_session_types)
        for script in hookdef.scripts:
            declared_by_script.setdefault(script, set()).add(declaration)

    for script, path in _hook_scripts():
        prologue = _scope_prologue(path)
        declarations = declared_by_script.get(script, set())
        if prologue is not None:
            assert declarations, f"{script} declares a scope prologue but has no HookDef"
        if declarations:
            requires_prologue = any(
                scope != "any" or exempt_tiers for scope, exempt_tiers in declarations
            )
            assert not requires_prologue or prologue is not None, (
                f"{script} has a non-default HookDef scope but main() does not begin with "
                "enforce_session_scope(...)"
            )
            assert len(declarations) == 1, (
                f"{script} is listed by HookDefs with incompatible scope declarations: "
                f"{sorted(map(repr, declarations))}"
            )
            assert prologue is None or prologue == next(iter(declarations)), (
                f"{script} registry declaration {next(iter(declarations))!r} does not match "
                f"its main() scope prologue {prologue!r}"
            )

    known_scripts = {script for script, _path in _hook_scripts()}
    unknown = set(declared_by_script) - known_scripts
    assert not unknown, f"HookDef scripts must be public hook scripts: {sorted(unknown)}"
