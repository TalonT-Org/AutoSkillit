"""Shared type contracts for MCP tool responses.

Every discriminator field in MCP tool JSON responses has a corresponding
StrEnum here. Server code uses enum members to construct responses.
Tests import these enums to validate responses. This eliminates silent
misclassification from string typos or unhandled values.
"""

from __future__ import annotations

from enum import StrEnum


class RetryReason(StrEnum):
    RESUME = "resume"
    NONE = "none"


class MergeFailedStep(StrEnum):
    TEST_GATE = "test_gate"
    FETCH = "fetch"
    REBASE = "rebase"
    MERGE = "merge"


class MergeState(StrEnum):
    WORKTREE_INTACT = "worktree_intact"
    WORKTREE_INTACT_REBASE_ABORTED = "worktree_intact_rebase_aborted"
    MAIN_REPO_MERGE_ABORTED = "main_repo_merge_aborted"


class RestartScope(StrEnum):
    FULL_RESTART = "full_restart"
    PARTIAL_RESTART = "partial_restart"


class SkillSource(StrEnum):
    BUNDLED = "bundled"


class WorkflowSource(StrEnum):
    PROJECT = "project"
    BUILTIN = "builtin"


# Known field names in run_skill_retry response — used by workflow validation
RETRY_RESPONSE_FIELDS: frozenset[str] = frozenset(
    {
        "result",
        "session_id",
        "subtype",
        "is_error",
        "exit_code",
        "needs_retry",
        "retry_reason",
    }
)
