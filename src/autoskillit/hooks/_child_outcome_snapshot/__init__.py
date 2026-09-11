"""Stdlib-only durable snapshot of every observed L0 child run's terminal reason.

Pure re-export facade — see ``_snapshot.py`` for the implementation and full
module documentation. Kept a bare facade (no logic at module scope) because
sub-package ``__init__.py`` files in this repository must be pure re-export
facades (see ``tests/arch/test_ast_rules.py::test_init_files_are_pure_facades``).
"""

from ._snapshot import _CLI_SUBTYPE_ERROR_MAX_TURNS as _CLI_SUBTYPE_ERROR_MAX_TURNS
from ._snapshot import _INFRA_EXIT_CONTEXT_EXHAUSTED as _INFRA_EXIT_CONTEXT_EXHAUSTED
from ._snapshot import (
    CANONICAL_TERMINAL_REASONS,
    CHILD_OUTCOME_SNAPSHOT_SCHEMA_VERSION,
    HARNESS_API_ERROR_LITERAL,
    REASON_ABANDONED,
    REASON_COMPLETED,
    REASON_CONTEXT_EXHAUSTED,
    REASON_ERROR,
    REASON_INTERRUPTED,
    REASON_TURN_LIMITED,
    REASON_UNKNOWN,
    ChildOutcomeSnapshotError,
    ChildOutcomeWireDict,
    classify_evidence,
    observe_child,
    project_outcomes,
    read_snapshot,
    reconcile_ended_children,
    record_terminal_evidence,
    resolve_child_outcome_log_root,
    resolve_snapshot_path,
)

__all__ = [
    "CANONICAL_TERMINAL_REASONS",
    "CHILD_OUTCOME_SNAPSHOT_SCHEMA_VERSION",
    "HARNESS_API_ERROR_LITERAL",
    "REASON_ABANDONED",
    "REASON_COMPLETED",
    "REASON_CONTEXT_EXHAUSTED",
    "REASON_ERROR",
    "REASON_INTERRUPTED",
    "REASON_TURN_LIMITED",
    "REASON_UNKNOWN",
    "ChildOutcomeSnapshotError",
    "ChildOutcomeWireDict",
    "classify_evidence",
    "observe_child",
    "project_outcomes",
    "read_snapshot",
    "reconcile_ended_children",
    "record_terminal_evidence",
    "resolve_child_outcome_log_root",
    "resolve_snapshot_path",
]
