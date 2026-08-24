"""Subprocess tests must not inherit the developer's home directories."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import production_interpreter_env

pytestmark = pytest.mark.medium

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TESTS_ROOT = _REPO_ROOT / "tests"
_DEVELOPER_HOME = Path.home()
_SUBPROCESS_METHODS = {"Popen", "check_call", "check_output", "run"}

# These processes exist only to obtain a dead PID for lifecycle bookkeeping.
# The detector verifies the exact ``python -c pass`` argv before honoring an entry.
_LIFECYCLE_PROBE_ALLOWLIST: dict[tuple[str, str], str] = {
    (
        "execution/backends/test_codex_session_storage.py",
        "test_recover_marks_dead_child_reaped_without_kill",
    ): "Creates an already-exited PID and cannot import AutoSkillit or resolve a home.",
    (
        "execution/test_process_kill.py",
        "test_reaped_leader_cannot_authorize_test_group_teardown",
    ): "Creates an already-exited process leader and cannot resolve or mutate a home.",
    (
        "execution/test_process_tether.py",
        "test_sweep_removes_tether_for_dead_child",
    ): "Creates an already-exited child PID and cannot resolve or mutate a home.",
    (
        "execution/test_process_tether.py",
        "test_sweep_reaps_workload_when_wrapper_dead",
    ): "Creates already-exited wrapper PIDs and cannot resolve or mutate a home.",
    (
        "infra/test_mcp_health_advisor.py",
        "_dead_pid",
    ): "Creates an already-exited PID for a health record and executes no project code.",
    (
        "infra/test_pytest_tmp_lifecycle.py",
        "_dead_pid",
    ): "Creates an already-exited PID for reaper bookkeeping and executes no project code.",
}


def test_spawned_interpreter_does_not_see_the_developer_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    isolated_home = Path.home()
    simulated_developer_home = tmp_path / "developer-home"
    assert isolated_home != _DEVELOPER_HOME
    monkeypatch.setenv("HOME", str(simulated_developer_home))

    result = subprocess.run(
        [sys.executable, "-c", "from pathlib import Path; print(Path.home())"],
        check=True,
        capture_output=True,
        text=True,
        env=production_interpreter_env(),
    )

    assert Path(result.stdout.strip()) == isolated_home
    assert Path(result.stdout.strip()) != simulated_developer_home


def test_autoskillit_subprocess_writes_only_under_the_isolated_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    isolated_home = Path.home()
    simulated_developer_home = tmp_path / "developer-home"
    if isolated_home == _DEVELOPER_HOME:
        pytest.skip("home isolation is not in effect; refusing to run the subprocess")
    monkeypatch.setenv("HOME", str(simulated_developer_home))

    result = subprocess.run(
        [sys.executable, "-m", "autoskillit", "doctor"],
        check=True,
        capture_output=True,
        text=True,
        # Runs under four xdist workers; doctor startup can exceed the normal
        # 30-second ceiling under CPU contention even though it is not hung.
        timeout=60,
        env=production_interpreter_env(),
    )

    assert result.returncode == 0
    assert not (simulated_developer_home / ".autoskillit").exists()


def _is_sys_executable(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "sys"
        and node.attr == "executable"
    )


def _is_subprocess_spawn(node: ast.Call) -> bool:
    return (
        isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "subprocess"
        and node.func.attr in _SUBPROCESS_METHODS
        and bool(node.args)
        and any(_is_sys_executable(part) for part in ast.walk(node.args[0]))
    )


def _is_trivial_lifecycle_probe(node: ast.Call) -> bool:
    argv = node.args[0]
    return (
        isinstance(argv, (ast.List, ast.Tuple))
        and len(argv.elts) == 3
        and _is_sys_executable(argv.elts[0])
        and all(
            isinstance(value, ast.Constant) and value.value == expected
            for value, expected in zip(argv.elts[1:], ("-c", "pass"), strict=True)
        )
    )


def _enclosing_functions(
    tree: ast.Module,
) -> dict[ast.Call, ast.FunctionDef | ast.AsyncFunctionDef | None]:
    functions: dict[ast.Call, ast.FunctionDef | ast.AsyncFunctionDef | None] = {}

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.stack: list[ast.FunctionDef | ast.AsyncFunctionDef] = []

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self.stack.append(node)
            self.generic_visit(node)
            self.stack.pop()

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self.stack.append(node)
            self.generic_visit(node)
            self.stack.pop()

        def visit_Call(self, node: ast.Call) -> None:
            if _is_subprocess_spawn(node):
                functions[node] = self.stack[-1] if self.stack else None
            self.generic_visit(node)

    Visitor().visit(tree)
    return functions


def _has_production_env_call(node: ast.AST) -> bool:
    return any(
        isinstance(part, ast.Call)
        and isinstance(part.func, ast.Name)
        and part.func.id == "production_interpreter_env"
        for part in ast.walk(node)
    )


def _expression_is_derived_from_helper(node: ast.AST, tree: ast.Module) -> bool:
    if _has_production_env_call(node):
        return True
    local_calls = {
        part.func.id
        for part in ast.walk(node)
        if isinstance(part, ast.Call) and isinstance(part.func, ast.Name)
    }
    return any(
        isinstance(definition, (ast.FunctionDef, ast.AsyncFunctionDef))
        and definition.name in local_calls
        and _has_production_env_call(definition)
        for definition in tree.body
    )


def _env_is_derived_from_helper(
    env: ast.expr,
    tree: ast.Module,
    enclosing_function: ast.FunctionDef | ast.AsyncFunctionDef | None,
) -> bool:
    if _expression_is_derived_from_helper(env, tree):
        return True

    if isinstance(env, ast.Name):
        scope: ast.AST = enclosing_function or tree
        for node in ast.walk(scope):
            if (
                isinstance(node, (ast.Assign, ast.AnnAssign))
                and _assignment_targets_name(node, env.id)
                and node.value is not None
                and _expression_is_derived_from_helper(node.value, tree)
            ):
                return True

    return False


def _assignment_targets_name(node: ast.Assign | ast.AnnAssign, name: str) -> bool:
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    return any(isinstance(target, ast.Name) and target.id == name for target in targets)


def _subprocess_env_violations() -> tuple[list[str], set[tuple[str, str]]]:
    violations: list[str] = []
    observed_allowlist_entries: set[tuple[str, str]] = set()
    for path in sorted(_TESTS_ROOT.rglob("*.py")):
        relative = path.relative_to(_TESTS_ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        enclosing_functions = _enclosing_functions(tree)
        for call, enclosing_function in enclosing_functions.items():
            function_name = enclosing_function.name if enclosing_function else "<module>"
            identity = (relative, function_name)
            if identity in _LIFECYCLE_PROBE_ALLOWLIST:
                if not _is_trivial_lifecycle_probe(call):
                    violations.append(
                        f"{relative}:{call.lineno}: allowlisted lifecycle probe is no longer "
                        "exactly [sys.executable, '-c', 'pass']"
                    )
                observed_allowlist_entries.add(identity)
                continue

            env_keyword = next(
                (keyword for keyword in call.keywords if keyword.arg == "env"), None
            )
            if env_keyword is None or not _env_is_derived_from_helper(
                env_keyword.value, tree, enclosing_function
            ):
                violations.append(
                    f"{relative}:{call.lineno}: sys.executable spawn must pass env derived "
                    "from production_interpreter_env()"
                )
    return violations, observed_allowlist_entries


def test_every_subprocess_spawning_test_uses_the_isolated_env_helper() -> None:
    violations, observed = _subprocess_env_violations()
    orphaned = sorted(set(_LIFECYCLE_PROBE_ALLOWLIST) - observed)
    assert not orphaned, f"orphaned lifecycle-probe allowlist entries: {orphaned}"
    assert not violations, "\n".join(violations)


def test_subprocess_env_guard_rejects_an_unisolated_spawn() -> None:
    tree = ast.parse("subprocess.run([sys.executable, '-m', 'autoskillit'])")
    call = next(node for node in ast.walk(tree) if isinstance(node, ast.Call))

    assert _is_subprocess_spawn(call)
    assert not _is_trivial_lifecycle_probe(call)
    assert not any(keyword.arg == "env" for keyword in call.keywords)
