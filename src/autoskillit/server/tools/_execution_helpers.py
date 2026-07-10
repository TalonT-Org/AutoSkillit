"""Subprocess coercion helpers for run_python."""

from __future__ import annotations

import asyncio
import json
import types
import typing
from collections.abc import Mapping
from pathlib import Path

from autoskillit.core import (
    RUN_PYTHON_SENTINEL_KEYS,
    ActiveRunSkillSpec,
    BindingState,
    extract_positional_args,
    extract_skill_name,
    get_logger,
)
from autoskillit.pipeline import gate_error_result

logger = get_logger(__name__)

_PATH_LIKE_ARGS: frozenset[str] = frozenset({"output_dir", "workspace", "diagnostics_log_dir"})


def resolve_step_name_from_recipe(
    skill_command: str,
    active_recipe_steps: Mapping[str, object],
) -> tuple[str, bool]:
    """Resolve a unique step key by matching the sealed or legacy skill prefix."""
    cmd_prefix = skill_command.split()[0] if skill_command.strip() else ""
    if not cmd_prefix:
        return ("", False)
    matches: list[str] = []
    for step_key, step_obj in active_recipe_steps.items():
        if isinstance(step_obj, ActiveRunSkillSpec):
            step_sc = step_obj.expected_skill_command_template
        else:
            with_args = getattr(step_obj, "with_args", None)
            if not isinstance(with_args, dict):
                continue
            step_sc = with_args.get("skill_command", "")
        if step_sc and step_sc.split()[0] == cmd_prefix:
            matches.append(step_key)
    if len(matches) == 1:
        return (matches[0], False)
    return ("", len(matches) > 1)


def sealed_run_skill_specs(tool_ctx: object) -> dict[str, ActiveRunSkillSpec]:
    snapshot = getattr(tool_ctx, "active_recipe_snapshot", None)
    if snapshot is None:
        return {}
    return {spec.step_key: spec for spec in snapshot.run_skill_specs}


def active_step_defaults(
    tool_ctx: object,
    sealed_specs: dict[str, ActiveRunSkillSpec],
    step_name: str,
) -> tuple[str | None, str | None, int | None, int | None]:
    """Return provider/output/stale/idle defaults from sealed state, with legacy fallback."""
    sealed = sealed_specs.get(step_name)
    active_steps = getattr(tool_ctx, "active_recipe_steps", None)
    legacy = active_steps.get(step_name) if isinstance(active_steps, dict) else None
    if sealed is not None:
        return (
            sealed.declared_step_provider,
            sealed.declared_output_dir,
            sealed.declared_stale_threshold,
            sealed.declared_idle_output_timeout,
        )
    with_args = getattr(legacy, "with_args", None)
    output_dir = with_args.get("output_dir") if isinstance(with_args, dict) else None
    return (
        getattr(legacy, "provider", None),
        output_dir,
        getattr(legacy, "stale_threshold", None),
        getattr(legacy, "idle_output_timeout", None),
    )


def check_sealed_invocation_shape(
    skill_command: str,
    step_name: str,
    sealed_specs: dict[str, ActiveRunSkillSpec],
) -> str | None:
    """Reject a run_skill call that diverges from the sealed active-step shape."""
    if not step_name or not sealed_specs:
        return None
    spec = sealed_specs.get(step_name)
    if spec is None:
        return gate_error_result(
            f"Step {step_name!r} is not a sealed run_skill step in the active recipe"
        )
    expected_skill = extract_skill_name(spec.expected_skill_command_template)
    actual_skill = extract_skill_name(skill_command)
    if expected_skill != actual_skill:
        return gate_error_result(
            f"Step {step_name!r} expected skill {expected_skill!r}, got {actual_skill!r}"
        )
    actual_args = extract_positional_args(skill_command)
    for binding in spec.expected_bindings:
        actual = actual_args[binding.position] if binding.position < len(actual_args) else None
        if binding.state == BindingState.OMITTED and actual != "-":
            return gate_error_result(
                f"Step {step_name!r} input {binding.name!r} must preserve its omitted slot"
            )
        if binding.state == BindingState.BOUND and (actual is None or actual == "-"):
            return gate_error_result(
                f"Step {step_name!r} input {binding.name!r} is missing from its sealed slot"
            )
        if binding.state == BindingState.UNBOUND and actual is not None:
            return gate_error_result(
                f"Step {step_name!r} input {binding.name!r} was not declared in the sealed shape"
            )
    return None


def validate_path_arg_anchoring(args: dict[str, object] | None, work_dir: str) -> str | None:
    """Return error message if args contain relative path-like values without work_dir."""
    if not args:
        return None
    for key in _PATH_LIKE_ARGS:
        val = args.get(key)
        if isinstance(val, str) and val and not Path(val).is_absolute() and not work_dir:
            if "work_dir" in args:
                return (
                    f"run_python: arg '{key}' is a relative path ({val!r}) "
                    f"and work_dir appears inside args instead of as a top-level "
                    f"parameter — move work_dir to the top-level run_python call"
                )
            return (
                f"run_python: arg '{key}' is a relative path ({val!r}) "
                f"but work_dir was not provided — pass work_dir to anchor it"
            )
    return None


def resolve_relative_path_args(args: dict[str, object], work_dir: str) -> dict[str, object]:
    """Anchor relative path arguments to work_dir."""
    resolved = dict(args)
    for key in _PATH_LIKE_ARGS:
        val = resolved.get(key)
        if isinstance(val, str) and val and not Path(val).is_absolute():
            resolved[key] = str(Path(work_dir) / val)
    return resolved


