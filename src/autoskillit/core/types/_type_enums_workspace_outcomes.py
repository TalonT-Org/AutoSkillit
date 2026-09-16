"""Workspace outcome discriminators."""

from enum import StrEnum

__all__ = ["CommitFailureClass", "WorkspaceOutcomeKind"]


class WorkspaceOutcomeKind(StrEnum):
    """Durable workspace operations whose outcomes affect later decisions."""

    COMMIT_ATTEMPT = "commit_attempt"
    TEST_RUN = "test_run"


class CommitFailureClass(StrEnum):
    """Stable classifications for an unsuccessful commit attempt."""

    PATH_REJECTED = "path_rejected"
    GIT_ADD_FAILED = "git_add_failed"
    HOOK_REJECTED = "hook_rejected"
    HOOK_INFRASTRUCTURE = "hook_infrastructure"
    GIT_COMMIT_FAILED = "git_commit_failed"
    TOOLING_MISSING = "tooling_missing"
    UNHANDLED = "unhandled"
