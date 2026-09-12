"""Stdlib-only durable snapshot of every observed L0 child run's terminal reason.

Hook subprocesses import the parent package as ``_child_outcome_snapshot``
while package callers import it as ``autoskillit.hooks._child_outcome_snapshot``
(both resolve to the pure re-export facade at ``__init__.py``, which imports
this module). Do not add runtime imports from ``autoskillit.*`` (mirrors the
``_join_ledger.py`` boundary).

One versioned JSON snapshot is kept per ``(backend, parent_session_id)`` under
``<log-root>/child-outcomes/<backend>/<parent-session-id>.json``, beside the
existing ``sessions/``, ``sessions.jsonl``, ``codex-sessions/`` and
``.locks/`` entries. The snapshot is the canonical record; parent
summary/index projections are read-only copies of it (see
``execution/child_outcomes.py`` for the execution-side reader/collector and
``core/types/_type_execution_identity.py``'s ``ChildOutcomeDict`` for the
typed persistence shape this module's wire schema is kept consistent with).

Canonical ``terminal_reason`` values are ``completed``, ``context_exhausted``,
``turn_limited``, ``error``, ``abandoned``, ``interrupted``, and ``unknown``.
Where an existing ``core`` enum already names a value, this module duplicates
the exact string rather than importing it (this module cannot import
``core``): ``context_exhausted`` equals
``InfraExitCategory.CONTEXT_EXHAUSTED.value`` and the turn-limit *input*
evidence equals ``CliSubtype.ERROR_MAX_TURNS.value`` (``"error_max_turns"``).
A focused contract test in ``tests/hooks/test_child_outcomes.py`` pins this
equality against the real enums.
"""

from __future__ import annotations

import importlib
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, TypedDict, cast


# This submodule's own ``__package__`` names its immediate parent package
# (``autoskillit.hooks._child_outcome_snapshot`` when dotted-installed,
# ``_child_outcome_snapshot`` when the parent package was bare-imported off a
# ``sys.path`` entry pointing at ``hooks/`` — see ``child_outcome_hook.py``'s
# ``_HOOKS_DIR`` bootstrap). A dot in that value means the dotted chain, where
# stripping the last component reaches ``hooks``; no dot means the bare
# top-level case, where ``hooks/``'s own siblings resolve by bare name.
# Resolved dynamically via importlib (matching the existing precedent in
# hooks/_hook_settings.py:read_session_binding) rather than a literal dual
# ``if __package__: from .. import X else: import X`` branch, which would
# need a static-analysis suppression comment per branch.
def _resolve_sibling(name: str) -> object:
    if __package__ and "." in __package__:
        return importlib.import_module(f"{__package__.rsplit('.', 1)[0]}.{name}")
    return importlib.import_module(name)


_hook_settings_module = _resolve_sibling("_hook_settings")
_session_binding_module = _resolve_sibling("_session_binding")

_resolve_quota_log_dir = getattr(_hook_settings_module, "resolve_quota_log_dir")
_validate_session_id = getattr(_hook_settings_module, "validate_session_id")
_atomic_write = getattr(_session_binding_module, "atomic_write")
_binding_lock = getattr(_session_binding_module, "binding_lock")

#: Persisted snapshot schema version. Bump alongside any incompatible change
#: to the on-disk record shape below.
CHILD_OUTCOME_SNAPSHOT_SCHEMA_VERSION: int = 1

#: The seven canonical terminal reasons (issue #4623).
REASON_COMPLETED = "completed"
REASON_CONTEXT_EXHAUSTED = "context_exhausted"
REASON_TURN_LIMITED = "turn_limited"
REASON_ERROR = "error"
REASON_ABANDONED = "abandoned"
REASON_INTERRUPTED = "interrupted"
REASON_UNKNOWN = "unknown"

CANONICAL_TERMINAL_REASONS: frozenset[str] = frozenset(
    {
        REASON_COMPLETED,
        REASON_CONTEXT_EXHAUSTED,
        REASON_TURN_LIMITED,
        REASON_ERROR,
        REASON_ABANDONED,
        REASON_INTERRUPTED,
        REASON_UNKNOWN,
    }
)

