"""Verify commands.py builders are thin forwarding shims."""

import ast
import inspect

import pytest

from autoskillit.execution import commands

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]

BUILDERS = [
    "build_interactive_cmd",
    "build_headless_cmd",
    "build_headless_resume_cmd",
]


class TestCommandsShimContract:
    @pytest.mark.parametrize("name", BUILDERS)
    def test_builder_body_is_thin(self, name: str):
        """Each builder body must have <= 3 statements (forwarding shim)."""
        source = inspect.getsource(getattr(commands, name))
        tree = ast.parse(source)
        func = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)
        body_stmts = [
            s
            for s in func.body
            if not isinstance(s, ast.Expr) or not isinstance(s.value, ast.Constant)
        ]
        assert len(body_stmts) <= 3, f"{name} has {len(body_stmts)} body statements, expected <= 3"

    # Names that indicate backend-dispatch: the builder resolves its backend
    # via ctx.backend or get_backend() instead of instantiating ClaudeCodeBackend directly.
    _BACKEND_DISPATCH_NAMES: frozenset[str] = frozenset({"backend", "ctx", "get_backend"})

    @pytest.mark.parametrize("name", BUILDERS)
    def test_builder_delegates_to_backend(self, name: str):
        """Each builder body must reference ClaudeCodeBackend or use backend dispatch."""
        source = inspect.getsource(getattr(commands, name))
        tree = ast.parse(source)
        func = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)
        ast_names = {
            node.id if isinstance(node, ast.Name) else node.attr
            for node in ast.walk(func)
            if isinstance(node, (ast.Name, ast.Attribute))
        }
        # Direct ClaudeCodeBackend() instantiation OR backend-dispatch (ctx.backend,
        # get_backend()) are both valid delegation patterns.
        assert "ClaudeCodeBackend" in ast_names or ast_names & self._BACKEND_DISPATCH_NAMES, (
            f"{name} does not delegate to ClaudeCodeBackend or use backend dispatch"
        )
