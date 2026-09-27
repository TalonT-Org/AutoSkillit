"""Core skill name resolution and text-processing helpers.

Zero autoskillit imports outside this sub-package. Provides extract_skill_name,
extract_path_arg, resolve_target_skill, truncate_text, fleet_error, and session_type.
"""

from __future__ import annotations

import json
import operator
import os
import re
import shlex
import warnings
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, assert_never, cast

from ._type_backend import BackendConventions
from ._type_constants import SKILL_COMMAND_PREFIX
from ._type_constants_env import HEADLESS_ENV_VAR, SESSION_TYPE_ENV_VAR
from ._type_constants_registries import FLEET_ERROR_CODES
from ._type_enums import RetryReason, SessionType, SkillSource
from ._type_skill_contract import SkillSourceRef

if TYPE_CHECKING:
    from ._type_protocols_workspace import SkillResolver

__all__ = [
    "is_path_like_token",
    "extract_path_arg",
    "extract_positional_args",
    "extract_skill_name",
    "detect_body_marker",
    "fleet_error",
    "render_target_skill_command",
    "resolve_skill_name",
    "resolve_target_skill",
    "session_type",
    "strip_markdown_code_regions",
    "truncate_text",
    "OutcomeComparison",
    "evaluate_outcome_expression",
    "parse_outcome_expression",
    "RETRY_REASON_DESCRIPTIONS",
    "TERMINAL_FAILURE_POLICY",
]


# Trailing clause shared by every terminal-failure retry reason. Centralizes
# the routing policy that BUDGET_EXHAUSTED, CANCELLED, OUTCOME_INVARIANT,
# OUTCOME_REPORT_MALFORMED, and CONTEXT_EXHAUSTED all share — none of those
# reasons is resumable and none should route to on_context_limit. The
# orchestrator prompt renders the same policy as a bullet; keep both surfaces
# anchored on this single source so a policy edit only lands once.
TERMINAL_FAILURE_POLICY: str = "route on_failure, never on_context_limit, and do not resume"


def _terminal_retry_description(reason_text: str) -> str:
    """Render a description for a retry reason that always routes to on_failure."""
    return f"{reason_text}; {TERMINAL_FAILURE_POLICY}"


RETRY_REASON_DESCRIPTIONS: dict[RetryReason, str] = {
    RetryReason.RESUME: (
        "transient infrastructure failure; Resume is safe with the recorded recovery context"
    ),
    RetryReason.STALE: "start a fresh retry because the prior session is stale",
    RetryReason.NONE: "no retry reason was supplied",
    RetryReason.BUDGET_EXHAUSTED: _terminal_retry_description("budget exhausted"),
    RetryReason.EARLY_STOP: "retry after the early stop using the recorded progress",
    RetryReason.ZERO_WRITES: "retry because the implementation produced no write evidence",
    RetryReason.EMPTY_OUTPUT: "retry because the session exited without output",
    RetryReason.COMPLETED_NO_FLUSH: "retry because completed output was not flushed",
    RetryReason.DRAIN_RACE: "retry after the output drain race",
    RetryReason.PATH_CONTAMINATION: "retry from a clean worktree after path contamination",
    RetryReason.CONTRACT_RECOVERY: "retry using the artifact-contract recovery route",
    RetryReason.CLONE_CONTAMINATION: "retry from an uncontaminated clone",
    RetryReason.THINKING_STALL: "retry after the thinking-only stall",
    RetryReason.IDLE_STALL: "idle timeout; Resume is safe with the existing session",
    RetryReason.RATE_LIMITED: "wait for the rate-limit window and retry",
    RetryReason.CANCELLED: _terminal_retry_description("session cancelled"),
    RetryReason.OUTCOME_INVARIANT: _terminal_retry_description("outcome invariant failed"),
    RetryReason.OUTCOME_REPORT_MALFORMED: _terminal_retry_description("outcome report malformed"),
    RetryReason.ASYNC_OBLIGATION: "retry after resolving the outstanding asynchronous obligation",
    RetryReason.CONTEXT_EXHAUSTED: _terminal_retry_description("context exhausted"),
}

if set(RETRY_REASON_DESCRIPTIONS) != set(RetryReason):
    raise AssertionError("RETRY_REASON_DESCRIPTIONS must cover every RetryReason exactly")

_SKILL_CMD_RE = re.compile(
    r"^/(?:autoskillit:)?([\w-]+)"
)  # anchored: strict leading-slash for extraction
_SKILL_RESOLVE_RE = re.compile(
    r"/(?:autoskillit:)?([\w-]+)"
)  # unanchored: supports "Use /..." prefix forms

