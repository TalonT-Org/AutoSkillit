"""Bytecode-suppression guard: every hook interpreter invocation passes ``-B``.

Hook scripts execute against a shared, sometimes read-only or version-skewed
plugin tree (relocatable plugin cache, machine-local dev checkout). Writing
``.pyc`` files there races concurrent sessions and can leave stale bytecode
behind a pivoted install. Every renderer that builds a Python interpreter
command line for a hook — Claude Code's relocatable and machine-local forms,
Codex's config.toml form, the stable dispatcher's own subprocess spawn, and
the Codex-only shell-capture runner spawn — must pass ``-B`` (and, for the
dispatcher's child process, set ``PYTHONDONTWRITEBYTECODE=1`` in the child
environment as a belt-and-suspenders backstop).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from autoskillit.execution.backends._codex_hooks import _build_codex_hook_command
from autoskillit.hook_registry import _build_hook_command, render_relocatable_hook_command
from autoskillit.hooks._capture_contract import CAPTURE_REQUEST_PROTOCOL_VERSION, CaptureRequest
from autoskillit.hooks.shell_capture_hook import _render_harness, _runner_argv

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.small]


class TestRenderRelocatableHookCommand:
    def test_includes_bytecode_suppression_flag(self) -> None:
        command = render_relocatable_hook_command("guards/quota_guard")
        assert "python3 -B" in command


class TestBuildHookCommandNonRelocatable:
    def test_includes_bytecode_suppression_flag(self, tmp_path: Path) -> None:
        cmd = _build_hook_command(tmp_path, "guards/quota_guard.py", None, relocatable=False)
        assert "python3 -B" in cmd["command"]


class TestBuildCodexHookCommand:
    def test_includes_bytecode_suppression_flag(self, tmp_path: Path) -> None:
        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        # _build_codex_hook_command hashes the dispatcher's bytes for trusted_hash,
        # so a real file (content is otherwise irrelevant here) is required.
        (hooks_dir / "_dispatch.py").write_text("# dispatcher stand-in\n")

        cmd = _build_codex_hook_command(hooks_dir, "guards/quota_guard.py", None)

        assert "python3 -B" in cmd["command"]


class TestDispatchChildSpawn:
    """AST-level check of _dispatch.py's subprocess.run call — no live subprocess."""

    @staticmethod
    def _dispatch_source() -> str:
        import autoskillit.hooks

        dispatch_path = Path(autoskillit.hooks.__file__).parent / "_dispatch.py"
        return dispatch_path.read_text()

    @classmethod
    def _subprocess_run_call(cls) -> ast.Call:
        tree = ast.parse(cls._dispatch_source())
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "run"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "subprocess"
        ]
        assert len(calls) == 1, "expected exactly one subprocess.run call in _dispatch.py"
        return calls[0]

    @staticmethod
    def _argv_node(call: ast.Call) -> ast.expr:
        if call.args:
            return call.args[0]
        for kw in call.keywords:
            if kw.arg == "args":
                return kw.value
        raise AssertionError("subprocess.run call has no positional or args= argument")

    def test_argv_includes_bytecode_suppression_flag(self) -> None:
        call = self._subprocess_run_call()
        argv_node = self._argv_node(call)
        assert isinstance(argv_node, ast.List), "expected a literal argv list"

        string_elements = [
            elt.value
            for elt in argv_node.elts
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
        ]
        assert "-B" in string_elements

    def test_env_sets_pythondontwritebytecode(self) -> None:
        call = self._subprocess_run_call()
        env_kw = next((kw for kw in call.keywords if kw.arg == "env"), None)
        assert env_kw is not None, "subprocess.run call has no env= keyword"
        assert isinstance(env_kw.value, ast.Name), "expected env= to reference a local variable"
        env_var_name = env_kw.value.id

        tree = ast.parse(self._dispatch_source())
        assignment_value: ast.expr | None = None
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == env_var_name
                    and isinstance(target.slice, ast.Constant)
                    and target.slice.value == "PYTHONDONTWRITEBYTECODE"
                ):
                    assignment_value = node.value

        assert assignment_value is not None, (
            f"{env_var_name}['PYTHONDONTWRITEBYTECODE'] assignment not found in _dispatch.py"
        )
        assert isinstance(assignment_value, ast.Constant)
        assert assignment_value.value == "1", "must be the exact string '1', not merely truthy"


class TestShellCaptureRunnerArgv:
    """Codex-only isolated-runner spawn used by shell_capture_hook."""

    @staticmethod
    def _sample_request() -> CaptureRequest:
        return CaptureRequest(
            protocol_version=CAPTURE_REQUEST_PROTOCOL_VERSION,
            action="run",
            mode="capture",
            attempt_id=None,
            lineage_ref=None,
            cwd="/abs/project",
            capture_id="0123456789abcdef",
            command="echo hi",
        )

    def test_argv_includes_bytecode_suppression_flag(self) -> None:
        argv = _runner_argv(self._sample_request())
        assert "-B" in argv

    def test_rendered_harness_sets_pythondontwritebytecode(self) -> None:
        argv = _runner_argv(self._sample_request())
        harness = _render_harness(argv)
        assert "PYTHONDONTWRITEBYTECODE=1" in harness
