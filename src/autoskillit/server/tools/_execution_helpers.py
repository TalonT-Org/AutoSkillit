"""Subprocess coercion helpers for run_python."""

from __future__ import annotations

import asyncio
import json
import types
import typing
from pathlib import Path

from autoskillit.core import get_logger

logger = get_logger(__name__)


_RUN_PYTHON_SENTINEL_KEYS: frozenset[str] = frozenset({"callable", "timeout", "work_dir"})

_PATH_LIKE_ARGS: frozenset[str] = frozenset({"output_dir", "workspace", "diagnostics_log_dir"})


def validate_path_arg_anchoring(args: dict[str, object] | None, work_dir: str) -> str | None:
    """Return error message if args contain relative path-like values without work_dir."""
    if not args:
        return None
    for key in _PATH_LIKE_ARGS:
        val = args.get(key)
        if isinstance(val, str) and val and not Path(val).is_absolute() and not work_dir:
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

    for key in list(args.keys()):
        if key in _RUN_PYTHON_SENTINEL_KEYS:
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
        valid_keys = set(sig.parameters.keys())
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
