# fleet/campaign_state/

Campaign-state storage, transitions, records, effects, and recovery —
relocated from `fleet/` (issue #4673) with no behavior change.

## Responsibilities

`state` owns crash-safe campaign-state persistence with atomic writes and
resume support; `_state_lock` provides `CampaignStateMutatorOwnership`, the
bounded-timeout flock discipline that guards concurrent state mutation.
`state_records` owns `DispatchRecord`, `CampaignState`, `ResumeDecision`,
the schema-version/halted-sentinel constants, and the retry-clearing
mechanics. `state_transitions` owns the `DispatchStatus` enum and the
transition table (`state_records -> state_transitions` only — the reverse
edge does not exist). `state_effects` owns retry-relevant effect enums,
immutable snapshots, and the request-scoped tracker. `state_outcomes` owns
`GateRecordResult`, `DispatchRejected`, `DispatchCompleted`,
`DispatchOutcome`, and `DispatchResult`. `state_error_codes` owns the
`_ERROR_CODE_CATEGORIES` mapping and `get_error_category`. `state_gates`
records gate dispatch outcomes. `state_recovery` implements crash
recovery and campaign resume logic.

## Import boundary

Consumers import from the defining module under
`autoskillit.fleet.campaign_state`, not through a package-level
re-export — this package's `__init__.py` carries a responsibility
docstring only. External production packages continue using the
`autoskillit.fleet` gateway, which retains its exact existing `__all__`.
`fleet/dispatch/_api.py` and `fleet/dispatch/_errors.py` bind
`campaign_state.state` under the existing `_fleet_state` alias to preserve
their runtime `append_dispatch_record` attribute-lookup seam.
