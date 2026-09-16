"""Durable workspace outcome StrEnum discriminators.

Closed vocabularies used by the workspace outcome ledger: outcome kinds
(commit attempt / test run) and the commit-attempt failure classifier.
Split from ``_type_enums_context_admission.py`` so that context-admission
and durable-outcome vocabularies stay colocated with their consumers and
each shard's filename accurately reflects its scope.
"""

from __future__ import annotations

from enum import StrEnum, unique

__all__ = [
    "CommitFailureClass",
    "WorkspaceOutcomeKind",
]


@unique
class WorkspaceOutcomeKind(StrEnum):
    """Durable workspace operations whose outcomes affect later decisions."""

    COMMIT_ATTEMPT = "commit_attempt"
    TEST_RUN = "test_run"


@unique
class CommitFailureClass(StrEnum):
    """Stable classifications for an unsuccessful commit attempt."""

    PATH_REJECTED = "path_rejected"
    GIT_ADD_FAILED = "git_add_failed"
    HOOK_REJECTED = "hook_rejected"
    HOOK_INFRASTRUCTURE = "hook_infrastructure"
    GIT_COMMIT_FAILED = "git_commit_failed"
    TOOLING_MISSING = "tooling_missing"
    UNHANDLED = "unhandled"
