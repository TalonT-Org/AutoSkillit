"""Architectural invariants for run_python path resolution.

Verifies that:
1. No smoke_utils callable falls back to a relative path when output_dir is absent.
2. Every smoke_utils callable parameter matching *_dir, *_path, or *_file is in
   _PATH_LIKE_ARGS (the set that run_python resolves against work_dir) or is
   explicitly excluded with justification.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_SMOKE_UTILS_DIR = Path(__file__).resolve().parent.parent.parent / "src/autoskillit/smoke_utils"

_PATH_LIKE_SUFFIXES = ("_dir", "_path", "_file")

_EXPLICITLY_EXCLUDED: dict[str, str] = {
    "counter_file": (
        "constructed as absolute via ${{ context.work_dir }}/... in recipe YAML; "
        "is_absolute() guard in resolve_relative_path_args skips it safely"
    ),
    "eval_run_dir": (
        "always passed as an absolute path from context (eval_run_dir is set by "
        "the recipe executor as an absolute directory); never a relative path at the "
        "run_python call site"
    ),
    "worktree_path": (
        "always absolute; git worktree paths are inherently absolute by design "
        "and are never passed as relative paths to run_python steps"
    ),
    "work_dir": (
        "enrich_diff_context receives work_dir as the anchor/base directory itself "
        "for constructing temp_dir; resolving it against itself would be circular"
    ),
    "log_dir": (
        "patch_pr_token_summary receives log_dir as an absolute path from the "
        "caller context; never passed as a relative string to run_python steps"
    ),
}


def test_smoke_utils_output_dir_never_resolves_against_implicit_cwd() -> None:
    """No smoke_utils callable may fall back to a relative path when output_dir is falsy."""
    violations: list[str] = []

    for py_file in sorted(_SMOKE_UTILS_DIR.glob("*.py")):
        source = py_file.read_text()
        tree = ast.parse(source, filename=str(py_file))

        for func_node in ast.walk(tree):
            if not isinstance(func_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            param_names = {arg.arg for arg in func_node.args.args}
            if "output_dir" not in param_names:
                continue

            for stmt in ast.walk(func_node):
                if not isinstance(stmt, ast.If):
                    continue
                for else_stmt in stmt.orelse:
                    for call_node in ast.walk(else_stmt):
                        if not isinstance(call_node, ast.Call):
                            continue
                        func = call_node.func
                        if not (isinstance(func, ast.Name) and func.id == "Path"):
                            continue
                        if not call_node.args:
                            continue
                        first_arg = call_node.args[0]
                        if not isinstance(first_arg, ast.Constant):
                            continue
                        val = first_arg.value
                        if isinstance(val, str) and not val.startswith("/"):
                            violations.append(
                                f"{py_file.name}:{call_node.lineno}: "
                                f"'{func_node.name}' falls back to relative Path({val!r}) "
                                f"— raise ValueError instead"
                            )

    assert not violations, (
        "smoke_utils callables must not fall back to relative paths; "
        "run_python work_dir anchoring ensures output_dir always arrives absolute:\n"
        + "\n".join(violations)
    )


def test_path_like_args_registry_complete() -> None:
    """Every smoke_utils callable param named *_dir, *_path, or *_file must be in _PATH_LIKE_ARGS
    or explicitly excluded."""
    from autoskillit.server.tools._execution_helpers import _PATH_LIKE_ARGS

    unregistered: list[str] = []

    for py_file in sorted(_SMOKE_UTILS_DIR.glob("*.py")):
        source = py_file.read_text()
        tree = ast.parse(source, filename=str(py_file))

        for func_node in ast.walk(tree):
            if not isinstance(func_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for arg in func_node.args.args:
                name = arg.arg
                if not any(name.endswith(suffix) for suffix in _PATH_LIKE_SUFFIXES):
                    continue
                if name in _PATH_LIKE_ARGS:
                    continue
                if name in _EXPLICITLY_EXCLUDED:
                    continue
                unregistered.append(
                    f"{py_file.name}:{func_node.name}: param '{name}' not in "
                    f"_PATH_LIKE_ARGS and not explicitly excluded"
                )

    assert not unregistered, (
        "smoke_utils callable params with path-like names must be registered in "
        "_PATH_LIKE_ARGS or listed in _EXPLICITLY_EXCLUDED with justification:\n"
        + "\n".join(unregistered)
    )
