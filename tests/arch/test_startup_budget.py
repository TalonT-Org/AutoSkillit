"""Startup budget enforcement (REQ-STARTUP-001).

The serve() -> mcp.run() critical path must not contain subprocess calls.
Any subprocess on this path risks exceeding Claude Code's ~5s connection
timeout, causing "No such tool available" for all MCP tools.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "autoskillit"

FORBIDDEN_SUBPROCESS_CALLS = frozenset(
    {"subprocess.run", "subprocess.Popen", "subprocess.call", "subprocess.check_output"}
)


def _get_call_name(node: ast.Call) -> str:
    """Extract dotted call name from an ast.Call node."""
    if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
        return f"{node.func.value.id}.{node.func.attr}"
    if isinstance(node.func, ast.Name):
        return node.func.id
    return ""


def test_lifespan_calls_deferred_initialize() -> None:
    """Lifespan must wire deferred_initialize as a background task."""
    source = (SRC / "server" / "_lifespan.py").read_text()
    tree = ast.parse(source)

    lifespan_func = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_autoskillit_lifespan":
            lifespan_func = node
            break
    assert lifespan_func is not None, "_autoskillit_lifespan not found in _lifespan.py"

    # Walk the entire function body for any call to deferred_initialize
    found = False
    for node in ast.walk(lifespan_func):
        if isinstance(node, ast.Call):
            name = _get_call_name(node)
            if "deferred_initialize" in name:
                found = True
                break
            # Also check for Name nodes (direct call without module prefix)
            if isinstance(node.func, ast.Name) and node.func.id == "_run_deferred_init":
                found = True
                break
    assert found, (
        "_autoskillit_lifespan must call deferred_initialize (or _run_deferred_init) "
        "as a background task — deferred startup I/O is not wired into the lifespan"
    )


def test_no_subprocess_in_make_context() -> None:
    """REQ-STARTUP-001: make_context() must not eagerly call subprocess."""
    factory_src = (SRC / "server" / "_factory.py").read_text()
    tree = ast.parse(factory_src)

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "make_context":
            for call in _iter_eager_calls(node):
                call_name = _get_call_name(call)
                assert call_name not in FORBIDDEN_SUBPROCESS_CALLS, (
                    f"make_context() eagerly calls {call_name} at line {call.lineno} — "
                    f"this blocks the MCP server startup path"
                )
            break
    else:
        raise AssertionError("make_context() not found in _factory.py")


def _iter_eager_calls(func_node: ast.FunctionDef) -> list[ast.Call]:
    """Yield Call nodes that are eagerly executed in func_node.

    Skips calls inside nested lambdas, inner functions, and class bodies
    because those are deferred — not executed when the enclosing function runs.
    """
    eager_calls: list[ast.Call] = []

    class _EagerCallVisitor(ast.NodeVisitor):
        def visit_Lambda(self, node: ast.Lambda) -> None:
            pass  # skip lambda bodies — deferred execution

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            pass  # skip inner function bodies — deferred execution

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            pass  # skip async inner functions

        def visit_Call(self, node: ast.Call) -> None:
            eager_calls.append(node)
            self.generic_visit(node)

    # Visit direct children of each statement in the function body
    for stmt in func_node.body:
        _EagerCallVisitor().visit(stmt)

    return eager_calls


def test_no_calls_between_initialize_and_anyio_run() -> None:
    """REQ-STARTUP-001: serve() must not call anything between _initialize() and anyio.run()."""
    source = (SRC / "cli" / "app.py").read_text()
    tree = ast.parse(source)

    serve_func = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "serve":
            serve_func = node
            break
    assert serve_func is not None, "serve() not found in app.py"

    # Find the indices of _initialize(...) and anyio.run(...) in the body
    init_idx = None
    anyio_idx = None
    for i, stmt in enumerate(serve_func.body):
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            name = _get_call_name(stmt.value)
            if name == "_initialize":
                init_idx = i
        # anyio.run is wrapped in try/except — look for ast.Try containing anyio.run
        if isinstance(stmt, ast.Try):
            for try_stmt in stmt.body:
                if isinstance(try_stmt, ast.Expr) and isinstance(try_stmt.value, ast.Call):
                    name = _get_call_name(try_stmt.value)
                    if name == "anyio.run":
                        anyio_idx = i

    assert init_idx is not None, "_initialize() call not found in serve() body"
    assert anyio_idx is not None, "anyio.run() call not found in serve() body"
    assert init_idx < anyio_idx, "_initialize() must come before anyio.run()"

    # Check for function calls in statements between _initialize and anyio.run
    violations: list[str] = []
    for stmt in serve_func.body[init_idx + 1 : anyio_idx]:
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            name = _get_call_name(stmt.value)
            violations.append(f"{name}() at line {stmt.lineno}")

    assert not violations, (
        "serve() must not call anything between _initialize() and anyio.run() — "
        "found:\n" + "\n".join(f"  {v}" for v in violations)
    )


def test_no_gh_cli_token_in_make_context() -> None:
    """REQ-STARTUP-001: make_context() must not call _gh_cli_token() eagerly.

    The _gh_cli_token() function runs subprocess.run with a 5s timeout.
    Token resolution must be lazy (deferred to first gated tool call).
    Calls inside lambdas/closures are acceptable — they are deferred.
    """
    factory_src = (SRC / "server" / "_factory.py").read_text()
    tree = ast.parse(factory_src)

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "make_context":
            for call in _iter_eager_calls(node):
                call_name = _get_call_name(call)
                assert call_name != "_gh_cli_token", (
                    f"make_context() eagerly calls _gh_cli_token() at line {call.lineno} — "
                    f"this 5s subprocess blocks the MCP server startup path. "
                    f"Token resolution must be lazy (wrapped in a lambda or factory)."
                )
            break
    else:
        raise AssertionError("make_context() not found in _factory.py")
