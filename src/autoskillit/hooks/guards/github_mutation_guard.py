#!/usr/bin/env python3
"""Deny unsafe raw GitHub mutations at Bash and run_cmd boundaries.

The structured ``post_pr_review`` tool is the sole pull-request review
publication authority. Other raw GitHub writes are allowed only when this
guard can prove that the command issues exactly one non-review mutation.

stdlib-only; no autoskillit imports.
"""

from __future__ import annotations

import json
import logging
import sys
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple, NoReturn

_HOOKS_DIR = str(Path(__file__).resolve().parent.parent)
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

if TYPE_CHECKING:
    from autoskillit.hooks._github_mutation_analysis import (
        GitHubMutationKind,
        GitHubMutationStatus,
        analyze_github_mutations,
    )
    from autoskillit.hooks._hook_payload import (
        ParsedHookCommand,
        PayloadAnomaly,
        parse_hook_command,
    )
else:
    from _github_mutation_analysis import (  # noqa: E402
        GitHubMutationKind,
        GitHubMutationStatus,
        analyze_github_mutations,
    )
    from _hook_payload import (  # noqa: E402
        ParsedHookCommand,
        PayloadAnomaly,
        parse_hook_command,
    )

_REVIEW_KINDS: frozenset[GitHubMutationKind] = frozenset(
    {
        GitHubMutationKind.PULL_REVIEW,
        GitHubMutationKind.PULL_REVIEW_COMMENT,
        GitHubMutationKind.PULL_REVIEW_REPLY,
        GitHubMutationKind.GRAPHQL_REVIEW,
    }
)

GITHUB_MUTATION_DENY_TRIGGER: str = "Unsafe raw GitHub mutation is prohibited"
_LOGGER = logging.getLogger(__name__)  # noqa: TID251 - standalone stdlib guard


class DenyTrigger(StrEnum):
    """Exhaustive machine-readable reasons this guard denies a command."""

    FIELD_CONFUSION = "field_confusion"
    MALFORMED_COMMAND = "malformed_command"
    MALFORMED_CWD = "malformed_cwd"
    UNRESOLVED_MUTATION = "unresolved_mutation"
    CLASSIFIER_INTERNAL_ERROR = "classifier_internal_error"
    MULTIPLE_MUTATIONS = "multiple_mutations"
    REVIEW_MUTATION = "review_mutation"


class GuardDecision(NamedTuple):
    """The guard's outcome for one command, plus the trigger that produced it."""

    allow: bool
    trigger: DenyTrigger | None
    reason_code: str


_POST_PR_REVIEW_POINTER = (
    "Use post_pr_review for pull-request review publication, or the appropriate "
    "structured AutoSkillit mutation tool."
)

_DENY_MESSAGES: dict[DenyTrigger, str] = {
    DenyTrigger.FIELD_CONFUSION: (
        "field_confusion: the hook payload carries both the Bash and run_cmd "
        "command fields, or a stray cwd field inside a Bash tool_input, so which "
        "text executes is ambiguous. Fail closed rather than guess."
    ),
    DenyTrigger.MALFORMED_COMMAND: (
        "malformed_command: the payload's command field is missing or not a "
        "string, so no command text could be inspected."
    ),
    DenyTrigger.MALFORMED_CWD: (
        "malformed_cwd: the command's execution cwd is non-string or relative, "
        "so path-sensitive mutation inputs cannot be inspected safely."
    ),
    DenyTrigger.UNRESOLVED_MUTATION: (
        "unresolved_mutation: this command's GitHub mutation cardinality or "
        "target cannot be statically proven safe. Rewrite it as exactly one literal "
        "non-review mutation; use post_pr_review only for review publication. "
        "Unresolved mutation commands fail closed."
    ),
    DenyTrigger.CLASSIFIER_INTERNAL_ERROR: (
        "classifier_internal_error: GitHub mutation classification failed "
        "unexpectedly. The command was denied without exposing runtime details."
    ),
    DenyTrigger.MULTIPLE_MUTATIONS: (
        "multiple_mutations: this command issues more than one GitHub mutation "
        f"request. {_POST_PR_REVIEW_POINTER} Multiple writes fail closed."
    ),
    DenyTrigger.REVIEW_MUTATION: (
        "review_mutation: raw pull-request review publication is prohibited. "
        f"{_POST_PR_REVIEW_POINTER}"
    ),
}


def decide(parsed: ParsedHookCommand) -> GuardDecision:
    """Pure decision function: payload facts plus mutation analysis, no I/O.

    Ordering mirrors decide_response_conformance's ordered pure-function
    shape: structural payload defects (missing command, field confusion)
    are checked before any command-content classification runs.
    """
    if parsed.tool_kind not in ("bash", "run_cmd"):
        return GuardDecision(allow=True, trigger=None, reason_code="")
    if parsed.command is None:
        return GuardDecision(
            allow=False,
            trigger=DenyTrigger.MALFORMED_COMMAND,
            reason_code="",
        )
    if PayloadAnomaly.FIELD_CONFUSION in parsed.anomalies:
        return GuardDecision(allow=False, trigger=DenyTrigger.FIELD_CONFUSION, reason_code="")
    if any(
        anomaly in parsed.anomalies
        for anomaly in (PayloadAnomaly.NON_STRING_CWD, PayloadAnomaly.RELATIVE_CWD)
    ):
        return GuardDecision(allow=False, trigger=DenyTrigger.MALFORMED_CWD, reason_code="")

    analysis = analyze_github_mutations(parsed.command, cwd=parsed.execution_cwd)

    if analysis.status is GitHubMutationStatus.MULTIPLE:
        return GuardDecision(
            allow=False,
            trigger=DenyTrigger.MULTIPLE_MUTATIONS,
            reason_code="",
        )
    if analysis.status is GitHubMutationStatus.UNRESOLVED:
        return GuardDecision(
            allow=False,
            trigger=DenyTrigger.UNRESOLVED_MUTATION,
            reason_code=analysis.reason_code,
        )
    if any(record.kind in _REVIEW_KINDS for record in analysis.mutations):
        return GuardDecision(allow=False, trigger=DenyTrigger.REVIEW_MUTATION, reason_code="")
    return GuardDecision(allow=True, trigger=None, reason_code="")


def _deny(trigger: DenyTrigger, reason_code: str) -> NoReturn:
    reason = _DENY_MESSAGES[trigger]
    if trigger is DenyTrigger.UNRESOLVED_MUTATION:
        reason = f"{reason} classifier_code={reason_code or 'unclassified_uncertainty'}"
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
    sys.stdout.write(json.dumps(payload) + "\n")
    raise SystemExit(0)


def main() -> None:
    try:
        loaded = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, OSError):
        raise SystemExit(0)
    if not isinstance(loaded, dict):
        raise SystemExit(0)

    try:
        decision = decide(parse_hook_command(loaded))
    except Exception:
        _LOGGER.error("GitHub mutation classification failed", exc_info=True)
        _deny(DenyTrigger.CLASSIFIER_INTERNAL_ERROR, "")
    if not decision.allow and decision.trigger is not None:
        _deny(decision.trigger, decision.reason_code)
    raise SystemExit(0)


if __name__ == "__main__":
    main()
