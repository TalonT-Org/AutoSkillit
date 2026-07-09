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