#: Duplicated from ``core.types._type_enums.InfraExitCategory.CONTEXT_EXHAUSTED.value``.
#: This module cannot import ``core`` (see module docstring); a contract test pins equality.
_INFRA_EXIT_CONTEXT_EXHAUSTED = "context_exhausted"
#: Duplicated from ``core.types._type_enums.CliSubtype.ERROR_MAX_TURNS.value``.
_CLI_SUBTYPE_ERROR_MAX_TURNS = "error_max_turns"
#: Provider/execution terminal-error evidence value, matched on
#: ``ApiFailureOutcome.terminal_reason`` regardless of subtype.
_API_TERMINAL_REASON_ERROR = "api_error"
#: Verbatim harness literal for a foreground subagent that produced no output
#: before an API error, per the official Claude Code sub-agents documentation
#: (v2.1.199+) and pinned in Step 1 investigation notes. Matched by exact
#: substring equality only — never by loose keyword search.
HARNESS_API_ERROR_LITERAL = "Agent terminated early due to an API error"
#: Structured context-window terminal evidence code (distinct from the
#: normalized ``InfraExitCategory`` value above).
_CONTEXT_TERMINAL_CODE = "prompt_too_long"
#: Structured provider/execution terminal-reason value for an explicit turn
#: ceiling (distinct source from ``CliSubtype``).
_TERMINAL_REASON_MAX_TURNS = "max_turns"


class ChildOutcomeWireDict(TypedDict):
    """The persisted, projectable shape of one child's outcome record.

    Field-for-field identical to ``core.types._type_execution_identity.ChildOutcomeDict``
    (the stable persistence/projection type); a contract test in
    ``tests/hooks/test_child_outcomes.py`` pins that parity.
    """

    child_id: str
    launch_alias: str
    backend: str
    parent_session_id: str
    role: str
    attribution_skill: str
    effective_model: str
    effective_effort: str
    effective_provider: str
    terminal_reason: str
    raw_reason: str
    raw_subtype: str
    raw_code: str
    evidence_source: str
    transcript_locator: str
    start_confirmed: bool


class ChildOutcomeSnapshotError(Exception):
    """Raised when a persisted snapshot violates its schema."""


def _empty_outcome(
    *, child_id: str, backend: str, parent_session_id: str, launch_alias: str = ""
) -> ChildOutcomeWireDict:
    return ChildOutcomeWireDict(
        child_id=child_id,
        launch_alias=launch_alias,
        backend=backend,
        parent_session_id=parent_session_id,
        role="",
        attribution_skill="",
        effective_model="",
        effective_effort="",
        effective_provider="",
        terminal_reason=REASON_UNKNOWN,
        raw_reason="",
        raw_subtype="",
        raw_code="",
        evidence_source="",
        transcript_locator="",
        start_confirmed=False,
    )


def resolve_child_outcome_log_root(*, caller: str = "") -> Path | None:
    """Resolve the log root child-outcome snapshots live under.

    Headless/launched sessions read the package-owned
    ``AUTOSKILLIT_CHILD_OUTCOME_LOG_DIR`` channel first (produced by the
    parent launch, see ``execution/session_log.py``). Interactive sessions
    fall back to the same operator/default root every other diagnostic sink
    resolves through (``resolve_quota_log_dir``), keeping the two authorities
    distinct per the design decision: operators own ``AUTOSKILLIT_LOG_DIR``,
    the package owns ``AUTOSKILLIT_CHILD_OUTCOME_LOG_DIR``.
    """
    override = os.environ.get("AUTOSKILLIT_CHILD_OUTCOME_LOG_DIR")
    if override:
        return Path(override)
    return _resolve_quota_log_dir(caller=caller or "child_outcome_snapshot")


def _validated_component(value: str, *, field: str) -> str:
    try:
        return cast(str, _validate_session_id(value))
    except ValueError as exc:
        raise ChildOutcomeSnapshotError(f"{field}: {exc}") from exc


def resolve_snapshot_path(log_root: Path, *, backend: str, parent_session_id: str) -> Path:
    """Return the canonical snapshot path for one ``(backend, parent)`` pair."""
    backend_component = _validated_component(backend, field="backend")
    parent_component = _validated_component(parent_session_id, field="parent_session_id")
    return log_root / "child-outcomes" / backend_component / f"{parent_component}.json"