_PATH_PREFIXES: tuple[str, ...] = ("/", "./", ".autoskillit/")

_OUTCOME_COMPARISON_RE = re.compile(r"^(\w+)\s*(>=|<=|!=|==|>|<)\s*(\d+)$")
OutcomeOperator = Literal[">", ">=", "==", "!=", "<=", "<"]
_OUTCOME_OPERATORS: dict[OutcomeOperator, Callable[[int, int], bool]] = {
    ">": operator.gt,
    ">=": operator.ge,
    "==": operator.eq,
    "!=": operator.ne,
    "<=": operator.le,
    "<": operator.lt,
}


@dataclass(frozen=True, slots=True)
class OutcomeComparison:
    """One integer comparison in an outcome-contract expression."""

    field_name: str
    operator: OutcomeOperator
    literal: int


def parse_outcome_expression(expression: object) -> tuple[OutcomeComparison, ...] | None:
    """Parse integer comparisons joined by ``and``, or return ``None`` when invalid."""
    if not isinstance(expression, str):
        return None
    comparisons: list[OutcomeComparison] = []
    for conjunct in expression.split(" and "):
        match = _OUTCOME_COMPARISON_RE.match(conjunct.strip())
        if match is None:
            return None
        field_name, operator_name, literal = match.groups()
        comparisons.append(
            OutcomeComparison(
                field_name=field_name,
                operator=cast("OutcomeOperator", operator_name),
                literal=int(literal),
            )
        )
    return tuple(comparisons) if comparisons else None


def evaluate_outcome_expression(
    expression: str,
    fields: Mapping[str, object],
) -> bool | None:
    """Evaluate an expression, returning ``None`` for absent or non-integer fields."""
    comparisons = parse_outcome_expression(expression)
    if comparisons is None:
        return None
    for comparison in comparisons:
        value = fields.get(comparison.field_name)
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        if not _OUTCOME_OPERATORS[comparison.operator](value, comparison.literal):
            return False
    return True


def is_path_like_token(token: str) -> bool:
    return any(token.startswith(p) for p in _PATH_PREFIXES)


def extract_positional_args(skill_command: str) -> list[str]:
    """Extract all positional tokens from a skill_command string.

    Returns tokens after the skill name, tokenized with ``shlex.split`` so
    quoted arguments (including those containing whitespace or newlines)
    remain one logical argument. Unmatched quotes raise ``ValueError``.

    Path-like and non-path tokens are both included, preserving positional
    order.
    """
    stripped = skill_command.strip()
    m = _SKILL_CMD_RE.match(stripped)
    if m is None:
        return []
    remainder = stripped[m.end() :]
    if not remainder:
        return []
    return shlex.split(remainder)


def extract_path_arg(skill_command: str) -> str | None:
    """Extract the first path-like positional argument from a skill_command string.

    Tolerates trailing text (markdown headers, extra tokens, embedded newlines)
    after the path. Returns None if no path-like token is found.
    Strips enclosing quotes from the returned path token.
    """
    stripped = skill_command.strip()
    m = _SKILL_CMD_RE.match(stripped)
    if m is None:
        return None
    tokens = stripped[m.end() :].split()
    for token in tokens:
        cleaned = token.strip('"').strip("'")
        if is_path_like_token(cleaned):
            return cleaned
    return None


def extract_skill_name(skill_command: str) -> str | None:
    """Extract the bare skill name from a skill_command string.

    Handles both ``/autoskillit:make-plan ...`` and ``/make-plan ...`` forms.
    Returns None if the command is not a slash-command.
    """
    m = _SKILL_CMD_RE.match(skill_command.strip())
    return m.group(1) if m else None


def resolve_skill_name(skill_command: str) -> str | None:
    """Extract and validate skill name from command string.

    Handles both ``/name`` and ``/autoskillit:name`` forms. Returns None if
    no match, name contains template expressions, or is followed by a
    bash-style ``{placeholder}`` token.
    """
    stripped = skill_command.strip()
    match = _SKILL_RESOLVE_RE.search(stripped)
    if not match:
        return None
    name = match.group(1)
    if "${{" in name:
        return None
    remainder = stripped[match.end() :]
    if remainder.startswith("{") or remainder.startswith("${{"):
        return None
    return name


def resolve_target_skill(
    skill_command: str,
    resolver: SkillResolver,
    project_root: Path | None,
) -> tuple[str, str | None]:
    """Resolve a skill_command to the correct invocation namespace.

    Returns (resolved_command, skill_name).
    skill_name is None if skill_command is not a slash command.

    - Skills in ``skills/`` (BUNDLED) → ``/autoskillit:name`` namespace
    - Skills in ``skills_extended/`` (BUNDLED_EXTENDED) → ``/name`` namespace
    """
    name = extract_skill_name(skill_command)
    if name is None:
        return skill_command, None

    info = resolver.resolve_effective(name, project_root)
    if info is None or info.invalidities:
        return skill_command, name

    return render_target_skill_command(
        skill_command,
        info.source_ref or info.source,
    ), name


