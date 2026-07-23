"""Behavioral contracts for generic and typed MCP cancellation shielding."""

from __future__ import annotations

import asyncio
import json
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

import pytest

from autoskillit.server.tools._cancellation_shield import _cancellation_shield

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("decorator", "expected"),
    [
        (
            _cancellation_shield(),
            {"success": False, "error": "cancelled", "subtype": "cancelled"},
        ),
        (
            _cancellation_shield(result_type="generic"),
            {"success": False, "error": "cancelled", "subtype": "cancelled"},
        ),
        (
            _cancellation_shield(result_type="run_cmd"),
            {
                "success": False,
                "exit_code": -1,
                "stdout": "",
                "stderr": "CancelledError: transport teardown",
            },
        ),
        (
            _cancellation_shield(result_type="run_python"),
            {
                "success": False,
                "exit_code": -1,
                "stdout": "",
                "stderr": "CancelledError: transport teardown",
            },
        ),
    ],
)
async def test_legacy_modes_preserve_existing_result_schemas(
    decorator: Any, expected: dict[str, object]
) -> None:
    @decorator
    async def cancelled() -> str:
        raise asyncio.CancelledError

    assert json.loads(await cancelled()) == expected


@pytest.mark.anyio
async def test_explicit_fleet_result_mode_is_preserved() -> None:
    @_cancellation_shield(result_type="fleet_error")
    async def cancelled() -> str:
        raise asyncio.CancelledError

    result = json.loads(await cancelled())
    assert result["success"] is False
    assert result["error"] == "fleet_l3_startup_or_crash"
    assert result["user_visible_message"] == "CancelledError: transport teardown"


@dataclass(frozen=True, slots=True)
class _State:
    request_id: str
    admitted: bool
    bound_bytes: int


def _typed_decorator(
    state_factory: Any,
    state_context_var: ContextVar[_State],
    response_factory: Any,
) -> Any:
    return _cancellation_shield(
        state_factory=state_factory,
        state_context_var=state_context_var,
        response_factory=response_factory,
    )


@pytest.mark.parametrize(
    "typed_kwargs",
    [
        {"state_factory": lambda: None},
        {"state_context_var": ContextVar("partial-state")},
        {"response_factory": lambda state, exc: ""},
        {
            "state_factory": lambda: None,
            "state_context_var": ContextVar("partial-state"),
        },
        {
            "state_factory": lambda: None,
            "response_factory": lambda state, exc: "",
        },
        {
            "state_context_var": ContextVar("partial-state"),
            "response_factory": lambda state, exc: "",
        },
    ],
)
def test_typed_mode_arguments_are_all_or_none(typed_kwargs: dict[str, object]) -> None:
    with pytest.raises(TypeError, match="state_factory.*state_context_var.*response_factory"):
        _cancellation_shield(**typed_kwargs)  # type: ignore[arg-type]


def test_typed_mode_rejects_explicit_result_type() -> None:
    state_var: ContextVar[_State] = ContextVar("state")

    with pytest.raises(TypeError, match="result_type"):
        _cancellation_shield(
            result_type="generic",
            state_factory=lambda: _State("request", True, 512),
            state_context_var=state_var,
            response_factory=lambda state, exc: "{}",
        )


@pytest.mark.anyio
async def test_typed_factories_use_exact_signatures_and_same_state_object() -> None:
    state_var: ContextVar[_State] = ContextVar("state")
    observed: list[tuple[str, object]] = []

    def state_factory() -> _State:
        state = _State("request", True, 512)
        observed.append(("factory", state))
        return state

    def response_factory(state: _State, exc: asyncio.CancelledError) -> str:
        observed.append(("response", state))
        observed.append(("exception", exc))
        return json.dumps({"request_id": state.request_id, "cancelled": True})

    @_typed_decorator(state_factory, state_var, response_factory)
    async def cancelled(positional: str, *, keyword: str) -> str:
        assert (positional, keyword) == ("positional", "keyword")
        observed.append(("handler", state_var.get()))
        raise asyncio.CancelledError

    result = json.loads(await cancelled("positional", keyword="keyword"))

    assert result == {"request_id": "request", "cancelled": True}
    states = [value for label, value in observed if label != "exception"]
    assert states[0] is states[1] is states[2]
    assert isinstance(observed[-1][1], asyncio.CancelledError)
    with pytest.raises(LookupError):
        state_var.get()


