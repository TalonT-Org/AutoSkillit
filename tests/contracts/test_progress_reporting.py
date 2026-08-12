"""Bounded recipe progress counters are complete and monotonic."""

import json

import pytest

from autoskillit.core import SerializedChars
from autoskillit.execution.backends import BACKEND_REGISTRY
from autoskillit.pipeline import ToolContext
from autoskillit.server.tools.tools_recipe import get_recipe_section
from tests.server._helpers import _open_kitchen_patched

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.medium, pytest.mark.anyio]


async def test_progress_counters_in_every_recovery_response(
    tool_ctx: ToolContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tool_ctx, "backend", BACKEND_REGISTRY["codex"]())
    envelope = await _open_kitchen_patched("implementation", {}, monkeypatch)
    assert envelope["delivery_bound_spill"] is True
    for key in ("completed_parts", "total_parts", "remaining_section_pulls"):
        assert key in envelope
    identity = {key: value for key, value in envelope["recipe_pull"].items() if key != "pull_tool"}
    completed: list[int] = []
    totals: list[int] = []
    remaining: list[int] = []
    for requirement in envelope["required_sections"]:
        response = json.loads(
            await get_recipe_section(
                section=requirement["section"],
                part=0,
                initialization_id=envelope["initialization_id"],
                page_plan_sha256=requirement["page_plan_sha256"],
                continuation=None,
                **identity,
            )
        )
        for key in ("completed_parts", "total_parts", "remaining_section_pulls"):
            assert key in response
        completed.append(response["completed_parts"])
        totals.append(response["total_parts"])
        remaining.append(response["remaining_section_pulls"])
    assert completed == [1, 2]
    assert totals == [2, 2]
    assert remaining == [1, 0]


async def test_progress_recheck_enforces_client_char_ceiling_too(
    tool_ctx: ToolContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The post-mutation re-check that gates the initialization progress-counter
    update must also enforce the
    client-measured serialized-char ceiling, not just the byte ceiling —
    otherwise a page that passes the byte re-check could still overflow the
    client's actual truncation gate.
    """
    monkeypatch.setattr(tool_ctx, "backend", BACKEND_REGISTRY["codex"]())
    envelope = await _open_kitchen_patched("implementation", {}, monkeypatch)
    assert envelope["delivery_bound_spill"] is True
    identity = {key: value for key, value in envelope["recipe_pull"].items() if key != "pull_tool"}
    requirement = envelope["required_sections"][0]

    from autoskillit.server.tools import _recipe_section_handler

    monkeypatch.setattr(
        _recipe_section_handler,
        "client_serialized_char_len",
        lambda _text: SerializedChars(10**9),
    )

    response = json.loads(
        await get_recipe_section(
            section=requirement["section"],
            part=0,
            initialization_id=envelope["initialization_id"],
            page_plan_sha256=requirement["page_plan_sha256"],
            continuation=None,
            **identity,
        )
    )

    assert response == {"success": False, "error": "recipe_section_bound_too_small"}
