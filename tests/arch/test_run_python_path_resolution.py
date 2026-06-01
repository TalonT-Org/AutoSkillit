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
    "project_dir": (
        "enrich_diff_context receives project_dir as the anchor/base directory itself "
        "for constructing temp_dir; resolving it against itself would be circular"
    ),
    "log_dir": (
        "patch_pr_token_summary receives log_dir as an absolute path from the "
        "caller context; never passed as a relative string to run_python steps"
    ),
}


def _find_absoluteness_guard(
    func_node: ast.FunctionDef | ast.AsyncFunctionDef, _param_names: set[str]
) -> bool:
    """Check whether *func_node* guards *param_names* with is_absolute() or raise."""
    for node in ast.walk(func_node):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute) and node.func.attr == "is_absolute":
                return True
        if isinstance(node, ast.Raise) and node.exc is not None:
            for child in ast.walk(node.exc):
                if (
                    isinstance(child, ast.Constant)
                    and isinstance(child.value, str)
                    and "absolute" in child.value.lower()
                ):
                    return True
    return False


_GUARDED_PARAMS = {"output_dir", "workspace", "diagnostics_log_dir", "project_dir"}


def test_smoke_utils_path_params_always_guarded_absolute() -> None:
    """Every Path(path_param) in smoke_utils must be preceded by an is_absolute() guard."""
    violations = []
    for py_file in sorted(_SMOKE_UTILS_DIR.glob("*.py")):
        tree = ast.parse(py_file.read_text(), filename=str(py_file))
        for func_node in ast.walk(tree):
            if not isinstance(func_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            func_params = {a.arg for a in func_node.args.args}
            guarded_in_func = func_params & _GUARDED_PARAMS
            if not guarded_in_func:
                continue
            has_guard = _find_absoluteness_guard(func_node, guarded_in_func)
            if not has_guard:
                violations.append(f"{py_file.name}:{func_node.name}: missing absoluteness guard")
    assert not violations, (
        "smoke_utils callables with path params must check is_absolute():\n"
        + "\n".join(violations)
    )


def test_sentinel_keys_do_not_collide_with_callable_params() -> None:
    """No sentinel key may also be a legitimate callable parameter in bundled recipes."""
    import importlib
    import inspect

    import yaml

    from autoskillit.core.types._type_constants import RUN_PYTHON_SENTINEL_KEYS

    recipes_dir = Path(__file__).resolve().parent.parent.parent / "src/autoskillit/recipes"
    collisions: list[str] = []

    for yaml_file in sorted(recipes_dir.glob("*.yaml")):
        content = yaml.safe_load(yaml_file.read_text())
        steps = content.get("steps", {})
        if not isinstance(steps, dict):
            continue
        for step_name, step in steps.items():
            if not isinstance(step, dict) or step.get("tool") != "run_python":
                continue
            with_args = step.get("with", {})
            callable_path = with_args.get("callable", "")
            if not callable_path or "." not in callable_path:
                continue
            try:
                module_path, attr_name = callable_path.rsplit(".", 1)
                mod = importlib.import_module(module_path)
                func = getattr(mod, attr_name)
                sig = inspect.signature(func)
            except (ImportError, AttributeError, ValueError):
                continue
            overlap = RUN_PYTHON_SENTINEL_KEYS & set(sig.parameters.keys())
            for key in sorted(overlap):
                collisions.append(
                    f"{yaml_file.name}:{step_name} → {callable_path} declares '{key}'"
                )

    assert not collisions, (
        "Sentinel keys collide with callable parameters — "
        "these keys would be stripped before reaching the callable:\n" + "\n".join(collisions)
    )


def test_sentinel_keys_subset_of_tool_params() -> None:
    """Sentinel keys must be a subset of run_python's tool-level params."""
    from autoskillit.core.types._type_constants import RUN_PYTHON_SENTINEL_KEYS
    from autoskillit.recipe.rules.rules_tools import _TOOL_PARAMS

    tool_params = _TOOL_PARAMS["run_python"]
    assert RUN_PYTHON_SENTINEL_KEYS < tool_params, (
        f"Sentinel keys {RUN_PYTHON_SENTINEL_KEYS - tool_params} are not in _TOOL_PARAMS"
    )
    non_sentinel_tool_params = tool_params - RUN_PYTHON_SENTINEL_KEYS - {"args"}
    assert non_sentinel_tool_params == {"work_dir"}, (
        f"Unexpected non-sentinel tool params: {non_sentinel_tool_params}"
    )


def test_executor_uses_canonical_sentinel_constant() -> None:
    """_execution_helpers must not define its own private sentinel set."""
    from autoskillit.server.tools import _execution_helpers

    assert not hasattr(_execution_helpers, "_RUN_PYTHON_SENTINEL_KEYS"), (
        "_execution_helpers still defines private _RUN_PYTHON_SENTINEL_KEYS; "
        "it must import RUN_PYTHON_SENTINEL_KEYS from core"
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


def test_path_like_args_synchronized_across_layers() -> None:
    """_PATH_LIKE_ARGS and _RUN_PYTHON_PATH_LIKE_ARGS must stay in sync."""
    from autoskillit.recipe.rules.rules_tools import _RUN_PYTHON_PATH_LIKE_ARGS
    from autoskillit.server.tools._execution_helpers import _PATH_LIKE_ARGS

    assert _PATH_LIKE_ARGS == _RUN_PYTHON_PATH_LIKE_ARGS, (
        f"Path-like args registries out of sync: "
        f"server={_PATH_LIKE_ARGS}, recipe={_RUN_PYTHON_PATH_LIKE_ARGS}"
    )
