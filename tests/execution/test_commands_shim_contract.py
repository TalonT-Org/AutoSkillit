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
    "build_skill_session_cmd",
    "build_food_truck_cmd",
]


class TestCommandsShimContract:
    @pytest.mark.parametrize("name", BUILDERS)
    def test_builder_body_is_thin(self, name: str):
        """Each builder body must have <= 3 statements (forwarding shim)."""
        source = inspect.getsource(getattr(commands, name))
        tree = ast.parse(source)
        func = tree.body[0]
        assert isinstance(func, ast.FunctionDef)
        body_stmts = [
            s
            for s in func.body
            if not isinstance(s, ast.Expr) or not isinstance(s.value, ast.Constant)
        ]
        assert len(body_stmts) <= 3, f"{name} has {len(body_stmts)} body statements, expected <= 3"

    @pytest.mark.parametrize("name", BUILDERS)
    def test_builder_delegates_to_backend(self, name: str):
        """Each builder body must reference ClaudeCodeBackend."""
        source = inspect.getsource(getattr(commands, name))
        assert "ClaudeCodeBackend" in source, f"{name} does not delegate to ClaudeCodeBackend"
