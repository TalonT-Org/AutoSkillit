"""Every executable hook flock acquisition is statically non-blocking.

Hook processes run on agent-control paths where a kernel-blocking lock can
turn one contended file into an unbounded hung command. This guard scans the
entire hooks tree, inventories every acquisition, and fails closed when it
cannot resolve an operation expression to ``fcntl.LOCK_*`` flags. Unlocks
are deliberately outside the rule: they release an already-held lock rather
than acquire one.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from dataclasses import dataclass

import pytest

from tests.arch._helpers import SRC_ROOT

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_HOOKS_ROOT = SRC_ROOT / "hooks"
_LOCK_UN = frozenset({"LOCK_UN"})
_EXPECTED_ACQUISITIONS = (
    ("_capture/_resolver.py", "_acquire_shared_lease"),
    ("_capture/_resolver.py", "acquire_writer_lease"),
    ("_capture_lifecycle/_admission.py", "_acquire_flock"),
    ("_capture_lifecycle/_admission.py", "_acquire_flock"),
    ("_capture_lifecycle/_store.py", "_try_artifact_lease"),
    ("_join_ledger.py", "_acquire_lock"),
    ("guards/open_kitchen_guard.py", "_acquire_registry_lock"),
    ("resume_gate_post_hook.py", "_acquire_lock"),
)


@dataclass(frozen=True)
class _FlockSite:
    path: str
    function: str
    lineno: int
    flags: frozenset[str] | None

    @property
    def violation(self) -> str | None:
        if self.flags is None:
            return "operation expression is not statically resolvable"
        if self.flags == _LOCK_UN:
            return None
        if "LOCK_NB" not in self.flags:
            return "operation does not include fcntl.LOCK_NB"
        return None


def _is_fcntl_flock_call(node: ast.Call) -> bool:
    return (
        isinstance(node.func, ast.Attribute)
        and node.func.attr == "flock"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "fcntl"
    )


def _operation_argument(node: ast.Call) -> ast.expr | None:
    if len(node.args) >= 2:
        return node.args[1]
    return next((keyword.value for keyword in node.keywords if keyword.arg == "operation"), None)


def _direct_assignments(body: Iterable[ast.stmt]) -> dict[str, ast.expr | None]:
    """Map directly assigned local names, marking duplicates unresolved."""
    assignments: dict[str, ast.expr | None] = {}
    for statement in body:
        if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
            continue
        value = statement.value
        targets = statement.targets if isinstance(statement, ast.Assign) else (statement.target,)
        for target in targets:
            if not isinstance(target, ast.Name):
                continue
            assignments[target.id] = None if target.id in assignments else value
    return assignments


def _static_lock_flags(
    expression: ast.expr | None,
    assignments: dict[str, ast.expr | None],
    seen_names: frozenset[str] = frozenset(),
) -> frozenset[str] | None:
    if (
        isinstance(expression, ast.Attribute)
        and isinstance(expression.value, ast.Name)
        and expression.value.id == "fcntl"
        and expression.attr.startswith("LOCK_")
    ):
        return frozenset({expression.attr})
    if isinstance(expression, ast.BinOp) and isinstance(expression.op, ast.BitOr):
        left = _static_lock_flags(expression.left, assignments, seen_names)
        right = _static_lock_flags(expression.right, assignments, seen_names)
        return None if left is None or right is None else left | right
    if isinstance(expression, ast.Name) and expression.id not in seen_names:
        assigned = assignments.get(expression.id)
        if assigned is not None:
            return _static_lock_flags(assigned, assignments, seen_names | {expression.id})
    return None


class _FlockCollector(ast.NodeVisitor):
    def __init__(self, path: str) -> None:
        self._path = path
        self._functions: list[str] = []
        self._assignments: list[dict[str, ast.expr | None]] = [{}]
        self.sites: list[_FlockSite] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self._functions.append(node.name)
        self._assignments.append(_direct_assignments(node.body))
        self.generic_visit(node)
        self._assignments.pop()
        self._functions.pop()

    def visit_Call(self, node: ast.Call) -> None:
        if _is_fcntl_flock_call(node):
            self.sites.append(
                _FlockSite(
                    path=self._path,
                    function=".".join(self._functions) or "<module>",
                    lineno=node.lineno,
                    flags=_static_lock_flags(_operation_argument(node), self._assignments[-1]),
                )
            )
        self.generic_visit(node)


def _scan_tree(tree: ast.AST, path: str) -> tuple[_FlockSite, ...]:
    collector = _FlockCollector(path)
    collector.visit(tree)
    return tuple(collector.sites)


def _scan_hooks() -> tuple[_FlockSite, ...]:
    sites: list[_FlockSite] = []
    for source_path in sorted(_HOOKS_ROOT.rglob("*.py")):
        relative_path = source_path.relative_to(_HOOKS_ROOT).as_posix()
        sites.extend(_scan_tree(ast.parse(source_path.read_text(encoding="utf-8")), relative_path))
    return tuple(sites)


def test_every_hook_flock_acquisition_uses_lock_nb() -> None:
    violations = [site for site in _scan_hooks() if site.violation is not None]
    details = "\n".join(
        f"  {site.path}:{site.lineno} ({site.function}): {site.violation}" for site in violations
    )
    assert not violations, f"Hook flock acquisitions must be statically non-blocking:\n{details}"


def test_hook_flock_acquisition_inventory_is_complete() -> None:
    """New acquisitions must consciously join the bounded-lock inventory."""
    observed = sorted(
        (site.path, site.function) for site in _scan_hooks() if site.flags != _LOCK_UN
    )
    assert observed == sorted(_EXPECTED_ACQUISITIONS)


def test_unresolvable_flock_operation_is_caught() -> None:
    """Canary: an operation passed through an unknown name must fail closed."""
    sites = _scan_tree(
        ast.parse("def write(fd, operation):\n    fcntl.flock(fd, operation)\n"), "synthetic.py"
    )
    assert [site.violation for site in sites] == [
        "operation expression is not statically resolvable"
    ]


def test_reverting_to_bare_lock_ex_is_caught() -> None:
    """Canary: the exact bare-LOCK_EX regression remains detectable."""
    sites = _scan_tree(
        ast.parse("def write(fd):\n    fcntl.flock(fd, fcntl.LOCK_EX)\n"), "synthetic.py"
    )
    assert [site.violation for site in sites] == ["operation does not include fcntl.LOCK_NB"]


def test_nonblocking_flock_operation_is_not_flagged() -> None:
    """Canary: a statically explicit non-blocking acquisition remains valid."""
    sites = _scan_tree(
        ast.parse("def write(fd):\n    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"),
        "synthetic.py",
    )
    assert [site.violation for site in sites] == [None]
