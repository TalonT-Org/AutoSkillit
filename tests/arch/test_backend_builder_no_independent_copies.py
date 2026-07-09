"""AST and structural guards: shared env-key literals absent from per-backend files."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from autoskillit.execution.backends import BACKEND_REGISTRY
from autoskillit.execution.backends._backend_cmd_builder_base import BackendCmdBuilderBase

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_BACKENDS_DIR = (
    Path(__file__).resolve().parents[2] / "src" / "autoskillit" / "execution" / "backends"
)

_CLAUDE_PATH = _BACKENDS_DIR / "claude.py"
_CODEX_PATH = _BACKENDS_DIR / "codex.py"
_BASE_PATH = _BACKENDS_DIR / "_backend_cmd_builder_base.py"
_HEADLESS_EXECUTE_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "autoskillit"
    / "execution"
    / "headless"
    / "_headless_execute.py"
)
_SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "autoskillit"
_IDLE_OUTPUT_ENV = "AUTOSKILLIT_IDLE_OUTPUT_TIMEOUT"

SHARED_ENV_LITERAL_KEYS: frozenset[str] = frozenset(
    {
        "AUTOSKILLIT_ALLOWED_WRITE_PREFIX",
        "AUTOSKILLIT_ALLOWED_WRITE_PREFIXES",
        "AUTOSKILLIT_CWD",
    }
)

SHARED_ENV_NAME_REFS: frozenset[str] = frozenset(
    {
        "CAMPAIGN_ID_ENV_VAR",
        "KITCHEN_SESSION_ID_ENV_VAR",
    }
)


def _collect_env_key_string_literals(path: Path) -> set[str]:
    """Collect string constants used as dict subscript keys in assignments."""
    tree = ast.parse(path.read_text())
    keys: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AugAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if (
                isinstance(target, ast.Subscript)
                and isinstance(target.slice, ast.Constant)
                and isinstance(target.slice.value, str)
            ):
                keys.add(target.slice.value)
    return keys


def _collect_env_name_subscripts(path: Path) -> set[str]:
    """Collect Name-node identifiers used as dict subscript keys in assignments."""
    tree = ast.parse(path.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AugAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if isinstance(target, ast.Subscript) and isinstance(target.slice, ast.Name):
                names.add(target.slice.id)
    return names


def _find_function(path: Path, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{path}: function {name!r} not found")


def _call_name(func: ast.expr) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _keyword_value(call: ast.Call, keyword: str) -> ast.expr | None:
    for kw in call.keywords:
        if kw.arg == keyword:
            return kw.value
    return None


def _is_os_environ(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "environ"
        and isinstance(node.value, ast.Name)
        and node.value.id == "os"
    )


def _constant_string(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def test_shared_env_literals_absent_from_per_backend_files() -> None:
    for path in (_CLAUDE_PATH, _CODEX_PATH):
        found = _collect_env_key_string_literals(path) & SHARED_ENV_LITERAL_KEYS
        assert not found, f"{path.name} still has shared env literals as subscript keys: {found}"

    base_keys = _collect_env_key_string_literals(_BASE_PATH)
    missing = SHARED_ENV_LITERAL_KEYS - base_keys
    assert not missing, f"_backend_cmd_builder_base.py missing shared env literals: {missing}"


def test_shared_env_name_refs_absent_from_per_backend_files() -> None:
    for path in (_CLAUDE_PATH, _CODEX_PATH):
        found = _collect_env_name_subscripts(path) & SHARED_ENV_NAME_REFS
        assert not found, f"{path.name} still has shared env Name refs as subscript keys: {found}"

    base_names = _collect_env_name_subscripts(_BASE_PATH)
    missing = SHARED_ENV_NAME_REFS - base_names
    assert not missing, f"_backend_cmd_builder_base.py missing shared env Name refs: {missing}"


def test_all_backend_registry_entries_use_base_assemble() -> None:
    assert BACKEND_REGISTRY, "BACKEND_REGISTRY is empty"
    for name, cls in BACKEND_REGISTRY.items():
        assert issubclass(cls, BackendCmdBuilderBase), (
            f"{name}: {cls.__name__} does not inherit from BackendCmdBuilderBase"
        )


def test_flag_vocabulary_covers_all_claude_flags() -> None:
    from autoskillit.core import (
        NON_VARIADIC_CLAUDE_FLAGS,
        VARIADIC_CLAUDE_FLAGS,
        ClaudeFlags,
    )
    from autoskillit.execution.backends.claude import ClaudeCodeBackend

    backend = ClaudeCodeBackend()
    vocab = backend._flag_vocabulary()
    assert vocab.variadic_flags == VARIADIC_CLAUDE_FLAGS
    assert vocab.non_variadic_flags == NON_VARIADIC_CLAUDE_FLAGS
    assert vocab.variadic_flags | vocab.non_variadic_flags == frozenset(ClaudeFlags)


def test_flag_vocabulary_covers_all_codex_flags() -> None:
    from autoskillit.execution.backends.codex import (
        NON_VARIADIC_CODEX_FLAGS,
        VARIADIC_CODEX_FLAGS,
        CodexBackend,
        CodexFlags,
    )

    backend = CodexBackend()
    vocab = backend._flag_vocabulary()
    assert vocab.variadic_flags == VARIADIC_CODEX_FLAGS
    assert vocab.non_variadic_flags == NON_VARIADIC_CODEX_FLAGS
    assert vocab.variadic_flags | vocab.non_variadic_flags == frozenset(CodexFlags)


@pytest.mark.parametrize("builder_name", ["build_skill_session_cmd", "build_food_truck_cmd"])
def test_codex_stream_idle_timeout_never_sets_process_idle_timeout(builder_name: str) -> None:
    function = _find_function(_CODEX_PATH, builder_name)
    cmdspec_calls = [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Call) and _call_name(node.func) == "CmdSpec"
    ]
    assert cmdspec_calls, f"{builder_name} does not construct CmdSpec"
    for call in cmdspec_calls:
        process_idle = _keyword_value(call, "process_idle_timeout_ms")
        assert isinstance(process_idle, ast.Constant)
        assert process_idle.value == 0, (
            f"{builder_name} must not route stream_idle_timeout_ms into "
            "CmdSpec.process_idle_timeout_ms"
        )


def test_headless_execute_parent_idle_comes_from_resolved_liveness_spec() -> None:
    function = _find_function(_HEADLESS_EXECUTE_PATH, "_execute_claude_headless")
    assigns_resolved_idle = False
    runner_uses_resolved_idle = False
    calls_resolver = False
    normalizes_child_env = False

    for node in ast.walk(function):
        if isinstance(node, ast.Call):
            call_name = _call_name(node.func)
            calls_resolver = calls_resolver or call_name == "resolve_session_liveness_spec"
            normalizes_child_env = normalizes_child_env or call_name == (
                "apply_resolved_child_idle_env"
            )
            idle_kw = _keyword_value(node, "idle_output_timeout")
            if isinstance(idle_kw, ast.Name) and idle_kw.id == "effective_idle":
                runner_uses_resolved_idle = True
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if (
                node.target.id == "effective_idle"
                and isinstance(node.value, ast.Attribute)
                and node.value.attr == "stdout_idle_timeout_sec"
                and isinstance(node.value.value, ast.Name)
                and node.value.value.id == "_liveness_spec"
            ):
                assigns_resolved_idle = True

    assert calls_resolver
    assert assigns_resolved_idle
    assert normalizes_child_env
    assert runner_uses_resolved_idle


def test_idle_output_env_is_not_read_from_ambient_environment() -> None:
    violations: list[str] = []
    for path in _SRC_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Subscript) and _is_os_environ(node.value):
                key = _constant_string(node.slice)
                if key == _IDLE_OUTPUT_ENV:
                    violations.append(f"{path}:{node.lineno}: os.environ[...]")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if _is_os_environ(node.func.value):
                    first_arg = node.args[0] if node.args else None
                    if first_arg is not None and _constant_string(first_arg) == _IDLE_OUTPUT_ENV:
                        violations.append(
                            f"{path}:{node.lineno}: os.environ.{node.func.attr}(...)"
                        )

    assert violations == []
