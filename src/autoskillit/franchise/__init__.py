"""Franchise sub-package: campaign dispatch orchestration.

Gateway exports per REQ-IMP-001 — consumers import from
``autoskillit.franchise``, not from sub-modules.
"""

from ._api import execute_dispatch
from ._prompts import _build_food_truck_prompt as _build_food_truck_prompt
from ._prompts import _build_l2_sous_chef_block as _build_l2_sous_chef_block
from .state import (
    CampaignState,
    DispatchRecord,
    DispatchStatus,
    ResumeDecision,
    append_dispatch_record,
    mark_dispatch_interrupted,
    mark_dispatch_running,
    read_state,
    resume_campaign_from_state,
    write_initial_state,
)

# Tools that appear in the Franchise group in menu listings (cook + kitchen).
# Both cli/_cook.py and server/tools_kitchen.py use this constant.
FRANCHISE_MENU_TOOLS: tuple[str, ...] = ("dispatch_food_truck",)

__all__ = [
    "execute_dispatch",
    "FRANCHISE_MENU_TOOLS",
    "_build_food_truck_prompt",
    "_build_l2_sous_chef_block",
    "CampaignState",
    "DispatchRecord",
    "DispatchStatus",
    "ResumeDecision",
    "append_dispatch_record",
    "mark_dispatch_interrupted",
    "mark_dispatch_running",
    "read_state",
    "resume_campaign_from_state",
    "write_initial_state",
]