def _read_raw(snapshot_path: Path) -> dict[str, Any]:
    try:
        raw = snapshot_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise ChildOutcomeSnapshotError(f"snapshot is unreadable: {exc}") from exc
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ChildOutcomeSnapshotError(f"snapshot is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ChildOutcomeSnapshotError("snapshot top level must be an object")
    return parsed


def read_snapshot(snapshot_path: Path) -> dict[str, Any]:
    """Read the raw persisted snapshot document. Returns ``{}`` if absent."""
    return _read_raw(snapshot_path)


def project_outcomes(snapshot: Mapping[str, Any]) -> tuple[ChildOutcomeWireDict, ...]:
    """Return every child's wire-shaped outcome record, sorted by ``child_id``."""
    children = snapshot.get("children")
    if not isinstance(children, dict):
        return ()
    result: list[ChildOutcomeWireDict] = []
    for entry in children.values():
        if not isinstance(entry, dict):
            continue
        outcome = entry.get("outcome")
        if not isinstance(outcome, dict):
            continue
        result.append(
            ChildOutcomeWireDict(
                child_id=str(outcome.get("child_id", "")),
                launch_alias=str(outcome.get("launch_alias", "")),
                backend=str(outcome.get("backend", "")),
                parent_session_id=str(outcome.get("parent_session_id", "")),
                role=str(outcome.get("role", "")),
                attribution_skill=str(outcome.get("attribution_skill", "")),
                effective_model=str(outcome.get("effective_model", "")),
                effective_effort=str(outcome.get("effective_effort", "")),
                effective_provider=str(outcome.get("effective_provider", "")),
                terminal_reason=str(outcome.get("terminal_reason", REASON_UNKNOWN)),
                raw_reason=str(outcome.get("raw_reason", "")),
                raw_subtype=str(outcome.get("raw_subtype", "")),
                raw_code=str(outcome.get("raw_code", "")),
                evidence_source=str(outcome.get("evidence_source", "")),
                transcript_locator=str(outcome.get("transcript_locator", "")),
                start_confirmed=bool(outcome.get("start_confirmed", False)),
            )
        )
    result.sort(key=lambda item: item["child_id"])
    return tuple(result)


def classify_evidence(evidence: Mapping[str, Any]) -> str:
    """Return the canonical terminal reason implied by one evidence record.

    Checks explicit adverse terminal evidence before success, in the exact
    row order of the plan's Evidence and mapping rules table. Returns
    ``REASON_UNKNOWN`` for a generic/unrecognized/absent signal — callers
    must never guess a cause from timing, role, provider, or transcript
    marker absence.
    """
    if evidence.get("infra_exit_category") == _INFRA_EXIT_CONTEXT_EXHAUSTED:
        return REASON_CONTEXT_EXHAUSTED
    if evidence.get("context_terminal_code") == _CONTEXT_TERMINAL_CODE:
        return REASON_CONTEXT_EXHAUSTED
    if evidence.get("cli_subtype") == _CLI_SUBTYPE_ERROR_MAX_TURNS:
        return REASON_TURN_LIMITED
    if evidence.get("terminal_reason") == _TERMINAL_REASON_MAX_TURNS:
        return REASON_TURN_LIMITED
    if evidence.get("api_terminal_reason") == _API_TERMINAL_REASON_ERROR:
        return REASON_ERROR
    harness_literal = evidence.get("harness_literal")
    if isinstance(harness_literal, str) and HARNESS_API_ERROR_LITERAL in harness_literal:
        return REASON_ERROR
    if evidence.get("confirmed_interrupted"):
        return REASON_INTERRUPTED
    if evidence.get("confirmed_abandoned"):
        return REASON_ABANDONED
    if evidence.get("confirmed_completed"):
        return REASON_COMPLETED
    return REASON_UNKNOWN


def _raw_evidence_fields(evidence: Mapping[str, Any]) -> tuple[str, str, str]:
    """Return ``(raw_reason, raw_subtype, raw_code)`` preserved verbatim from evidence."""
    raw_reason = str(
        evidence.get("terminal_reason")
        or evidence.get("api_terminal_reason")
        or evidence.get("harness_literal")
        or evidence.get("subagent_stop_reason")
        or ""
    )
    raw_subtype = str(evidence.get("cli_subtype") or "")
    raw_code = str(evidence.get("error_code") or evidence.get("context_terminal_code") or "")
    return raw_reason, raw_subtype, raw_code


def _merge_evidence_into_outcome(
    outcome: ChildOutcomeWireDict, evidence: Mapping[str, Any]
) -> ChildOutcomeWireDict:
    """Apply one evidence record to a child's outcome per the idempotent merge rules.

    Known cannot be downgraded by silence (an ``unknown`` classification never
    overwrites an already-known reason); later precise evidence refines
    ``unknown``; conflicting terminal evidence of equal authority collapses to
    one ``unknown`` with both raw reasons retained for diagnosis.
    """
    new_reason = classify_evidence(evidence)
    new_raw_reason, new_raw_subtype, new_raw_code = _raw_evidence_fields(evidence)

    updated = dict(_merge_metadata_into_outcome(outcome, evidence))

    current_reason = outcome["terminal_reason"]
    if new_reason == REASON_UNKNOWN:
        # Still unknown, but the raw evidence (e.g. an unpinned SubagentStop
        # `reason` value) is preserved for diagnosis even though it does not
        # classify — "unknown" must not mean "evidence discarded".
        if current_reason == REASON_UNKNOWN:
            if evidence.get("evidence_source"):
                updated["evidence_source"] = str(evidence["evidence_source"])
            if new_raw_reason:
                updated["raw_reason"] = new_raw_reason
            if new_raw_subtype:
                updated["raw_subtype"] = new_raw_subtype
            if new_raw_code:
                updated["raw_code"] = new_raw_code
        return cast(ChildOutcomeWireDict, updated)

    if current_reason == REASON_UNKNOWN:
        updated["terminal_reason"] = new_reason
        updated["raw_reason"] = new_raw_reason
        updated["raw_subtype"] = new_raw_subtype
        updated["raw_code"] = new_raw_code
        updated["evidence_source"] = str(evidence.get("evidence_source", ""))
    elif current_reason == new_reason:
        pass
    else:
        updated["terminal_reason"] = REASON_UNKNOWN
        updated["raw_reason"] = f"{outcome['raw_reason']} | conflict:{new_raw_reason}"
        updated["raw_subtype"] = f"{outcome['raw_subtype']} | conflict:{new_raw_subtype}"
        updated["evidence_source"] = (
            f"{outcome['evidence_source']} | conflict:{evidence.get('evidence_source', '')}"
        )
    return cast(ChildOutcomeWireDict, updated)


def _merge_metadata_into_outcome(
    outcome: ChildOutcomeWireDict, evidence: Mapping[str, Any]
) -> ChildOutcomeWireDict:
    """Reconcile independently observed metadata without changing terminal evidence."""
    updated = dict(outcome)
    for key in ("role", "attribution_skill", "effective_provider"):
        value = evidence.get(key)
        if isinstance(value, str) and value and not updated.get(key):
            updated[key] = value
    locator = evidence.get("transcript_locator")
    if isinstance(locator, str) and locator and not updated.get("transcript_locator"):
        updated["transcript_locator"] = locator

    conflicts = evidence.get("metadata_conflicts")
    conflict_fields = (
        {item for item in conflicts if isinstance(item, str)}
        if isinstance(conflicts, (list, tuple))
        else set()
    )
    for key in ("effective_model", "effective_effort"):
        if key in conflict_fields:
            updated[key] = ""
            continue
        value = evidence.get(key)
        if isinstance(value, str) and value:
            updated[key] = value
    return cast(ChildOutcomeWireDict, updated)


def _load_or_init_document(
    snapshot_path: Path, *, backend: str, parent_session_id: str
) -> dict[str, Any]:
    document = _read_raw(snapshot_path)
    if not document:
        return {
            "schema_version": CHILD_OUTCOME_SNAPSHOT_SCHEMA_VERSION,
            "backend": backend,
            "parent_session_id": parent_session_id,
            "children": {},
        }
    if document.get("schema_version") != CHILD_OUTCOME_SNAPSHOT_SCHEMA_VERSION:
        raise ChildOutcomeSnapshotError(
            f"unsupported child-outcome snapshot schema_version: "
            f"{document.get('schema_version')!r}"
        )
    document.setdefault("children", {})
    return document


def _write_document(snapshot_path: Path, document: dict[str, Any]) -> None:
    """The one durable-writer call site (registered as such in core's
    DURABLE_ARTIFACT_WRITERS) — every mutator writes through here."""
    _atomic_write(snapshot_path, json.dumps(document, sort_keys=True))


def observe_child(
    snapshot_path: Path,
    *,
    backend: str,
    parent_session_id: str,
    child_id: str,
    launch_alias: str = "",
) -> None:
    """Record a confirmed but not-yet-terminal child row.

    Called on a native child-start event, or when a managed launch reservation
    is promoted to a confirmed process/session start. Idempotent: observing an
    already-known child is a no-op beyond binding a later-known alias.
    """
    with _binding_lock(snapshot_path):
        document = _load_or_init_document(
            snapshot_path, backend=backend, parent_session_id=parent_session_id
        )
        children: dict[str, Any] = document["children"]
        entry = children.get(child_id)
        if entry is None:
            outcome = _empty_outcome(
                child_id=child_id,
                backend=backend,
                parent_session_id=parent_session_id,
                launch_alias=launch_alias,
            )
            outcome["start_confirmed"] = True
            children[child_id] = {"outcome": outcome, "evidence_keys": []}
        else:
            outcome = entry["outcome"]
            outcome["start_confirmed"] = True
            if launch_alias and not outcome.get("launch_alias"):
                outcome["launch_alias"] = launch_alias
        _write_document(snapshot_path, document)


def record_terminal_evidence(
    snapshot_path: Path,
    *,
    backend: str,
    parent_session_id: str,
    child_id: str,
    evidence_key: str,
    evidence: Mapping[str, Any],
    launch_alias: str = "",
) -> str:
    """Merge one child-bound terminal evidence record. Returns the resulting reason.

    Terminal evidence is idempotent by ``evidence_key``
    (backend/child/evidence-source/event-or-tool-use-id). Current metadata is
    reconciled before a known key returns so a repeated observation can restore
    a value that explicit conflict evidence cleared. A start row is created on
    demand if this is the first evidence seen for ``child_id`` (an evidence
    record on its own still confirms the child exists).
    """
    with _binding_lock(snapshot_path):
        document = _load_or_init_document(
            snapshot_path, backend=backend, parent_session_id=parent_session_id
        )
        children: dict[str, Any] = document["children"]
        entry = children.setdefault(
            child_id,
            {
                "outcome": _empty_outcome(
                    child_id=child_id,
                    backend=backend,
                    parent_session_id=parent_session_id,
                    launch_alias=launch_alias,
                ),
                "evidence_keys": [],
            },
        )
        applied_keys: list[str] = entry["evidence_keys"]
        outcome = cast(ChildOutcomeWireDict, dict(entry["outcome"]))
        if evidence_key in applied_keys:
            merged_metadata = _merge_metadata_into_outcome(outcome, evidence)
            if merged_metadata != outcome:
                entry["outcome"] = dict(merged_metadata)
                _write_document(snapshot_path, document)
            return merged_metadata["terminal_reason"]

        outcome["start_confirmed"] = True
        if launch_alias and not outcome.get("launch_alias"):
            outcome["launch_alias"] = launch_alias
        merged = _merge_evidence_into_outcome(outcome, evidence)
        entry["outcome"] = dict(merged)
        applied_keys.append(evidence_key)
        _write_document(snapshot_path, document)
        return merged["terminal_reason"]


def finalize_snapshot_at_session_end(
    snapshot_path: Path,
    *,
    backend: str,
    parent_session_id: str,
) -> None:
    """Finalize the snapshot at parent SessionEnd.

    Every already-known reason is left untouched. Every row still ``unknown``
    stays ``unknown`` — proving only that the parent ended never invents a
    cause. This exists as an explicit reconciliation point (rather than a
    silent no-op) so a future closure-evidence source has one place to plug
    in without re-deriving the read/lock/write sequence.
    """
    with _binding_lock(snapshot_path):
        document = _load_or_init_document(
            snapshot_path, backend=backend, parent_session_id=parent_session_id
        )
        _write_document(snapshot_path, document)
