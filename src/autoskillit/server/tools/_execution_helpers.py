"""Subprocess coercion helpers for run_python."""

from __future__ import annotations

import asyncio
import json
import os
import time
import types
import typing
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from autoskillit.core import (
    RUN_PYTHON_SENTINEL_KEYS,
    BackendCapabilities,
    CapturedStream,
    SpillSpec,
    SubprocessResult,
    get_logger,
    resolve_effective_delivery_bound,
    resolve_temp_dir,
    spill_output,
)
from autoskillit.execution import (
    CaptureReadError,
    resolve_worst_case_delivery_bound,
    summarize_capture,
)
from autoskillit.server._misc import _hook_config_overlay_path
from autoskillit.server._response_budget import shape_json_response

logger = get_logger(__name__)

if TYPE_CHECKING:
    from autoskillit.core import SkillResult
    from autoskillit.pipeline import ToolContext

_PATH_LIKE_ARGS: frozenset[str] = frozenset({"output_dir", "workspace", "diagnostics_log_dir"})


def persist_run_skill_state(skill_result: SkillResult, project_dir: Path) -> None:
    from autoskillit.server._misc import persist_run_skill_state as persist  # circular-break

    persist(skill_result, project_dir)


def clear_run_skill_state(project_dir: Path) -> None:
    from autoskillit.server._misc import clear_run_skill_state as clear  # circular-break

    clear(project_dir)


def _spill_spec(tool_ctx: ToolContext) -> SpillSpec:
    budget = tool_ctx.config.output_budget
    return SpillSpec(
        inline_max_chars=budget.inline_max_chars,
        head_chars=budget.head_chars,
        tail_chars=budget.tail_chars,
    )


def run_cmd_artifact_root(tool_ctx: ToolContext, cwd: str) -> Path:
    if cwd and Path(cwd).is_absolute():
        return (
            resolve_temp_dir(Path(cwd).resolve(), tool_ctx.config.workspace.temp_dir) / "run_cmd"
        )
    return tool_ctx.temp_dir / "run_cmd"


def spill_run_cmd_result(
    tool_ctx: ToolContext,
    *,
    cwd: str,
    returncode: int,
    stdout: str,
    stderr: str,
    stdout_capture: CapturedStream | None = None,
    stderr_capture: CapturedStream | None = None,
    capture_error: str | None = None,
    execution_error: str | None = None,
) -> dict[str, object]:
    if capture_error is not None:
        result: dict[str, object] = {
            "success": False,
            "exit_code": returncode,
            "error": f"capture_failed: {capture_error}",
        }
        for stream_name, capture in [("stdout", stdout_capture), ("stderr", stderr_capture)]:
            if capture is not None:
                _process_capture_stream(result, stream_name, capture)
        return result

    if stdout_capture is not None or stderr_capture is not None:
        result = {
            "success": returncode == 0 and execution_error is None,
            "exit_code": returncode,
            "stdout": "",
            "stderr": "",
        }
        if execution_error:
            result["error"] = execution_error
        for stream_name, capture in [("stdout", stdout_capture), ("stderr", stderr_capture)]:
            if capture is not None:
                _process_capture_stream(result, stream_name, capture)
        return result

    artifact_root = run_cmd_artifact_root(tool_ctx, cwd)
    spec = _spill_spec(tool_ctx)
    shaped_stdout = spill_output(stdout, artifact_root, "stdout", spec)
    shaped_stderr = spill_output(stderr, artifact_root, "stderr", spec)
    result = {
        "success": returncode == 0,
        "exit_code": returncode,
        "stdout": shaped_stdout.text,
        "stderr": shaped_stderr.text,
    }
    if shaped_stdout.artifact_path is not None:
        result["stdout_artifact_path"] = shaped_stdout.artifact_path
    if shaped_stderr.artifact_path is not None:
        result["stderr_artifact_path"] = shaped_stderr.artifact_path
    return result


