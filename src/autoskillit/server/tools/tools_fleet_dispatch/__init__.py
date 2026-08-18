"""MCP tool handlers: dispatch_food_truck, record_gate_dispatch."""

from __future__ import annotations

from autoskillit.fleet import execute_dispatch
from autoskillit.server._guards import _require_fleet  # noqa: F401  (mock.patch reachability)
from autoskillit.server.tools.tools_fleet_dispatch._campaign_state import (
    _confirm_campaign_state_write,
    _dispatch_effect_identities,
    _get_food_truck_prompt_builder,
    _project_food_truck_sous_chef,
    _write_dispatch_to_campaign_state,
)
from autoskillit.server.tools.tools_fleet_dispatch._handlers import (
    dispatch_food_truck,
    record_gate_dispatch,
)
from autoskillit.server.tools.tools_fleet_dispatch._provenance import (
    _ACTIVE_DISPATCH_PROVENANCE,
    _BOUND_DISPATCH_PROVENANCE,
    _attach_dispatch_provenance,
    _bind_dispatch_provenance,
    _bound_dispatch_provenance,
    _dispatch_cancellation_response,
    _read_health_report,
)

__all__ = [
    "dispatch_food_truck",
    "execute_dispatch",
    "record_gate_dispatch",
    "_ACTIVE_DISPATCH_PROVENANCE",
    "_BOUND_DISPATCH_PROVENANCE",
    "_attach_dispatch_provenance",
    "_bind_dispatch_provenance",
    "_bound_dispatch_provenance",
    "_confirm_campaign_state_write",
    "_dispatch_cancellation_response",
    "_dispatch_effect_identities",
    "_get_food_truck_prompt_builder",
    "_project_food_truck_sous_chef",
    "_read_health_report",
    "_require_fleet",
    "_write_dispatch_to_campaign_state",
]
