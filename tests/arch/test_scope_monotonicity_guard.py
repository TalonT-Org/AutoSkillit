"""The coverage-oracle scope accumulator may only grow, never shrink.

``build_test_scope`` in tests/_test_filter.py threads a ``ScopeAccumulator``
named ``scope`` through ``_add_reexport_cascades`` and ``_add_always_run_paths``.
The type has no subtractive method (see ``ScopeAccumulator``), but a future edit
could still shrink the effective scope by *rebinding* the name — a comprehension
rebuild, a walrus, a for/with/except target, or a fresh instance placed after the
single construction site. This guard classifies every site that touches ``scope``
within those three functions and fails closed on anything it cannot prove
additive: "not statically resolvable" maps to a violation, never to a pass.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_TEST_FILTER_PATH = Path(__file__).parent.parent / "_test_filter.py"

_SCOPE_NAME = "scope"
_ACCUMULATOR_CLASS = "ScopeAccumulator"
# Calls against `scope` that do not shrink the selection: the two additive
# adders, and the read-only final resolve.
_SAFE_METHODS = frozenset({"add_targets", "add_files", "resolve"})
# The only functions in tests/_test_filter.py that carry the accumulator today.
# A function newly touching `scope` must join this tuple deliberately —
# test_scope_carrying_function_inventory_is_complete enforces that.
_SCOPE_CARRYING_FUNCTIONS: tuple[str, ...] = (
    "_add_always_run_paths",
    "_add_reexport_cascades",
    "build_test_scope",
)


@dataclass(frozen=True)
class _ScopeSite:
    function: str
    lineno: int
    kind: str
    detail: str

    @property
    def violation(self) -> str | None:
        if self.kind in ("additive_call", "construction"):
            return None
        if self.kind == "subtractive_call":
            return f"scope.{self.detail}(...) is not an additive call"
        if self.kind == "unresolvable_call":
            return f"call against '{_SCOPE_NAME}' is not statically resolvable ({self.detail})"
        if self.kind == "rebind":
            return f"'{_SCOPE_NAME}' rebound outside its single construction site ({self.detail})"
        if self.kind == "string_rebind":
            return f"'{_SCOPE_NAME}' rebound via {self.detail}"
        return f"mutation of '{_SCOPE_NAME}' is not statically resolvable ({self.detail})"


def _is_bare_accumulator_construction(value: ast.expr | None) -> bool:
    return (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Name)
        and value.func.id == _ACCUMULATOR_CLASS
        and not value.args
        and not value.keywords
    )


def _mentions_scope(node: ast.AST | None) -> bool:
    if node is None:
        return False
    return any(
        isinstance(n, ast.Name) and n.id == _SCOPE_NAME and isinstance(n.ctx, ast.Load)
        for n in ast.walk(node)
    )


class _ScopeCollector(ast.NodeVisitor):
    """Classify every touch of `scope` within one function's body.

    Anchored on `ast.Name` with `ctx=Store` (and `ctx=Del`) rather than an
    enumeration of binding statement types. Assign, AnnAssign, AugAssign,
    NamedExpr (walrus), For/AsyncFor targets, and With/AsyncWith targets all
    surface uniformly as such a Name — a single `visit_Name` rule subsumes
    what a statement-type allowlist would otherwise need to re-enumerate one
    node kind at a time, and automatically covers any future grammar addition
    with the same shape. `visit_Assign` is kept only to track the RHS value
    for the single-construction-site check, not for detection.

    Two binding shapes are NOT `ast.Name` at all — bare strings — and are
    handled separately: `ast.ExceptHandler.name` and `ast.Global`/`ast.Nonlocal`
    names.

    Comprehension targets are deliberately excluded (own scope in Python 3, so
    an inner `scope` binding there cannot reach the enclosing name) via
    dedicated visitors that skip `generator.target` but still visit everything
    else in the comprehension.

    Not a general lexical-scope walker: none of the three functions this guard
    audits currently define a nested function, so descending into every nested
    block (but not tracking a nested def's own shadowing) is sufficient here.
    """

    def __init__(self, function_name: str, *, allow_construction: bool) -> None:
        self._function = function_name
        self._allow_construction = allow_construction
        self._construction_seen = False
        self._assign_value: ast.expr | None = None
        self.sites: list[_ScopeSite] = []

    def visit_Name(self, node: ast.Name) -> None:
        if node.id == _SCOPE_NAME:
            if isinstance(node.ctx, ast.Store):
                if (
                    self._assign_value is not None
                    and self._allow_construction
                    and not self._construction_seen
                    and _is_bare_accumulator_construction(self._assign_value)
                ):
                    self._construction_seen = True
                    self.sites.append(
                        _ScopeSite(self._function, node.lineno, "construction", "single site")
                    )
                else:
                    self.sites.append(_ScopeSite(self._function, node.lineno, "rebind", "binding"))
            elif isinstance(node.ctx, ast.Del):
                self.sites.append(
                    _ScopeSite(self._function, node.lineno, "rebind", "a del statement")
                )
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        previous = self._assign_value
        self._assign_value = node.value
        for target in node.targets:
            self.visit(target)
        self._assign_value = previous
        self.visit(node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        previous = self._assign_value
        self._assign_value = node.value
        self.visit(node.target)
        self._assign_value = previous
        if node.value is not None:
            self.visit(node.value)

    def _skip_comprehension_targets(self, node: ast.expr) -> None:
        for generator in node.generators:  # type: ignore[attr-defined]
            self.visit(generator.iter)
            for if_clause in generator.ifs:
                self.visit(if_clause)
        if isinstance(node, ast.DictComp):
            self.visit(node.key)
            self.visit(node.value)
        else:
            self.visit(node.elt)  # type: ignore[attr-defined]

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._skip_comprehension_targets(node)

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._skip_comprehension_targets(node)

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._skip_comprehension_targets(node)

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._skip_comprehension_targets(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == _SCOPE_NAME
        ):
            kind = "additive_call" if func.attr in _SAFE_METHODS else "subtractive_call"
            self.sites.append(_ScopeSite(self._function, node.lineno, kind, func.attr))
        elif _mentions_scope(node.func):
            self.sites.append(
                _ScopeSite(self._function, node.lineno, "unresolvable_call", "dynamic call target")
            )
        self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name == _SCOPE_NAME:
            self.sites.append(
                _ScopeSite(self._function, node.lineno, "string_rebind", "an except-as clause")
            )
        self.generic_visit(node)

    def visit_Global(self, node: ast.Global) -> None:
        if _SCOPE_NAME in node.names:
            self.sites.append(
                _ScopeSite(self._function, node.lineno, "string_rebind", "a global declaration")
            )
        self.generic_visit(node)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        if _SCOPE_NAME in node.names:
            self.sites.append(
                _ScopeSite(self._function, node.lineno, "string_rebind", "a nonlocal declaration")
            )
        self.generic_visit(node)


def _scan_tree(tree: ast.Module) -> tuple[_ScopeSite, ...]:
    sites: list[_ScopeSite] = []
    for function_name in _SCOPE_CARRYING_FUNCTIONS:
        target = next(
            (
                node
                for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == function_name
            ),
            None,
        )
        if target is None:
            continue
        collector = _ScopeCollector(
            function_name, allow_construction=function_name == "build_test_scope"
        )
        for statement in target.body:
            collector.visit(statement)
        sites.extend(collector.sites)
    return tuple(sites)


def _scan_source() -> tuple[_ScopeSite, ...]:
    return _scan_tree(ast.parse(_TEST_FILTER_PATH.read_text(encoding="utf-8")))


def _functions_mentioning_scope(tree: ast.Module) -> frozenset[str]:
    found: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        if any(arg.arg == _SCOPE_NAME for arg in node.args.args):
            found.add(node.name)
            continue
        if any(isinstance(n, ast.Name) and n.id == _SCOPE_NAME for n in ast.walk(node)):
            found.add(node.name)
    return frozenset(found)


def test_every_coverage_oracle_scope_mutation_is_additive() -> None:
    violations = [site for site in _scan_source() if site.violation is not None]
    details = "\n".join(
        f"  {_TEST_FILTER_PATH.name}:{site.lineno} ({site.function}): {site.violation}"
        for site in violations
    )
    assert not violations, f"Coverage-oracle scope mutations must be additive only:\n{details}"


def test_scope_carrying_function_inventory_is_complete() -> None:
    """A new function touching `scope` must consciously join the guarded inventory."""
    tree = ast.parse(_TEST_FILTER_PATH.read_text(encoding="utf-8"))
    assert _functions_mentioning_scope(tree) == frozenset(_SCOPE_CARRYING_FUNCTIONS)


def test_subtractive_scope_method_is_caught() -> None:
    """Canary: scope.discard(d) is the forbidden shape this guard exists for."""
    source = "def build_test_scope():\n    scope = ScopeAccumulator()\n    scope.discard(d)\n"
    sites = _scan_tree(ast.parse(source))
    assert [site.violation for site in sites] == [
        None,
        "scope.discard(...) is not an additive call",
    ]


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            "def build_test_scope():\n    scope = ScopeAccumulator()\n    scope -= other\n",
            id="augmented-assignment",
        ),
        pytest.param(
            "def build_test_scope():\n"
            "    scope = ScopeAccumulator()\n"
            "    scope = {d for d in scope if d}\n",
            id="comprehension-rebuild",
        ),
        pytest.param(
            "def build_test_scope():\n"
            "    scope = ScopeAccumulator()\n"
            "    scope.add_targets('arch')\n"
            "    scope = ScopeAccumulator()\n",
            id="rebind-to-fresh-instance-after-prior-additions",
        ),
        pytest.param(
            "def build_test_scope():\n    scope = ScopeAccumulator()\n    (scope := other())\n",
            id="walrus-assignment",
        ),
        pytest.param(
            "def build_test_scope():\n"
            "    scope = ScopeAccumulator()\n"
            "    for scope in candidates:\n"
            "        pass\n",
            id="for-loop-target",
        ),
        pytest.param(
            "def build_test_scope():\n"
            "    scope = ScopeAccumulator()\n"
            "    with acquire() as scope:\n"
            "        pass\n",
            id="with-statement-target",
        ),
        pytest.param(
            "def build_test_scope():\n"
            "    scope: ScopeAccumulator = ScopeAccumulator()\n"
            "    scope.add_targets('arch')\n"
            "    scope = ScopeAccumulator()\n",
            id="annotated-construction-rebound",
        ),
    ],
)
def test_scope_rebind_shapes_are_caught(source: str) -> None:
    sites = _scan_tree(ast.parse(source))
    violations = [site.violation for site in sites if site.violation is not None]
    assert violations, "this rebind shape must be caught as a violation"


def test_except_as_scope_rebind_is_caught() -> None:
    """Canary: `except ... as scope` is a bare-string binding, invisible to an ast.Name sweep."""
    source = (
        "def build_test_scope():\n"
        "    scope = ScopeAccumulator()\n"
        "    try:\n"
        "        pass\n"
        "    except LookupError as scope:\n"
        "        pass\n"
    )
    sites = _scan_tree(ast.parse(source))
    violations = [site.violation for site in sites if site.violation is not None]
    assert violations, "except ... as scope must be caught as a violation"


def test_unresolvable_scope_mutation_is_caught() -> None:
    """Canary: dynamic dispatch against `scope` must fail closed, not pass silently."""
    source = (
        "def build_test_scope():\n"
        "    scope = ScopeAccumulator()\n"
        "    getattr(scope, 'discard')(d)\n"
    )
    sites = _scan_tree(ast.parse(source))
    assert [site.violation for site in sites] == [
        None,
        "call against 'scope' is not statically resolvable (dynamic call target)",
    ]


def test_argument_level_set_difference_inside_additive_call_is_not_flagged() -> None:
    """Canary: classification is by receiver/method identity, not by auditing arguments.

    set.update(X) is monotone in X regardless of how X was computed.
    """
    source = (
        "def build_test_scope():\n"
        "    scope = ScopeAccumulator()\n"
        "    scope.add_targets(*(full_set - exclusions))\n"
    )
    sites = _scan_tree(ast.parse(source))
    assert [site.violation for site in sites] == [None, None]


def test_comprehension_target_named_scope_is_not_flagged() -> None:
    """Canary: a comprehension's own iteration variable is not a rebind.

    Comprehensions carry their own scope in Python 3, so `scope` as a
    comprehension target cannot reach the enclosing name.
    """
    source = (
        "def build_test_scope():\n"
        "    scope = ScopeAccumulator()\n"
        "    scope.add_targets(*(name for scope in groups for name in scope))\n"
    )
    sites = _scan_tree(ast.parse(source))
    assert [site.violation for site in sites] == [None, None]


def test_annotated_construction_is_not_flagged_as_rebind() -> None:
    """Annotated construction `scope: ScopeAccumulator = ScopeAccumulator()` is the same
    single-construction site as a plain Assign, and must register as construction rather
    than a rebind — visit_AnnAssign has to populate _assign_value before visiting the
    target.
    """
    source = (
        "def build_test_scope():\n"
        "    scope: ScopeAccumulator = ScopeAccumulator()\n"
        "    scope.add_targets('arch')\n"
    )
    sites = _scan_tree(ast.parse(source))
    assert [site.violation for site in sites] == [None, None]


def test_same_named_local_in_a_different_function_is_out_of_scope() -> None:
    """Canary: a `scope` local outside the guarded functions is not audited at all."""
    source = (
        "def build_test_scope():\n"
        "    scope = ScopeAccumulator()\n"
        "    scope.add_targets('arch')\n"
        "\n"
        "def _unrelated_function():\n"
        "    scope = get_settings_scope()\n"
        "    scope = rebind_it()\n"
    )
    sites = _scan_tree(ast.parse(source))
    assert [site.violation for site in sites] == [None, None]