def maybe_promote_work_dir(args: dict[str, object] | None, work_dir: str) -> str:
    """Promote work_dir from args to tool level if misplaced by the LLM.

    Returns the (possibly updated) work_dir value. Does not modify args —
    the caller is responsible for removing the key from args after promotion.
    """
    if not args or work_dir or "work_dir" not in args:
        return work_dir
    candidate = args["work_dir"]
    if isinstance(candidate, str) and candidate:
        return candidate
    return work_dir


def _coerce_scalar(val: object, annotation: object) -> object:
    """Coerce val to match the annotated type.

    Handles str, int, float, and Optional[T] / T | None variants.
    Skips containers, unions, bool, and unconvertible values.
    """
    if isinstance(val, bool):
        return val

    actual = annotation

    # Unwrap X | None (types.UnionType — bare union syntax, Python 3.10+)
    if isinstance(annotation, types.UnionType):
        non_none = [a for a in annotation.__args__ if a is not type(None)]
        if len(non_none) == 1:
            actual = non_none[0]
    # Unwrap Optional[X] / Union[X, None] (typing.Union with __origin__)
    elif hasattr(annotation, "__origin__") and hasattr(annotation, "__args__"):
        ann: typing.Any = annotation  # type: ignore[name-defined]
        origin = ann.__origin__
        args = ann.__args__
        if origin is typing.Union:
            non_none = [a for a in args if a is not type(None)]
            if len(non_none) == 1:
                actual = non_none[0]

    # str ← int/float
    if actual is str and not isinstance(val, str):
        if isinstance(val, (int, float)):
            return str(val)
        return val
    # int ← str (try/except for unconvertible)
    if actual is int and not isinstance(val, int):
        if isinstance(val, str):
            try:
                return int(val)
            except ValueError:
                return val
        return val
    # float ← str/int (try/except for unconvertible)
    if actual is float and not isinstance(val, float):
        if isinstance(val, (str, int)):
            try:
                return float(val)
            except ValueError:
                return val
        return val
    return val


async def _import_and_call(
    dotted_path: str,
    args: dict[str, object] | None = None,
    timeout: float = 30,
) -> dict[str, object]:
    """Import a Python callable by dotted path and invoke it.

    Returns dict with 'success', 'result' (or 'error').
    Handles sync and async callables, with timeout protection.
    """
    import importlib
    import inspect

    if args is None:
        args = {}
    args = dict(args)

    if "." not in dotted_path:
        return {"success": False, "error": f"Invalid dotted path: {dotted_path!r}"}

    module_path, attr_name = dotted_path.rsplit(".", 1)

    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        return {"success": False, "error": f"Import failed for {module_path!r}: {exc}"}

    try:
        func = getattr(module, attr_name)
    except AttributeError:
        return {
            "success": False,
            "error": f"Module {module_path!r} has no attribute {attr_name!r}",
        }

    if not callable(func):
        return {"success": False, "error": f"{dotted_path!r} is not callable"}

    sig = inspect.signature(func)

    valid_keys = set(sig.parameters.keys())
    for key in list(args.keys()):
        if key in RUN_PYTHON_SENTINEL_KEYS and key not in valid_keys:
            logger.warning(
                "run_python stripped sentinel key from args",
                callable=dotted_path,
                arg_name=key,
            )
            del args[key]

    accepts_var_keyword = any(
        p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
    )
    if not accepts_var_keyword:
        for key in list(args.keys()):
            if key not in valid_keys:
                logger.warning(
                    "run_python dropped unrecognized arg",
                    callable=dotted_path,
                    arg_name=key,
                    extra_args=[key],
                )
                del args[key]

    try:
        type_hints = typing.get_type_hints(func)
    except (NameError, TypeError, AttributeError):
        logger.warning(
            "get_type_hints failed, skipping coercion", callable=dotted_path, exc_info=True
        )
        type_hints = {}
    coerced: dict[str, object] = {}
    for key, val in args.items():
        if val is None and key in sig.parameters:
            param = sig.parameters[key]
            if param.default is not inspect.Parameter.empty and param.default is not None:
                logger.warning(
                    "run_python null-arg coerced to default",
                    callable=dotted_path,
                    arg=key,
                    default=repr(param.default),
                )
                coerced[key] = param.default
                continue
        if val is not None and key in type_hints:
            annotation = type_hints[key]
            coerced_val = _coerce_scalar(val, annotation)
            if coerced_val is not val:
                logger.warning(
                    "run_python type coerced",
                    callable=dotted_path,
                    arg=key,
                    from_type=type(val).__name__,
                    to_type=type(coerced_val).__name__,
                )
                coerced[key] = coerced_val
                continue
        coerced[key] = val
    args = coerced

    try:
        if inspect.iscoroutinefunction(func):
            result = await asyncio.wait_for(func(**args), timeout=timeout)
        else:
            result = await asyncio.wait_for(asyncio.to_thread(func, **args), timeout=timeout)
    except TimeoutError:
        logger.warning(
            "run_python timed out; sync thread may continue running",
            dotted_path=dotted_path,
            timeout=timeout,
        )
        return {"success": False, "error": f"Timeout after {timeout}s calling {dotted_path}"}
    except Exception as exc:
        logger.warning(
            "run_python execution failed",
            dotted_path=dotted_path,
            error=type(exc).__name__,
            exc_info=True,
        )
        return {"success": False, "error": f"{type(exc).__name__}: {exc}"}

    try:
        json.dumps(result)
        return {"success": True, "result": result}
    except (TypeError, ValueError):
        return {"success": True, "result": str(result)}