@pytest.mark.anyio
async def test_typed_context_is_restored_after_success_and_failure() -> None:
    state_var: ContextVar[_State] = ContextVar("state")
    prior = _State("prior", False, 256)
    token = state_var.set(prior)
    created: list[_State] = []

    def state_factory() -> _State:
        state = _State(f"request-{len(created)}", True, 512)
        created.append(state)
        return state

    def response_factory(state: _State, exc: asyncio.CancelledError) -> str:
        return state.request_id

    @_typed_decorator(state_factory, state_var, response_factory)
    async def succeeds() -> str:
        assert state_var.get() is created[-1]
        return "ok"

    @_typed_decorator(state_factory, state_var, response_factory)
    async def fails() -> str:
        assert state_var.get() is created[-1]
        raise RuntimeError("not transport cancellation")

    try:
        assert await succeeds() == "ok"
        assert state_var.get() is prior
        with pytest.raises(RuntimeError, match="not transport cancellation"):
            await fails()
        assert state_var.get() is prior
    finally:
        state_var.reset(token)


@pytest.mark.anyio
async def test_nested_typed_calls_restore_the_outer_state() -> None:
    state_var: ContextVar[_State] = ContextVar("state")
    created: list[_State] = []

    def state_factory() -> _State:
        state = _State(f"request-{len(created)}", True, 512)
        created.append(state)
        return state

    def response_factory(state: _State, exc: asyncio.CancelledError) -> str:
        return state.request_id

    @_typed_decorator(state_factory, state_var, response_factory)
    async def inner() -> str:
        assert state_var.get() is created[1]
        return "inner"

    @_typed_decorator(state_factory, state_var, response_factory)
    async def outer() -> str:
        outer_state = state_var.get()
        assert outer_state is created[0]
        assert await inner() == "inner"
        assert state_var.get() is outer_state
        return "outer"

    assert await outer() == "outer"
    with pytest.raises(LookupError):
        state_var.get()


@pytest.mark.anyio
async def test_typed_state_does_not_leak_between_concurrent_tasks() -> None:
    state_var: ContextVar[_State] = ContextVar("state")
    seed_var: ContextVar[str] = ContextVar("seed")
    ready = asyncio.Event()
    release = asyncio.Event()
    seen: dict[str, _State] = {}

    def state_factory() -> _State:
        seed = seed_var.get()
        return _State(seed, True, 512)

    def response_factory(state: _State, exc: asyncio.CancelledError) -> str:
        return state.request_id

    @_typed_decorator(state_factory, state_var, response_factory)
    async def observe() -> str:
        state = state_var.get()
        seen[state.request_id] = state
        if len(seen) == 2:
            ready.set()
        await ready.wait()
        assert state_var.get() is state
        await release.wait()
        return state.request_id

    async def run(seed: str) -> str:
        token = seed_var.set(seed)
        try:
            return await observe()
        finally:
            seed_var.reset(token)

    first = asyncio.create_task(run("first"))
    second = asyncio.create_task(run("second"))
    await ready.wait()
    assert seen["first"] is not seen["second"]
    release.set()
    assert await asyncio.gather(first, second) == ["first", "second"]
    with pytest.raises(LookupError):
        state_var.get()


@pytest.mark.anyio
@pytest.mark.parametrize("admitted", [False, True])
async def test_cancellation_before_and_after_admission_uses_captured_state(
    admitted: bool,
) -> None:
    from autoskillit.server._recipe_section_pagination import (
        RecipeSectionRequestState,
        render_recipe_section_failure,
    )

    state_var: ContextVar[RecipeSectionRequestState] = ContextVar("recipe-request-state")
    state = RecipeSectionRequestState(
        admitted=admitted,
        recipe_section_bound_bytes=512,
    )
    seen: list[RecipeSectionRequestState] = []

    def response_factory(captured: RecipeSectionRequestState, exc: asyncio.CancelledError) -> str:
        seen.append(captured)
        return render_recipe_section_failure(
            "recipe_section_cancelled",
            bound_bytes=captured.recipe_section_bound_bytes,
            context={"admitted": captured.admitted},
        )

    @_typed_decorator(lambda: state, state_var, response_factory)
    async def cancelled() -> str:
        assert state_var.get() is state
        raise asyncio.CancelledError

    rendered = await cancelled()
    response = json.loads(rendered)
    assert seen == [state]
    assert response["success"] is False
    assert response["error"] == "recipe_section_cancelled"
    assert len(rendered.encode("utf-8")) <= state.recipe_section_bound_bytes
    with pytest.raises(LookupError):
        state_var.get()
