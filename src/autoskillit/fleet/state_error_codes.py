"""Fleet error-code categorization authority.

Owns the ``_ERROR_CODE_CATEGORIES`` mapping, ``get_error_category``,
and the derived ``_INFRASTRUCTURE_FAILURE_REASONS`` frozenset.
Decomposed from ``state_types`` (#4856).
"""

from autoskillit.core import FaultDomain, FleetErrorCode

_ERROR_CODE_CATEGORIES: dict[str, FaultDomain] = {
    FleetErrorCode.FLEET_L3_TIMEOUT: FaultDomain.INFRASTRUCTURE,
    FleetErrorCode.FLEET_L3_NO_RESULT_BLOCK: FaultDomain.INFRASTRUCTURE,
    FleetErrorCode.FLEET_L3_PARSE_FAILED: FaultDomain.INFRASTRUCTURE,
    FleetErrorCode.FLEET_L3_STARTUP_OR_CRASH: FaultDomain.INFRASTRUCTURE,
    FleetErrorCode.FLEET_QUOTA_EXHAUSTED: FaultDomain.INFRASTRUCTURE,
    FleetErrorCode.FLEET_CLEANUP_FAILED: FaultDomain.INFRASTRUCTURE,
    FleetErrorCode.FLEET_ACQUIRE_TIMEOUT: FaultDomain.INFRASTRUCTURE,
    FleetErrorCode.FLEET_PARALLEL_REFUSED: FaultDomain.INFRASTRUCTURE,
    FleetErrorCode.FLEET_HARD_REFUSAL_HEADLESS: FaultDomain.INFRASTRUCTURE,
    FleetErrorCode.FLEET_MANIFEST_MISSING: FaultDomain.INFRASTRUCTURE,
    FleetErrorCode.FLEET_MANIFEST_CORRUPTED: FaultDomain.INFRASTRUCTURE,
    FleetErrorCode.FLEET_LOCK_NOT_INITIALIZED: FaultDomain.INFRASTRUCTURE,
    FleetErrorCode.FLEET_RECIPE_INVALID: FaultDomain.INFRASTRUCTURE,
    FleetErrorCode.FLEET_PROCESS_STALE: FaultDomain.INFRASTRUCTURE,
    FleetErrorCode.FLEET_FEATURE_DISABLED: FaultDomain.INFRASTRUCTURE,
    FleetErrorCode.FLEET_DISPATCH_SKIPPED: FaultDomain.INFRASTRUCTURE,
    FleetErrorCode.FLEET_GATE_ALREADY_RECORDED: FaultDomain.INFRASTRUCTURE,
    FleetErrorCode.FLEET_GATE_NO_CAMPAIGN: FaultDomain.INFRASTRUCTURE,
    FleetErrorCode.FLEET_GATE_UNKNOWN_DISPATCH: FaultDomain.INFRASTRUCTURE,
    FleetErrorCode.FLEET_BUDGET_EXCEEDED: FaultDomain.INFRASTRUCTURE,
    FleetErrorCode.FLEET_RESUME_SESSION_MISSING: FaultDomain.INFRASTRUCTURE,
    FleetErrorCode.FLEET_UNKNOWN_INGREDIENT: FaultDomain.LOGIC,
    FleetErrorCode.FLEET_MISSING_INGREDIENT: FaultDomain.LOGIC,
    FleetErrorCode.FLEET_CAMPAIGN_HALTED: FaultDomain.LOGIC,
    FleetErrorCode.FLEET_RECIPE_NOT_FOUND: FaultDomain.LOGIC,
    FleetErrorCode.FLEET_INVALID_RECIPE_KIND: FaultDomain.LOGIC,
}


def get_error_category(code: str) -> FaultDomain:
    """Return the category for an error code. Unrecognized codes default to LOGIC."""
    return _ERROR_CODE_CATEGORIES.get(code, FaultDomain.LOGIC)


# Derived from metadata for exhaustiveness
_INFRASTRUCTURE_FAILURE_REASONS: frozenset[str] = frozenset(
    code for code, cat in _ERROR_CODE_CATEGORIES.items() if cat == FaultDomain.INFRASTRUCTURE
)


__all__ = [
    "_ERROR_CODE_CATEGORIES",
    "get_error_category",
    "_INFRASTRUCTURE_FAILURE_REASONS",
]
