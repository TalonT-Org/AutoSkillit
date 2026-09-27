"""AST guard: every launch command applies the terminal log-level policy before launching.

Three closed inventories:

- the ``configure_logging`` call sites (only the policy applier and serve's two phases);
- the launch entries, each applying its ``TerminalLogPolicy`` as an unconditional
  top-level statement before its first prompt or launch sink;
- the launch-reaching function closure under ``cli/``, so a new launch path must be
  classified as an entry (with a policy) or as an intermediate.
"""

from __future__ import annotations

import ast
from functools import cache
from typing import NamedTuple

import pytest

from tests.arch._helpers import SRC_ROOT

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_POLICY_MODULE = "cli/ui/_terminal_logging.py"

_CONFIGURE_LOGGING_SITES = frozenset(
    {
        (_POLICY_MODULE, "apply_terminal_logging"),
        ("cli/app.py", "serve"),
    }
)


class _LaunchEntry(NamedTuple):
    policy: str
    policy_call: str
    sinks: frozenset[str]


_LAUNCH_ENTRIES: dict[tuple[str, str], _LaunchEntry] = {
    ("cli/app.py", "serve"): _LaunchEntry(
        "SERVICE", "resolve_terminal_logging", frozenset({"make_context"})
    ),
    ("cli/session/_session_cook.py", "cook"): _LaunchEntry(
        "INTERACTIVE", "apply_terminal_logging", frozenset({"plugin_launch_binding_scope"})
    ),
    ("cli/session/_session_order.py", "order"): _LaunchEntry(
        "INTERACTIVE",
        "apply_terminal_logging",
        frozenset({"_prepare_order_selection", "_prepare_order_launch", "_launch_cook_session"}),
    ),
    ("cli/fleet/__init__.py", "fleet_dispatch"): _LaunchEntry(
        "INTERACTIVE",
        "apply_terminal_logging",
        frozenset({"timed_prompt", "_launch_fleet_session"}),
    ),
    ("cli/fleet/__init__.py", "fleet_campaign"): _LaunchEntry(
        "INTERACTIVE",
        "apply_terminal_logging",
        frozenset({"_select_campaign", "_launch_fleet_session"}),
    ),
    ("cli/fleet/_fleet_run.py", "fleet_run"): _LaunchEntry(
        "HEADLESS", "apply_terminal_logging", frozenset({"_execute_fleet_run"})
    ),
}

_LAUNCH_INTERMEDIATES = frozenset(
    {
        ("cli/app.py", "_cook_cmd"),
        ("cli/session/_session_launch.py", "_run_interactive_session"),
        ("cli/session/_session_launch.py", "_run_cook_session_loop"),
        ("cli/session/_session_launch.py", "_launch_cook_session"),
        ("cli/fleet/_fleet_session.py", "_fleet_session_launcher"),
        ("cli/fleet/_fleet_session.py", "raw_launch"),
        ("cli/fleet/_fleet_session.py", "managed_launch"),
        ("cli/fleet/_fleet_session.py", "_launch_fleet_session"),
        ("cli/fleet/_fleet_run.py", "_execute_fleet_run"),
    }
)

_PRIMITIVE_LAUNCH_SINKS = frozenset(
    {"plugin_launch_binding_scope", "make_context", "execute_dispatch"}
)

_FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef


@cache
def _parse(rel: str) -> ast.Module:
    return ast.parse((SRC_ROOT / rel).read_text(encoding="utf-8"), filename=rel)


def _rel_paths(subdir: str = "") -> list[str]:
    root = SRC_ROOT / subdir if subdir else SRC_ROOT
    return sorted(path.relative_to(SRC_ROOT).as_posix() for path in root.rglob("*.py"))


