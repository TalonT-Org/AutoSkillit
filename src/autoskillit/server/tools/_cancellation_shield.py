"""Cancellation shield decorator for MCP tool handlers.

Catches asyncio.CancelledError at the MCP tool boundary, converting
transport teardown into a structured JSON response. Without this guard
every tool handler that lacks an explicit except-BaseException clause
silently drops the MCP session instead of returning a routable result.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from contextvars import ContextVar
from functools import wraps
from typing import Any, Literal, TypeVar, cast, overload

import anyio

from autoskillit.core import FleetErrorCode, fleet_error, get_logger
from autoskillit.server._recipe_initialization import (
    admit_registered_tool_during_initialization,
)

logger = get_logger(__name__)

F = TypeVar("F", bound=Callable[..., Any])
StateT = TypeVar("StateT")
ResultType = Literal["fleet_error", "run_cmd", "run_python", "generic"]
_RESULT_TYPE_UNSET = object()
_TYPED_ARGUMENT_UNSET = object()


@overload
def _cancellation_shield() -> Callable[[F], F]: ...


@overload
def _cancellation_shield(
    result_type: ResultType,
) -> Callable[[F], F]: ...


@overload
def _cancellation_shield(
    *,
    state_factory: Callable[[], StateT],
    state_context_var: ContextVar[StateT],
    response_factory: Callable[[StateT, asyncio.CancelledError], str],
) -> Callable[[F], F]: ...


def _cancellation_shield(
    result_type: ResultType | object = _RESULT_TYPE_UNSET,
    *,
    state_factory: Callable[[], Any] | object = _TYPED_ARGUMENT_UNSET,
    state_context_var: ContextVar[Any] | object = _TYPED_ARGUMENT_UNSET,
    response_factory: Callable[[Any, asyncio.CancelledError], str]
    | object = _TYPED_ARGUMENT_UNSET,
) -> Callable[[F], F]:
    """Convert transport cancellation into a structured MCP-boundary response.

    Legacy result modes retain their existing response schemas. Typed mode captures
    one request state before the handler, publishes that exact object through a
    ContextVar, and passes it to the cancellation response factory.
    """
    typed_arguments = (state_factory, state_context_var, response_factory)
    typed_count = sum(argument is not _TYPED_ARGUMENT_UNSET for argument in typed_arguments)
    if typed_count not in {0, 3}:
        raise TypeError(
            "state_factory, state_context_var, and response_factory must be provided together"
        )
    typed_mode = typed_count == 3
    if typed_mode and result_type is not _RESULT_TYPE_UNSET:
        raise TypeError("result_type cannot be supplied with typed cancellation mode")
    if typed_mode:
        if not callable(state_factory):
            raise TypeError("state_factory must be callable")
        if not isinstance(state_context_var, ContextVar):
            raise TypeError("state_context_var must be a ContextVar")
        if not callable(response_factory):
            raise TypeError("response_factory must be callable")
    resolved_result_type = (
        "generic" if result_type is _RESULT_TYPE_UNSET else cast(ResultType, result_type)
    )

    def decorator(fn: F) -> F:
        @wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            if (denial := admit_registered_tool_during_initialization(fn.__name__)) is not None:
                return denial
            if typed_mode:
                typed_state_factory = cast(Callable[[], Any], state_factory)
                typed_context_var = cast(ContextVar[Any], state_context_var)
                typed_response_factory = cast(
                    Callable[[Any, asyncio.CancelledError], str],
                    response_factory,
                )
                state = typed_state_factory()
                token = typed_context_var.set(state)
                try:
                    try:
                        return await fn(*args, **kwargs)
                    except asyncio.CancelledError as exc:
                        with anyio.CancelScope(shield=True):
                            logger.warning("mcp_tool_cancelled", tool=fn.__name__)
                            return typed_response_factory(state, exc)
                finally:
                    typed_context_var.reset(token)
            try:
                return await fn(*args, **kwargs)
            except asyncio.CancelledError:
                with anyio.CancelScope(shield=True):
                    logger.warning("mcp_tool_cancelled", tool=fn.__name__)
                    return _build_cancellation_response(resolved_result_type)

        return wrapper  # type: ignore[return-value]

    return decorator


def _build_cancellation_response(result_type: str) -> str:
    """Build a structured JSON error response for transport-level CancelledError."""
    msg = "CancelledError: transport teardown"
    if result_type == "fleet_error":
        return fleet_error(FleetErrorCode.FLEET_L3_STARTUP_OR_CRASH, msg)
    if result_type in ("run_cmd", "run_python"):
        return json.dumps({"success": False, "exit_code": -1, "stdout": "", "stderr": msg})
    return json.dumps({"success": False, "error": "cancelled", "subtype": "cancelled"})
