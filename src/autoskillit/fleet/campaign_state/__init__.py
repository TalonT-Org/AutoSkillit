"""Fleet campaign-state storage, transitions, records, effects, and recovery.

Owns crash-safe campaign-state persistence and bounded mutation ownership
(``state``, ``_state_lock``), retry-relevant effect provenance
(``state_effects``), dispatch-record/campaign-state/resume-decision types
(``state_records``), the dispatch state machine (``state_transitions``),
gate outcome recording (``state_gates``), dispatch outcome/result types
(``state_outcomes``), error-code categorization (``state_error_codes``),
and crash recovery/campaign resume logic (``state_recovery``).
"""
