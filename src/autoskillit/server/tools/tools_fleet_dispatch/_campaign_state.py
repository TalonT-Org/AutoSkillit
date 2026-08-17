"""Campaign state helpers for fleet dispatch MCP tools."""

from __future__ import annotations

import functools
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from autoskillit.core import (
    CodingAgentBackend,
    SkillExecutionRole,
    detect_autoskillit_mcp_prefix,
    get_logger,
)
from autoskillit.fleet import (
    CampaignStateMutator,
    DispatchCompleted,
    DispatchEffectName,
    DispatchEffectProvenance,
    DispatchProvenanceTracker,
    DispatchRecord,
    DispatchRejected,
    _build_food_truck_prompt,
    read_state,
    upsert_dispatch_record_by_name,
)
from autoskillit.server._misc import SkillProjectionContext, project_agent_skill_document

if TYPE_CHECKING:
    from autoskillit.fleet import DispatchOutcome

logger = get_logger(__name__)


def _write_dispatch_to_campaign_state(
    campaign_state_path_str: str,
    effective_name: str,
    outcome: DispatchOutcome,
    per_dispatch_state_path: Path | None = None,
) -> bool:
    """Write the dispatch outcome to the campaign state file.

    Accepts a DispatchOutcome (DispatchCompleted or DispatchRejected) and persists
    the dispatch record to AUTOSKILLIT_CAMPAIGN_STATE_PATH. Never raises — state
    write failures are non-fatal.

    When per_dispatch_state_path is provided, reads the authoritative DispatchRecord
    from the per-dispatch state file and forwards it directly, avoiding manual
    field reconstruction and eliminating double-normalization of token_usage.
    """
    try:
        match outcome:
            case DispatchRejected(error_code=code, message=msg):
                upsert_dispatch_record_by_name(
                    Path(campaign_state_path_str),
                    DispatchRecord.for_refusal(
                        name=effective_name,
                        error_code=code,
                        diagnostic_message=msg,
                        dispatch_id=outcome.dispatch_id,
                        effect_provenance=outcome.effect_provenance.to_dict(),
                    ),
                )
            case DispatchCompleted() as completed:
                if per_dispatch_state_path is not None:
                    per_dispatch_state = read_state(per_dispatch_state_path)
                    if per_dispatch_state is None:
                        logger.warning(
                            "_write_dispatch_to_campaign_state: read_state(%s) returned None "
                            "— falling back to manual reconstruction",
                            per_dispatch_state_path,
                        )
                    else:
                        for d in per_dispatch_state.dispatches:
                            if d.name == effective_name:
                                upsert_dispatch_record_by_name(
                                    Path(campaign_state_path_str),
                                    d,
                                )
                                return True
                        logger.warning(
                            "_write_dispatch_to_campaign_state: no dispatch named %r in %s "
                            "— falling back to manual reconstruction",
                            effective_name,
                            per_dispatch_state_path,
                        )
                upsert_dispatch_record_by_name(
                    Path(campaign_state_path_str),
                    DispatchRecord(
                        name=effective_name,
                        status=completed.dispatch_status,
                        dispatch_id=completed.dispatch_id,
                        dispatched_session_id=completed.dispatched_session_id,
                        reason=completed.reason,
                        diagnostic_message=completed.diagnostic_message,
                        token_usage=completed.token_usage,
                        effect_provenance=completed.effect_provenance.to_dict(),
                    ),
                )
        return True
    except Exception:
        logger.warning("_write_dispatch_to_campaign_state: failed", exc_info=True)
        return False


def _confirm_campaign_state_write(
    provenance: DispatchProvenanceTracker,
    campaign_state_path_str: str,
    effective_name: str,
) -> bool:
    """Confirm the write and persist its post-confirmation provenance receipt."""
    provenance.confirm(
        DispatchEffectName.CAMPAIGN_STATE_WRITE,
        receipt="campaign state writer confirmed persistence",
        identities={"campaign_state_path": campaign_state_path_str},
    )
    try:
        receipt_persisted = False
        with CampaignStateMutator(Path(campaign_state_path_str)) as mutator:
            if mutator.state is not None:
                record = next(
                    (
                        dispatch
                        for dispatch in mutator.state.dispatches
                        if dispatch.name == effective_name
                    ),
                    None,
                )
                if record is not None:
                    receipt = provenance.snapshot().to_dict()
                    if record.effect_provenance != receipt:
                        record.effect_provenance = receipt
                        mutator.mark_dirty()
                    receipt_persisted = True
    except Exception:
        logger.warning(
            "_confirm_campaign_state_write: receipt persistence failed",
            exc_info=True,
        )
        receipt_persisted = False
    if not receipt_persisted:
        provenance.mark_ambiguous(
            DispatchEffectName.CAMPAIGN_STATE_WRITE,
            evidence="campaign state confirmation receipt persistence failed",
            identities={"campaign_state_path": campaign_state_path_str},
        )
    return receipt_persisted


def _get_food_truck_prompt_builder(
    backend: CodingAgentBackend,
    has_unguarded_filesystem_access: bool = False,
    projected_sous_chef: str = "",
) -> Callable[..., str]:
    """Return the food truck prompt builder with mcp_prefix pre-bound."""

    mcp_prefix = detect_autoskillit_mcp_prefix(backend.capabilities)
    return functools.partial(
        _build_food_truck_prompt,
        mcp_prefix=mcp_prefix,
        has_unguarded_filesystem_access=has_unguarded_filesystem_access,
        projected_sous_chef=projected_sous_chef,
    )


def _project_food_truck_sous_chef(
    tool_ctx: Any,
    backend: CodingAgentBackend | None,
) -> str:
    """Project L2 orchestration guidance before crossing into the fleet layer."""
    if tool_ctx.skill_resolver is None:
        return ""
    catalog = tool_ctx.skill_resolver.list_effective(
        tool_ctx.project_dir,
        SkillExecutionRole.ORCHESTRATOR,
        visibility=tool_ctx.config.skill_visibility_spec(),
        recipe_packs=tool_ctx.active_recipe_packs,
        recipe_features=tool_ctx.active_recipe_features,
    )
    sous_chef = next((skill for skill in catalog.skills if skill.name == "sous-chef"), None)
    if sous_chef is None:
        return ""
    return project_agent_skill_document(
        sous_chef,
        SkillProjectionContext(
            cwd=tool_ctx.project_dir.resolve(),
            catalog=catalog,
            backend=backend,
            conventions=backend.conventions if backend is not None else None,
            gating=False,
        ),
    ).content


def _dispatch_effect_identities(
    snapshot: DispatchEffectProvenance,
) -> dict[str, str]:
    """Collect the latest recorded value for each downstream identity."""
    identities: dict[str, str] = {}
    for effect in snapshot.effects:
        identities.update(effect.known_downstream_identities)
    return identities
