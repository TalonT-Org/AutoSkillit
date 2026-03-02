"""Canonical source for shared arch-test types, exempt sets, and RULES tuple.

Both test_ast_rules and test_registry import from this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(Path(__file__).parent.parent.parent))
    except ValueError:
        return str(path)


class Violation(NamedTuple):
    file: Path
    line: int
    col: int
    message: str
    rule_id: str = ""
    lens: str = ""

    def __str__(self) -> str:
        if not self.rule_id:
            return f"{_rel(self.file)}:{self.line}:{self.col}: {self.message}"
        rule = next((r for r in RULES if r.rule_id == self.rule_id), None)
        ds_part = f" / {rule.defense_standard}" if rule and rule.defense_standard else ""
        loc = f"{_rel(self.file)}:{self.line}:{self.col}"
        return f"[{self.rule_id} / {self.lens}{ds_part}] {loc}: {self.message}"


@dataclass(frozen=True)
class RuleDescriptor:
    """Metadata for a single AST-enforced architecture rule."""

    rule_id: str
    name: str
    lens: str
    description: str
    rationale: str
    exemptions: frozenset[str]
    severity: str
    defense_standard: str | None = None
    adr_ref: str | None = None


# ── Canonical exempt sets ─────────────────────────────────────────────────────

_SENSITIVE_KEYWORDS = frozenset({"token", "secret", "password", "key", "api_key", "auth"})
_LOGGER_METHODS = frozenset({"debug", "info", "warning", "error", "critical", "exception"})

_PRINT_EXEMPT = frozenset(
    {
        "app.py",
        "_doctor.py",
        "_marketplace.py",
        "quota_check.py",
        "remove_clone_guard.py",
        "skill_cmd_check.py",
        "skill_command_guard.py",
    }
)

# Standalone hook scripts: fail-open design requires silent broad excepts and print() for JSON
_BROAD_EXCEPT_EXEMPT = frozenset(
    {
        "quota_check.py",
        "remove_clone_guard.py",
        "skill_cmd_check.py",
        "skill_command_guard.py",
    }
)

_ASYNCIO_PIPE_EXEMPT: frozenset[str] = frozenset({"process.py"})

# ARCH-007: Functions that check TerminationReason as sequential early-exit guards
# (single-value checks), not as dispatch tables (≥2 values). Exempt from ARCH-007.
_DISPATCH_TABLE_EXEMPT_FUNCTIONS = frozenset(
    {
        "_build_skill_result",  # sequential early-exit guards, not a dispatch table
    }
)

# ── RULES tuple — 7 entries ───────────────────────────────────────────────────

RULES: tuple[RuleDescriptor, ...] = (
    RuleDescriptor(
        rule_id="ARCH-001",
        name="no-print",
        lens="operational",
        description="Production modules must not call print(); use structured logger instead.",
        rationale=(
            "AutoSkillit routes all output through MCP tool results and Claude CLI stdout. "
            "print() calls emit directly to stdout, polluting the JSON stream that headless "
            "sessions depend on for structured result parsing. The operational lens governs "
            "observability contracts; uncontrolled stdout corrupts the MCP communication protocol."
        ),
        exemptions=_PRINT_EXEMPT,
        severity="error",
        defense_standard="DS-003",
    ),
    RuleDescriptor(
        rule_id="ARCH-002",
        name="no-sensitive-logger-kwargs",
        lens="security",
        description="Sensitive values must not be passed as keyword arguments to logger calls.",
        rationale=(
            "Structured logging with sensitive kwargs (token, secret, password, key) persists "
            "credentials in log files, structlog output, or monitoring systems. AutoSkillit tools "
            "handle API keys and auth tokens for headless Claude sessions; accidental logging of "
            "these values via structlog kwargs creates audit-trail and credential-leak risks."
        ),
        exemptions=frozenset(),
        severity="error",
        defense_standard="DS-006",
    ),
    RuleDescriptor(
        rule_id="ARCH-003",
        name="no-silent-broad-except",
        lens="error-resilience",
        description=(
            "Broad except clauses must log the error or re-raise; silent swallowing is forbidden."
        ),
        rationale=(
            "AutoSkillit orchestrates multi-step pipelines where silent failure "
            "propagates corrupt state across recipe steps, worktrees, and headless "
            "sessions. Silent broad-except in "
            "the execution or merge path causes spurious PASS results to be reported upstream. "
            "The error-resilience lens mandates observable failures at all levels of the stack."
        ),
        exemptions=_BROAD_EXCEPT_EXEMPT,
        severity="error",
        defense_standard="DS-001",
    ),
    RuleDescriptor(
        rule_id="ARCH-004",
        name="no-asyncio-PIPE",
        lens="process-flow",
        description=(
            "asyncio.PIPE must not be used directly; "
            "route subprocess I/O through create_temp_io() from process_lifecycle instead."
        ),
        rationale=(
            "asyncio.PIPE causes OS pipe-buffer blocking when subprocess output exceeds 64 KB — "
            "a common occurrence with Claude CLI stdout containing full session JSON. "
            "create_temp_io() redirects to RAM-backed temp files, eliminating buffer deadlock in "
            "the process-flow path. Direct asyncio.PIPE usage outside process_lifecycle.py "
            "bypasses this protection."
        ),
        exemptions=_ASYNCIO_PIPE_EXEMPT,
        severity="error",
        defense_standard="DS-002",
    ),
    RuleDescriptor(
        rule_id="ARCH-005",
        name="get-logger-name",
        lens="operational",
        description=(
            "get_logger() must always be called with __name__ to ensure correct logger hierarchy."
        ),
        rationale=(
            "AutoSkillit uses structlog routed through a package-level NullHandler for stdlib "
            "compatibility. Logger hierarchy relies on __name__ for correct propagation through "
            "autoskillit.*. Literal or computed names break filtering, sampling, and structured "
            "log context. The operational lens requires that observability infrastructure is "
            "self-consistent."
        ),
        exemptions=frozenset(),
        severity="error",
        defense_standard="DS-005",
    ),
    RuleDescriptor(
        rule_id="ARCH-006",
        name="no-fstring-secrets",
        lens="security",
        description=(
            "Sensitive variable names must not be interpolated into "
            "f-string logger positional arguments."
        ),
        rationale=(
            "f-string interpolation of sensitive variables in logger messages embeds the value in "
            "the rendered string before structlog can apply masking or filtering. AutoSkillit's "
            "headless sessions handle API keys and auth tokens; accidental f-string log "
            "interpolation creates credential-exposure vectors in Claude CLI stdout, structured "
            "session output, and any downstream log aggregation."
        ),
        exemptions=frozenset(),
        severity="error",
        defense_standard="DS-006",
    ),
    RuleDescriptor(
        rule_id="ARCH-007",
        name="termination-dispatch-exhaustive",
        lens="process-flow",
        description=(
            "TerminationReason and ChannelConfirmation enum dispatch must use "
            "match/case + assert_never, not if/elif chains"
        ),
        rationale=(
            "Exhaustive dispatch via assert_never guarantees that adding a new enum variant "
            "produces a static type error rather than silent mis-routing at runtime"
        ),
        exemptions=_DISPATCH_TABLE_EXEMPT_FUNCTIONS,
        severity="high",
        defense_standard="DS-007",
    ),
)

_RULE: dict[str, RuleDescriptor] = {r.rule_id: r for r in RULES}