def _process_capture_stream(
    result: dict[str, object],
    stream_name: str,
    capture: CapturedStream,
) -> None:
    if capture.inline_text is not None:
        result[stream_name] = capture.inline_text
        try:
            capture.path.unlink(missing_ok=True)
        except OSError:
            pass
    else:
        promoted_name = f"{stream_name}_{_uuid8()}.log"
        promoted = capture.path.parent / promoted_name
        try:
            os.replace(capture.path, promoted)
        except OSError as exc:
            result["success"] = False
            result["error"] = (
                f"capture_failed: promote {stream_name} artifact "
                f"{capture.path} -> {promoted}: {exc}"
            )
            return
        try:
            fd = os.open(str(promoted.parent), os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError:
            pass
        complete_str = "true" if capture.complete else "false"
        marker = (
            f"\n[spilled {capture.total_bytes} bytes -> {promoted}"
            f" sha256={capture.sha256} complete={complete_str}]\n"
        )
        result[stream_name] = capture.head + marker + capture.tail
        result[f"{stream_name}_artifact_path"] = str(promoted)
        result[f"{stream_name}_total_bytes"] = capture.total_bytes
        result[f"{stream_name}_sha256"] = capture.sha256


def _uuid8() -> str:
    return uuid.uuid4().hex[:8]


def _summarize_streams(
    sub_result: SubprocessResult,
    spec: SpillSpec,
    complete: bool,
) -> tuple[CapturedStream | None, CapturedStream | None, str | None]:
    stdout_capture = None
    stderr_capture = None
    capture_error: str | None = None
    for stream_name in ("stdout", "stderr"):
        stream_path = getattr(sub_result, f"{stream_name}_path")
        if stream_path is not None:
            try:
                cap = summarize_capture(stream_path, spec, complete=complete)
                if stream_name == "stdout":
                    stdout_capture = cap
                else:
                    stderr_capture = cap
            except CaptureReadError as exc:
                capture_error = f"{exc} [orphan={stream_path}]"
                try:
                    stream_path.unlink(missing_ok=True)
                except OSError:
                    pass
    return stdout_capture, stderr_capture, capture_error


def shape_execution_response(
    tool_ctx: ToolContext,
    payload: dict[str, typing.Any],
    *,
    tool_name: str,
    work_dir: str,
) -> str:
    artifact_root = (
        resolve_temp_dir(Path(work_dir).resolve(), tool_ctx.config.workspace.temp_dir) / tool_name
        if work_dir and Path(work_dir).is_absolute()
        else tool_ctx.temp_dir / tool_name
    )
    effective_delivery_token_limit: int | None = None
    backend = getattr(tool_ctx, "backend", None)
    caps = getattr(backend, "capabilities", None) if backend is not None else None
    backend_inspected = True

    if isinstance(caps, BackendCapabilities):
        effective_delivery_token_limit = resolve_effective_delivery_bound(caps)
    if backend_inspected and (
        effective_delivery_token_limit is None or effective_delivery_token_limit <= 0
    ):
        fallback_limit = resolve_worst_case_delivery_bound()
        if fallback_limit > 0:
            logger.warning(
                "Delivery-bound enforcement using worst-case default "
                "(%d tokens): backend capabilities unavailable",
                fallback_limit,
            )
            effective_delivery_token_limit = fallback_limit
    return shape_json_response(
        payload,
        tool_name=tool_name,
        artifact_dir=artifact_root,
        config=tool_ctx.config.output_budget,
        effective_delivery_token_limit=effective_delivery_token_limit,
    )


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
        ann: typing.Any = annotation
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


def propagate_session_deadline(
    project_dir: Path, provider_extras: dict[str, str] | None
) -> dict[str, str] | None:
    """Propagate AUTOSKILLIT_SESSION_DEADLINE from the order overlay to L1 sessions.

    Fleet/food-truck sessions inherit the deadline via env_extras from fleet/_api.py;
    interactive "order" sessions must compute it here. The overlay is read directly
    (mirrors `_check_ingredient_locks`) — do NOT use `_build_config_snapshot`, which
    collapses explicit timeouts to the RunSkillConfig default of 7200.

    Mutates `provider_extras` in place (creating it if None) and returns it.
    Failures are swallowed silently (malformed overlay -> skip).
    """
    try:
        overlay_path = _hook_config_overlay_path(project_dir)
        if not overlay_path.exists():
            return provider_extras
        overlay = json.loads(overlay_path.read_text())
        order_section = overlay.get("order", {})
        if "timeout" not in order_section:
            return provider_extras
        existing_deadline = os.environ.get("AUTOSKILLIT_SESSION_DEADLINE")
        if existing_deadline:
            # Fleet session: preserve inherited deadline unchanged.
            deadline_str = existing_deadline
        else:
            # Order session: compute and cache deadline in process env.
            deadline = time.time() + int(order_section["timeout"])
            deadline_str = str(int(deadline))
            os.environ["AUTOSKILLIT_SESSION_DEADLINE"] = deadline_str
        if provider_extras is None:
            provider_extras = {}
        provider_extras["AUTOSKILLIT_SESSION_DEADLINE"] = deadline_str
    except (json.JSONDecodeError, OSError, ValueError, TypeError):
        pass  # malformed overlay — skip silently
    return provider_extras
