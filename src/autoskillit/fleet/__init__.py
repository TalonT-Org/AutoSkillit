"""Fleet sub-package: campaign dispatch orchestration.

Gateway exports per REQ-IMP-001 — consumers import from
``autoskillit.fleet``, not from sub-modules.
"""

from ._api import execute_dispatch
from ._prompts import _build_food_truck_prompt as _build_food_truck_prompt
from ._prompts import _build_l2_sous_chef_block as _build_l2_sous_chef_block
from .result_parser import L2ParseResult, parse_l2_result_block
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
from .summary import (
    CampaignSummary,
    CampaignSummaryStatus,
    DispatchTokenUsage,
    PerDispatchEntry,
    SummaryErrorRecord,
    parse_campaign_summary,
    serialize_campaign_summary,
    validate_campaign_summary,
)

# Tools that appear in the Fleet group in menu listings (cook + kitchen).
# Both cli/_cook.py and server/tools_kitchen.py use this constant.
FLEET_MENU_TOOLS: tuple[str, ...] = ("dispatch_food_truck",)

__all__ = [
    "execute_dispatch",
    "FLEET_MENU_TOOLS",
    "_build_food_truck_prompt",
    "_build_l2_sous_chef_block",
    "L2ParseResult",
    "parse_l2_result_block",
    "CampaignSummary",
    "CampaignSummaryStatus",
    "DispatchTokenUsage",
    "PerDispatchEntry",
    "SummaryErrorRecord",
    "parse_campaign_summary",
    "serialize_campaign_summary",
    "validate_campaign_summary",
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