def _call_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _functions(rel: str) -> dict[str, _FunctionNode]:
    return {
        node.name: node
        for node in ast.walk(_parse(rel))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


class _EnclosingCallCollector(ast.NodeVisitor):
    """Record the innermost enclosing function of every call to one name."""

    def __init__(self, callee: str) -> None:
        self._callee = callee
        self._stack: list[str] = []
        self.enclosing: list[str] = []

    def _visit_function(self, node: _FunctionNode) -> None:
        self._stack.append(node.name)
        self.generic_visit(node)
        self._stack.pop()

    visit_FunctionDef = _visit_function
    visit_AsyncFunctionDef = _visit_function

    def visit_Call(self, node: ast.Call) -> None:
        if _call_name(node) == self._callee:
            self.enclosing.append(self._stack[-1] if self._stack else "<module>")
        self.generic_visit(node)


def test_configure_logging_call_sites_are_closed() -> None:
    sites: set[tuple[str, str]] = set()
    for rel in _rel_paths():
        collector = _EnclosingCallCollector("configure_logging")
        collector.visit(_parse(rel))
        sites.update((rel, function) for function in collector.enclosing)
    assert sites == _CONFIGURE_LOGGING_SITES, (
        "configure_logging() may be called only by the terminal-logging policy applier "
        f"({_POLICY_MODULE}::apply_terminal_logging) and serve's two boot phases. "
        f"Unexpected: {sorted(sites - _CONFIGURE_LOGGING_SITES)}; "
        f"missing: {sorted(_CONFIGURE_LOGGING_SITES - sites)}"
    )


def test_logging_level_read_only_in_policy_module() -> None:
    offenders: list[str] = []
    for rel in _rel_paths("cli"):
        if rel == _POLICY_MODULE:
            continue
        for node in ast.walk(_parse(rel)):
            if (
                isinstance(node, ast.Attribute)
                and node.attr == "level"
                and isinstance(node.value, ast.Attribute)
                and node.value.attr == "logging"
            ):
                offenders.append(f"{rel}:{node.lineno}")
    assert not offenders, (
        f"Only {_POLICY_MODULE} may resolve logging.level into a terminal log level: {offenders}"
    )


def test_every_launch_entry_exists() -> None:
    missing = [
        f"{rel}::{name}"
        for rel, name in sorted(set(_LAUNCH_ENTRIES) | _LAUNCH_INTERMEDIATES)
        if name not in _functions(rel)
    ]
    assert not missing, f"Launch inventory names functions that no longer exist: {missing}"


def _is_policy_statement(stmt: ast.stmt, entry: _LaunchEntry) -> bool:
    if not isinstance(stmt, (ast.Expr, ast.Assign)) or not isinstance(stmt.value, ast.Call):
        return False
    call = stmt.value
    if _call_name(call) != entry.policy_call:
        return False
    return any(
        isinstance(arg, ast.Attribute)
        and arg.attr == entry.policy
        and isinstance(arg.value, ast.Name)
        and arg.value.id == "TerminalLogPolicy"
        for arg in [*call.args, *(keyword.value for keyword in call.keywords)]
    )


@pytest.mark.parametrize(
    ("rel", "name"), sorted(_LAUNCH_ENTRIES), ids=lambda part: str(part).replace("/", ".")
)
def test_launch_entries_apply_policy_before_first_sink(rel: str, name: str) -> None:
    entry = _LAUNCH_ENTRIES[(rel, name)]
    function = _functions(rel)[name]
    policy_lines = [stmt.lineno for stmt in function.body if _is_policy_statement(stmt, entry)]
    sink_lines = [
        node.lineno
        for node in ast.walk(function)
        if isinstance(node, ast.Call) and _call_name(node) in entry.sinks
    ]
    assert sink_lines, f"{rel}::{name} no longer calls any declared sink {sorted(entry.sinks)}"
    assert policy_lines, (
        f"{rel}::{name} must call {entry.policy_call}(..., TerminalLogPolicy.{entry.policy}) "
        "as an unconditional top-level statement of its body"
    )
    assert min(policy_lines) < min(sink_lines), (
        f"{rel}::{name} must apply TerminalLogPolicy.{entry.policy} before its first "
        f"prompt or launch sink (line {min(sink_lines)})"
    )


def _import_aliases(tree: ast.Module) -> dict[str, str]:
    return {
        alias.asname: alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
        if alias.asname is not None
    }


def _cli_call_graph() -> dict[tuple[str, str], set[str]]:
    graph: dict[tuple[str, str], set[str]] = {}
    for rel in _rel_paths("cli"):
        tree = _parse(rel)
        aliases = _import_aliases(tree)
        for function in ast.walk(tree):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            callees = graph.setdefault((rel, function.name), set())
            for node in ast.walk(function):
                if isinstance(node, ast.Call):
                    callee = _call_name(node)
                    if callee is not None:
                        callees.add(aliases.get(callee, callee))
    return graph


def test_launch_reaching_functions_are_classified() -> None:
    graph = _cli_call_graph()
    reaching = {key for key, callees in graph.items() if callees & _PRIMITIVE_LAUNCH_SINKS}
    while True:
        reaching_names = {name for _, name in reaching}
        grown = reaching | {key for key, callees in graph.items() if callees & reaching_names}
        if grown == reaching:
            break
        reaching = grown
    classified = set(_LAUNCH_ENTRIES) | _LAUNCH_INTERMEDIATES
    assert reaching == classified, (
        "The set of cli/ functions that reach a launch sink changed. Register a new "
        "top-level launch command in _LAUNCH_ENTRIES with its TerminalLogPolicy, or a "
        "new delegating helper in _LAUNCH_INTERMEDIATES. "
        f"Unclassified: {sorted(reaching - classified)}; stale: {sorted(classified - reaching)}"
    )
