"""Shared type contracts for MCP tool responses.

Every discriminator field in MCP tool JSON responses has a corresponding
StrEnum here. Server code uses enum members to construct responses.
Tests import these enums to validate responses. This eliminates silent
misclassification from string typos or unhandled values.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Generic, TypeVar

T = TypeVar("T")


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


@dataclass
class LoadReport:
    """A single file that failed to load, with the reason."""

    path: Path
    error: str


@dataclass
class LoadResult(Generic[T]):
    """Discovery result: successfully loaded items + error reports."""

    items: list[T]
    errors: list[LoadReport] = field(default_factory=list)


# The substring Claude CLI emits when the context window is full.
# Used by ClaudeSessionResult._is_context_exhausted() for detection.
# Centralized here so tests can reference the canonical value.
CONTEXT_EXHAUSTION_MARKER = "prompt is too long"

# Native Claude Code tools that pipeline orchestrators must NEVER use directly.
# Canonical source of truth — imported by server.py, semantic_rules.py, and tests.
PIPELINE_FORBIDDEN_TOOLS: tuple[str, ...] = (
    "Read",
    "Grep",
    "Glob",
    "Edit",
    "Write",
    "Bash",
    "Task",
    "Explore",
    "WebFetch",
    "WebSearch",
    "NotebookEdit",
)

# Known field names in run_skill_retry response — used by workflow validation
RETRY_RESPONSE_FIELDS: frozenset[str] = frozenset(
    {
        "success",
        "result",
        "session_id",
        "subtype",
        "is_error",
        "exit_code",
        "needs_retry",
        "retry_reason",
        "stderr",
    }
)
