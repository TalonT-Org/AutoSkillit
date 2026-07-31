"""Request-boundary provenance and cancellation contracts for fleet dispatch."""

from __future__ import annotations

import asyncio
import json

import pytest

from autoskillit.fleet import DispatchEffectName
from autoskillit.server.tools._cancellation_shield import _cancellation_shield
from autoskillit.server.tools.tools_fleet_dispatch import (
    _ACTIVE_DISPATCH_PROVENANCE,
    _BOUND_DISPATCH_PROVENANCE,
    _bind_dispatch_provenance,
    _bound_dispatch_provenance,
    _confirm_campaign_state_write,
    _dispatch_cancellation_response,
)

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


@pytest.mark.asyncio
async def test_argument_binder_attaches_unique_request_provenance() -> None:
    @_bind_dispatch_provenance
    @_cancellation_shield(
        state_factory=_bound_dispatch_provenance,
        state_context_var=_ACTIVE_DISPATCH_PROVENANCE,
        response_factory=_dispatch_cancellation_response,
    )
    async def handler(*, resume_session_id: str | None = None) -> str:
        tracker = _ACTIVE_DISPATCH_PROVENANCE.get()
        return json.dumps(
            {
                "success": False,
                "resume_session_id": resume_session_id,
                "operation_id_seen": tracker.operation_id,
            }
        )

    first = json.loads(await handler(resume_session_id="session-1"))
    second = json.loads(await handler(resume_session_id="session-1"))

    assert first["operation_id_seen"] != second["operation_id_seen"]
    assert first["effect_provenance"]["operation_id"] == first["operation_id_seen"]
    assert (
        first["effect_provenance"]["effects"][0]["name"]
        == DispatchEffectName.REQUESTED_RESUME_BINDING.value
    )
    assert _BOUND_DISPATCH_PROVENANCE.get() is None
    assert _ACTIVE_DISPATCH_PROVENANCE.get(None) is None


def test_campaign_write_confirmation_receipt_is_persisted(tmp_path) -> None:
    from autoskillit.fleet import (
        DispatchProvenanceTracker,
        DispatchRecord,
        read_state,
        write_initial_state,
    )

    state_path = tmp_path / "campaign.json"
    tracker = DispatchProvenanceTracker(operation_id="operation-campaign-write")
    tracker.start(
        DispatchEffectName.CAMPAIGN_STATE_WRITE,
        identities={"campaign_state_path": str(state_path)},
    )
    write_initial_state(
        state_path,
        "campaign-1",
        "campaign",
        "/manifest.yaml",
        [
            DispatchRecord(
                name="dispatch-a",
                effect_provenance=tracker.snapshot().to_dict(),
            )
        ],
    )

    assert _confirm_campaign_state_write(tracker, str(state_path), "dispatch-a")

    state = read_state(state_path)
    assert state is not None
    persisted = state.dispatches[0].effect_provenance
    campaign_write = next(
        effect
        for effect in persisted["effects"]
        if effect["name"] == DispatchEffectName.CAMPAIGN_STATE_WRITE.value
    )
    assert campaign_write["phase"] == "confirmed"
    assert persisted["retry_disposition"] == "resume_by_identity"


@pytest.mark.asyncio
async def test_cancellation_uses_exact_active_provenance() -> None:
    @_bind_dispatch_provenance
    @_cancellation_shield(
        state_factory=_bound_dispatch_provenance,
        state_context_var=_ACTIVE_DISPATCH_PROVENANCE,
        response_factory=_dispatch_cancellation_response,
    )
    async def handler() -> str:
        tracker = _ACTIVE_DISPATCH_PROVENANCE.get()
        tracker.start(
            DispatchEffectName.PROCESS_SPAWN,
            identities={"dispatch_id": "dispatch-cancelled"},
        )
        raise asyncio.CancelledError

    response = json.loads(await handler())
    provenance = response["effect_provenance"]

    assert response["success"] is False
    assert provenance["cancel_requested"] is True
    assert provenance["aggregate_phase"] == "unknown"
    assert provenance["retry_disposition"] == "reconcile_required"
    assert provenance["effects"][0]["known_downstream_identities"] == {
        "dispatch_id": "dispatch-cancelled"
    }
    assert _BOUND_DISPATCH_PROVENANCE.get() is None
    assert _ACTIVE_DISPATCH_PROVENANCE.get(None) is None
