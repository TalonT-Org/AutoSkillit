"""Architectural guards for dispatch-type-aware PTY allocation."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from tests.execution.conftest import _mock_backend

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


def _find_function(tree: ast.AST, func_name: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            return node
    return None


def _find_call(func_node: ast.AST, callee_name: str) -> ast.Call | None:
    for node in ast.walk(func_node):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == callee_name:
                return node
            if isinstance(node.func, ast.Attribute) and node.func.attr == callee_name:
                return node
    return None


class TestDispatchFoodTruckPtyOverrideGuard:
    def test_dispatch_food_truck_passes_pty_override_false(self) -> None:
        """dispatch_food_truck must call _execute_claude_headless with pty_override=False."""
        src = Path(__file__).parents[2] / "src/autoskillit/execution/headless/__init__.py"
        tree = ast.parse(src.read_text())

        dispatch_func = _find_function(tree, "dispatch_food_truck")
        assert dispatch_func is not None, "dispatch_food_truck not found"

        call = _find_call(dispatch_func, "_execute_claude_headless")
        assert call is not None, "_execute_claude_headless call not found in dispatch_food_truck"

        kw_names_values = {kw.arg: kw.value for kw in call.keywords if kw.arg is not None}
        assert "pty_override" in kw_names_values, (
            "dispatch_food_truck must pass pty_override= to _execute_claude_headless. "
            "Removing this guard causes food truck dispatches to use PTY (SIGTERM exit 143)."
        )
        val = kw_names_values["pty_override"]
        assert isinstance(val, ast.Constant) and val.value is False, (
            f"dispatch_food_truck must pass pty_override=False, got {ast.unparse(val)!r}"
        )


class TestAttemptContractNudgePtyOverrideGuard:
    def test_attempt_contract_nudge_accepts_pty_override(self) -> None:
        """_attempt_contract_nudge must have pty_override in its parameter list."""
        src = (
            Path(__file__).parents[2] / "src/autoskillit/execution/headless/_headless_recovery.py"
        )
        tree = ast.parse(src.read_text())

        func = _find_function(tree, "_attempt_contract_nudge")
        assert func is not None, "_attempt_contract_nudge not found"

        param_names = [arg.arg for arg in func.args.kwonlyargs]
        assert "pty_override" in param_names, (
            "_attempt_contract_nudge must accept pty_override= parameter. "
            "Without it, nudge paths in food truck dispatches reintroduce PTY allocation."
        )


class TestBoundaryPtyDispatchPaths:
    @pytest.mark.anyio
    async def test_food_truck_dispatch_uses_pty_false(self, minimal_ctx, tmp_path: Path) -> None:
        """dispatch_food_truck passes pty_mode=False to the subprocess runner."""
        from autoskillit.core.types import SubprocessResult, TerminationReason
        from autoskillit.core.types._type_plugin_source import DirectInstall
        from autoskillit.execution.backends.claude import ClaudeCodeBackend
        from autoskillit.execution.headless import DefaultHeadlessExecutor
        from tests.fakes import MockSubprocessRunner

        runner = MockSubprocessRunner()
        runner.set_default(
            SubprocessResult(
                returncode=0,
                stdout=json.dumps(
                    {
                        "type": "result",
                        "subtype": "success",
                        "result": "done %%FT_DONE%%",
                        "session_id": "ft-session",
                        "is_error": False,
                    }
                ),
                stderr="",
                termination=TerminationReason.NATURAL_EXIT,
                pid=55555,
            )
        )
        minimal_ctx.runner = runner
        minimal_ctx.plugin_source = DirectInstall(plugin_dir=tmp_path)
        minimal_ctx.backend = ClaudeCodeBackend()

        executor = DefaultHeadlessExecutor(minimal_ctx)
        await executor.dispatch_food_truck(
            "You are an L3 orchestrator",
            str(tmp_path),
            completion_marker="%%FT_DONE%%",
        )

        assert runner.last_pty_mode is False, (
            f"Food truck dispatch must use pty_mode=False (Claude Code has TUI mode), "
            f"got {runner.last_pty_mode!r}"
        )

    @pytest.mark.anyio
    async def test_run_headless_core_claude_code_uses_pty_true(
        self, minimal_ctx, tmp_path: Path
    ) -> None:
        """run_headless_core with Claude Code backend (no override) uses pty_mode=True."""
        import json

        from autoskillit.core import CmdSpec
        from autoskillit.core.types import SubprocessResult, TerminationReason
        from autoskillit.execution.headless._headless_execute import _execute_claude_headless
        from tests.fakes import MockSubprocessRunner

        runner = MockSubprocessRunner()
        runner.set_default(
            SubprocessResult(
                returncode=0,
                stdout=json.dumps(
                    {
                        "type": "result",
                        "subtype": "success",
                        "result": "done",
                        "session_id": "s1",
                        "is_error": False,
                    }
                ),
                stderr="",
                termination=TerminationReason.NATURAL_EXIT,
                pid=1,
            )
        )
        minimal_ctx.runner = runner
        backend = _mock_backend(pty_required=True)
        minimal_ctx.backend = backend

        spec = CmdSpec(cmd=("claude", "--print", "do something"), env={})
        await _execute_claude_headless(
            spec,
            cwd=str(tmp_path),
            ctx=minimal_ctx,
            timeout=10.0,
            stale_threshold=60.0,
            step_backend=backend,
        )

        assert runner.last_pty_mode is True, (
            f"run_headless_core with Claude Code backend (pty_required=True) must use "
            f"pty_mode=True, got {runner.last_pty_mode!r}"
        )
