"""Conformance tests for registry-driven checkpoint carrier attachment."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest

import autoskillit.server.recipe._recipe_segment_delivery as delivery_module
from autoskillit.core.tool_registry import TOOL_REGISTRY
from autoskillit.pipeline.gate import gate_error_result
from autoskillit.server._notify import track_response_size
from autoskillit.server.tools._types import deny_envelope

pytestmark = [pytest.mark.layer("server"), pytest.mark.medium]

_SUCCESS_CARRIER: dict[str, Any] = {"kind": "success", "marker": "S"}
_PULL_SUCCESS_CARRIER: dict[str, Any] = {"kind": "success", "marker": "P"}
_RECOVERY_CARRIER: dict[str, Any] = {"kind": "recovery", "marker": "R"}
_STEP_NAME = "checkpoint"


def _reader_shape_representatives() -> tuple[str, ...]:
    representatives: dict[tuple[object, ...], str] = {}
    for name in sorted(TOOL_REGISTRY):
        tool_def = TOOL_REGISTRY[name]
        if not (tool_def.automatic_recipe_delivery or tool_def.recovery_recipe_delivery):
            continue
        outcome = tool_def.recipe_delivery_outcome
        assert outcome is not None
        shape = (
            tool_def.automatic_recipe_delivery,
            outcome.success_field,
            type(outcome.success_value),
            outcome.success_value,
            outcome.step_name_field,
        )
        representatives.setdefault(shape, name)
    return tuple(representatives.values())


_READER_SHAPE_TOOLS = _reader_shape_representatives()


def _has_result_sourced_step_name(tool_name: str) -> bool:
    outcome = TOOL_REGISTRY[tool_name].recipe_delivery_outcome
    assert outcome is not None
    return outcome.step_name_field is not None


_ARGUMENT_SOURCED_READER_TOOLS = tuple(
    name for name in _READER_SHAPE_TOOLS if not _has_result_sourced_step_name(name)
)
_RESULT_SOURCED_READER_TOOLS = tuple(
    name for name in _READER_SHAPE_TOOLS if _has_result_sourced_step_name(name)
)


def _payload(
    tool_name: str, *, success: bool, include_step_name: bool = True
) -> dict[str, object]:
    outcome = TOOL_REGISTRY[tool_name].recipe_delivery_outcome
    assert outcome is not None
    if outcome.success_field is None:
        payload: dict[str, object] = {"success": success}
        if not success:
            payload["error"] = "failed"
    else:
        value = outcome.success_value
        if success:
            payload = {outcome.success_field: value}
        else:
            payload = {outcome.success_field: (False if isinstance(value, bool) else "failed")}
    if outcome.step_name_field is not None and include_step_name:
        payload[outcome.step_name_field] = _STEP_NAME
    return payload


def _install_preparer(
    monkeypatch: pytest.MonkeyPatch,
    *,
    success_carrier: dict[str, Any] | None,
    failure: Exception | None = None,
) -> tuple[delivery_module.PreparedRecipeSegmentDelivery, list[str]]:
    prepared = delivery_module.PreparedRecipeSegmentDelivery(
        step_name=_STEP_NAME,
        success_carrier=success_carrier,
        success_pull_carrier=(_PULL_SUCCESS_CARRIER if success_carrier is not None else None),
        recovery_carrier=_RECOVERY_CARRIER,
    )
    calls: list[str] = []

    def prepare(_tool_ctx, step_name: str):
        calls.append(step_name)
        if failure is not None:
            raise failure
        return prepared

    monkeypatch.setattr(
        delivery_module,
        "prepare_recipe_segment_delivery",
        prepare,
    )
    return prepared, calls


def _wrapped(
    tool_name: str,
    response: str,
    *,
    raised: Exception | None = None,
    entered: list[bool] | None = None,
) -> Callable[..., Any]:
    async def handler(step_name: str = "", **_kwargs: object) -> str:
        if entered is not None:
            entered.append(True)
        if raised is not None:
            raise raised
        return response

    handler.__name__ = tool_name
    return track_response_size(tool_name)(handler)


def _success_carrier_for(tool_name: str) -> dict[str, Any] | None:
    return _SUCCESS_CARRIER if TOOL_REGISTRY[tool_name].automatic_recipe_delivery else None


@pytest.mark.parametrize("tool_name", _READER_SHAPE_TOOLS, ids=_READER_SHAPE_TOOLS)
@pytest.mark.anyio
async def test_success_payload_gets_declared_carrier(
    tool_name: str,
    tool_ctx,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    success_carrier = _success_carrier_for(tool_name)
    prepared, calls = _install_preparer(
        monkeypatch,
        success_carrier=success_carrier,
    )
    payload = _payload(tool_name, success=True)
    outcome = TOOL_REGISTRY[tool_name].recipe_delivery_outcome
    assert outcome is not None and outcome.succeeded(payload)

    argument_step = "argument-step" if outcome.step_name_field is not None else _STEP_NAME
    result = await _wrapped(tool_name, json.dumps(payload))(step_name=argument_step)
    parsed = json.loads(result)

    if prepared.success_carrier is None:
        assert parsed == payload
    else:
        assert parsed["recipe_segment"] == prepared.success_carrier
    expected_step = (
        payload[outcome.step_name_field] if outcome.step_name_field is not None else argument_step
    )
    assert calls == [expected_step]


@pytest.mark.parametrize("tool_name", _READER_SHAPE_TOOLS, ids=_READER_SHAPE_TOOLS)
@pytest.mark.anyio
async def test_failure_payload_gets_recovery_carrier(
    tool_name: str,
    tool_ctx,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepared, calls = _install_preparer(
        monkeypatch,
        success_carrier=_success_carrier_for(tool_name),
    )
    payload = _payload(tool_name, success=False, include_step_name=False)
    outcome = TOOL_REGISTRY[tool_name].recipe_delivery_outcome
    assert outcome is not None and not outcome.succeeded(payload)

    result = await _wrapped(tool_name, json.dumps(payload))(step_name=_STEP_NAME)
    parsed = json.loads(result)

    if outcome.step_name_field is None:
        assert parsed["recipe_segment"] == _RECOVERY_CARRIER
        assert calls == [_STEP_NAME]
    else:
        assert "recipe_segment" not in parsed
        assert calls == []


@pytest.mark.parametrize("tool_name", _READER_SHAPE_TOOLS, ids=_READER_SHAPE_TOOLS)
@pytest.mark.anyio
async def test_handler_exception_gets_recovery_carrier(
    tool_name: str,
    tool_ctx,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outcome = TOOL_REGISTRY[tool_name].recipe_delivery_outcome
    assert outcome is not None
    _prepared, calls = _install_preparer(
        monkeypatch,
        success_carrier=_success_carrier_for(tool_name),
    )
    result = await _wrapped(tool_name, "", raised=RuntimeError("boom"))(step_name=_STEP_NAME)
    parsed = json.loads(result)

    assert parsed["subtype"] == "tool_exception"
    assert parsed["error"] == "RuntimeError: boom"
    if outcome.step_name_field is None:
        assert parsed["recipe_segment"] == _RECOVERY_CARRIER
        assert calls == [_STEP_NAME]
    else:
        assert "recipe_segment" not in parsed
        assert calls == []


@pytest.mark.parametrize("tool_name", _READER_SHAPE_TOOLS, ids=_READER_SHAPE_TOOLS)
@pytest.mark.anyio
async def test_deny_and_gate_errors_get_recovery_carrier(
    tool_name: str,
    tool_ctx,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outcome = TOOL_REGISTRY[tool_name].recipe_delivery_outcome
    assert outcome is not None
    _prepared, calls = _install_preparer(
        monkeypatch,
        success_carrier=_success_carrier_for(tool_name),
    )
    responses = (
        json.dumps(deny_envelope("denied", stage="test", retriable=False)),
        gate_error_result(),
    )

    for response in responses:
        parsed = json.loads(await _wrapped(tool_name, response)(step_name=_STEP_NAME))
        if outcome.step_name_field is None:
            assert parsed["recipe_segment"] == _RECOVERY_CARRIER
        else:
            assert "recipe_segment" not in parsed

    assert calls == ([] if outcome.step_name_field is not None else [_STEP_NAME] * 2)


@pytest.mark.parametrize("tool_name", _READER_SHAPE_TOOLS, ids=_READER_SHAPE_TOOLS)
@pytest.mark.anyio
async def test_missing_step_name_is_byte_identical_passthrough(
    tool_name: str,
    tool_ctx,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prepared, calls = _install_preparer(
        monkeypatch,
        success_carrier=_success_carrier_for(tool_name),
    )
    response = json.dumps({"success": True, "value": "unchanged"})
    result = await _wrapped(tool_name, response)()

    assert result == response
    assert calls == []


@pytest.mark.parametrize(
    "tool_name",
    _ARGUMENT_SOURCED_READER_TOOLS,
    ids=_ARGUMENT_SOURCED_READER_TOOLS,
)
@pytest.mark.anyio
async def test_pre_effect_prepare_failure_prevents_handler(
    tool_name: str,
    tool_ctx,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered: list[bool] = []
    _prepared, calls = _install_preparer(
        monkeypatch,
        success_carrier=_success_carrier_for(tool_name),
        failure=delivery_module.RecipeSegmentDeliveryError("unavailable"),
    )
    result = await _wrapped(
        tool_name,
        json.dumps({"success": True}),
        entered=entered,
    )(step_name=_STEP_NAME)

    assert not entered
    assert calls == [_STEP_NAME]
    assert json.loads(result) == {
        "success": False,
        "error": "RecipeSegmentDeliveryError: unavailable",
    }


@pytest.mark.parametrize(
    "tool_name",
    _RESULT_SOURCED_READER_TOOLS,
    ids=_RESULT_SOURCED_READER_TOOLS,
)
@pytest.mark.anyio
async def test_result_sourced_prepare_failure_is_retriable_after_handler(
    tool_name: str,
    tool_ctx,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered: list[bool] = []
    _prepared, calls = _install_preparer(
        monkeypatch,
        success_carrier=_SUCCESS_CARRIER,
        failure=delivery_module.RecipeSegmentDeliveryError("unavailable"),
    )
    payload = _payload(tool_name, success=True)
    result = await _wrapped(
        tool_name,
        json.dumps(payload),
        entered=entered,
    )(step_name="argument-step")
    parsed = json.loads(result)

    assert entered
    assert calls == [_STEP_NAME]
    assert parsed["success"] is False
    assert parsed["stage"] == "recipe_segment_delivery"
    assert parsed["retriable"] is True
    assert "recipe_segment" not in parsed
    assert "do_not_repeat" not in parsed


@pytest.mark.anyio
async def test_unflagged_tools_never_prepare_or_attach(
    tool_ctx,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def prepare(_tool_ctx, step_name: str):
        calls.append(step_name)
        return None

    monkeypatch.setattr(
        delivery_module,
        "prepare_recipe_segment_delivery",
        prepare,
    )
    for tool_name in sorted(TOOL_REGISTRY):
        tool_def = TOOL_REGISTRY[tool_name]
        if tool_def.automatic_recipe_delivery or tool_def.recovery_recipe_delivery:
            continue
        for success in (True, False):
            payload = {"success": success}
            result = await _wrapped(tool_name, json.dumps(payload))(step_name=_STEP_NAME)
            assert json.loads(result) == payload, (tool_name, success)

    assert calls == []