def render_target_skill_command(
    skill_command: str,
    source_ref: SkillSourceRef | SkillSource,
    conventions: BackendConventions | None = None,
) -> str:
    """Render a logical target from its effective source and backend conventions."""
    name = extract_skill_name(skill_command)
    if name is None:
        return skill_command

    source = source_ref.origin if isinstance(source_ref, SkillSourceRef) else source_ref
    configured_sigil = conventions.skill_sigil if conventions is not None else SKILL_COMMAND_PREFIX
    sigil = (
        configured_sigil
        if isinstance(configured_sigil, str) and configured_sigil
        else SKILL_COMMAND_PREFIX
    )
    match source:
        case SkillSource.BUNDLED:
            namespace = "autoskillit:" if sigil == SKILL_COMMAND_PREFIX else ""
        case SkillSource.BUNDLED_EXTENDED | SkillSource.PROJECT_LOCAL | SkillSource.THIRD_PARTY:
            namespace = ""
        case _ as unreachable:
            assert_never(unreachable)
    correct_prefix = f"{sigil}{namespace}{name}"

    # Reconstruct: replace the skill reference, preserve trailing arguments
    stripped = skill_command.strip()
    m = _SKILL_CMD_RE.match(stripped)
    if m is None:
        raise RuntimeError(f"regex failed after extract_skill_name succeeded: {stripped!r}")
    remainder = stripped[m.end() :]
    return correct_prefix + remainder


def truncate_text(text: str, max_len: int = 5000) -> str:
    """Truncate text to max_len, appending a count of truncated chars."""
    if len(text) <= max_len:
        return text
    return f"...[truncated {len(text) - max_len} chars]...\n" + text[-max_len:]


_CODE_BLOCK_RE = re.compile(r"(```|~~~).*?\1", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")


def strip_markdown_code_regions(text: str) -> str:
    """Remove fenced code blocks and inline code spans from markdown text."""
    while True:
        stripped = _CODE_BLOCK_RE.sub("", text)
        stripped = _INLINE_CODE_RE.sub("", stripped)
        if stripped == text:
            return stripped
        text = stripped


def detect_body_marker(body: str, marker: str) -> bool:
    """Check whether *marker* appears in *body* outside markdown code regions."""
    return marker in strip_markdown_code_regions(body)


def fleet_error(
    code: str,
    message: str,
    *,
    details: dict[str, Any] | None = None,
) -> str:
    """Return canonical JSON error envelope for fleet dispatch failures.

    Validates that code is a registered FleetErrorCode. Raises ValueError
    for unregistered codes. The details dict must be JSON-serializable.
    """
    if code not in FLEET_ERROR_CODES:
        msg = f"Unregistered fleet error code: {code!r}"
        raise ValueError(msg)
    return json.dumps(
        {
            "success": False,
            "error": str(code),
            "user_visible_message": message,
            "details": details,
        }
    )


def session_type() -> SessionType:
    """Resolve current session type from AUTOSKILLIT_SESSION_TYPE env var.

    Raises ValueError for the removed 'leaf' alias.
    Fail-closed: returns SKILL on unset or invalid values.
    Transitional bridge: HEADLESS=1 without SESSION_TYPE emits DeprecationWarning.
    """
    raw = os.environ.get(SESSION_TYPE_ENV_VAR, "")
    if raw:
        raw_lower = raw.lower()
        if raw_lower == "leaf":
            raise ValueError(
                "AUTOSKILLIT_SESSION_TYPE='leaf' has been removed. Use 'skill' instead."
            )
        try:
            return SessionType(raw_lower)
        except ValueError:
            valid = ", ".join(m.value for m in SessionType)
            raise ValueError(
                f"AUTOSKILLIT_SESSION_TYPE={raw!r} is not a valid SessionType. "
                f"Valid values: {valid}. "
                f"CLI display labels ('cook', 'order') must not be used here."
            ) from None
    if os.environ.get(HEADLESS_ENV_VAR) == "1":
        warnings.warn(
            f"{HEADLESS_ENV_VAR}=1 without {SESSION_TYPE_ENV_VAR} set. "
            "Defaulting to SKILL. Set AUTOSKILLIT_SESSION_TYPE explicitly.",
            DeprecationWarning,
            stacklevel=2,
        )
    return SessionType.SKILL
