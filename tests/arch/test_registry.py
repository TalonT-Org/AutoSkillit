"""Symbolic rule registry tests.

Tests the RuleDescriptor dataclass, RULES tuple, and Violation NamedTuple
that form the symbolic registry of architecture enforcement rules.
"""

from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError, dataclass
from pathlib import Path
from typing import NamedTuple

import pytest

# ── Shared infrastructure (must be in-file for tests that use RULES/Violation) ─


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
        exemptions=frozenset({"app.py", "_doctor.py"}),
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
        exemptions=frozenset(),
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
        exemptions=frozenset({"process.py"}),
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
)

_LOGGER_METHODS = frozenset({"debug", "info", "warning", "error", "critical", "exception"})
_PRINT_EXEMPT = frozenset({"app.py", "_doctor.py", "quota_check.py", "remove_clone_guard.py"})
_BROAD_EXCEPTION_TYPES: frozenset[str] = frozenset({"Exception", "BaseException"})
_BROAD_EXCEPT_EXEMPT = frozenset({"quota_check.py", "remove_clone_guard.py"})
_ASYNCIO_PIPE_EXEMPT: frozenset[str] = frozenset({"process.py"})
_SENSITIVE_KEYWORDS = frozenset({"token", "secret", "password", "key", "api_key", "auth"})
_RULE: dict[str, RuleDescriptor] = {r.rule_id: r for r in RULES}

SRC_ROOT = Path(__file__).parent.parent.parent / "src" / "autoskillit"


def _has_log_call(body: list[ast.stmt]) -> bool:
    """Return True if body contains any logger.<method>(…) call."""
    for node in ast.walk(ast.Module(body=body, type_ignores=[])):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in _LOGGER_METHODS
        ):
            return True
    return False


def _has_reraise(body: list[ast.stmt]) -> bool:
    """Return True if body contains any raise statement (re-raise pattern)."""
    for node in ast.walk(ast.Module(body=body, type_ignores=[])):
        if isinstance(node, ast.Raise):
            return True
    return False


class ArchitectureViolationVisitor(ast.NodeVisitor):
    def __init__(self, filepath: Path) -> None:
        self.filepath = filepath
        self.violations: list[Violation] = []
        self._print_exempt = filepath.name in _PRINT_EXEMPT
        self._asyncio_pipe_exempt = filepath.name in _ASYNCIO_PIPE_EXEMPT
        self._broad_except_exempt = filepath.name in _BROAD_EXCEPT_EXEMPT

    def _add(self, node: ast.AST, rule: RuleDescriptor, message: str) -> None:
        self.violations.append(
            Violation(
                self.filepath,
                node.lineno,  # type: ignore[attr-defined]
                node.col_offset,  # type: ignore[attr-defined]
                message,
                rule.rule_id,
                rule.lens,
            )
        )

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if (
            not self._asyncio_pipe_exempt
            and node.attr == "PIPE"
            and isinstance(node.value, ast.Name)
            and node.value.id == "asyncio"
        ):
            self._add(
                node,
                _RULE["ARCH-004"],
                "asyncio.PIPE is banned; use create_temp_io() from process_lifecycle",
            )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if not self._print_exempt and isinstance(node.func, ast.Name) and node.func.id == "print":
            self._add(node, _RULE["ARCH-001"], "print() call — use logger instead")
        if isinstance(node.func, ast.Attribute) and node.func.attr in _LOGGER_METHODS:
            for kw in node.keywords:
                if kw.arg and any(s in kw.arg.lower() for s in _SENSITIVE_KEYWORDS):
                    self._add(
                        node, _RULE["ARCH-002"], f"sensitive kwarg '{kw.arg}' passed to logger"
                    )
        func = node.func
        func_name = func.id if isinstance(func, ast.Name) else None
        if func_name == "get_logger" and node.args:
            first_arg = node.args[0]
            if not (isinstance(first_arg, ast.Name) and first_arg.id == "__name__"):
                self._add(
                    node,
                    _RULE["ARCH-005"],
                    "get_logger() must be called with __name__, not a literal or other value",
                )
        if isinstance(func, ast.Attribute) and func.attr in _LOGGER_METHODS:
            for arg in node.args:
                if isinstance(arg, ast.JoinedStr):
                    for fv in ast.walk(arg):
                        if isinstance(fv, ast.FormattedValue):
                            val = fv.value
                            var_name = None
                            if isinstance(val, ast.Name):
                                var_name = val.id
                            elif isinstance(val, ast.Attribute):
                                var_name = val.attr
                            if var_name and any(
                                kw in var_name.lower() for kw in _SENSITIVE_KEYWORDS
                            ):
                                self._add(
                                    node,
                                    _RULE["ARCH-006"],
                                    f"f-string log message interpolates sensitive variable "
                                    f"'{var_name}' — use structlog kwargs instead",
                                )
        self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        is_broad = node.type is None or (
            isinstance(node.type, ast.Name) and node.type.id in _BROAD_EXCEPTION_TYPES
        )
        if (
            is_broad
            and not self._broad_except_exempt
            and not _has_log_call(node.body)
            and not _has_reraise(node.body)
        ):
            type_label = ast.unparse(node.type) if node.type else "bare except"
            self._add(
                node,
                _RULE["ARCH-003"],
                f"broad except ({type_label}) without any logger call"
                " — add logger.warning/error with exc_info=True",
            )
        self.generic_visit(node)


def _scan(path: Path) -> list[Violation]:
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return [Violation(path, exc.lineno or 0, 0, f"SyntaxError: {exc.msg}")]
    visitor = ArchitectureViolationVisitor(filepath=path)
    visitor.visit(tree)
    return visitor.violations


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_rule_descriptor_is_frozen_dataclass() -> None:
    """REQ-SYMB-001: RuleDescriptor is a frozen dataclass with all required fields."""
    rd = RuleDescriptor(
        rule_id="ARCH-TEST",
        name="test-rule",
        lens="operational",
        description="A test rule.",
        rationale="Test rationale.",
        exemptions=frozenset(),
        severity="error",
        defense_standard=None,
        adr_ref=None,
    )
    assert rd.rule_id == "ARCH-TEST"
    assert rd.name == "test-rule"
    assert rd.lens == "operational"
    assert rd.exemptions == frozenset()
    assert rd.severity == "error"
    assert rd.defense_standard is None
    assert rd.adr_ref is None
    # Verify frozen (immutable)
    with pytest.raises((FrozenInstanceError, AttributeError)):
        rd.rule_id = "MODIFIED"  # type: ignore[misc]


def test_rule_registry_completeness() -> None:
    """REQ-SYMB-006: RULES is complete, non-duplicated, and lens-valid."""
    _KNOWN_LENSES = frozenset(
        {
            "c4-container",
            "module-dependency",
            "process-flow",
            "concurrency",
            "state-lifecycle",
            "error-resilience",
            "security",
            "repository-access",
            "data-lineage",
            "scenarios",
            "deployment",
            "operational",
            "development",
        }
    )
    # (a) all rule_id values are unique
    rule_ids = [r.rule_id for r in RULES]
    assert len(rule_ids) == len(set(rule_ids)), f"Duplicate rule_ids: {rule_ids}"

    # (b) all lens values are from the 13-lens vocabulary
    for r in RULES:
        assert r.lens in _KNOWN_LENSES, (
            f"Rule {r.rule_id} has unknown lens {r.lens!r}. Known: {sorted(_KNOWN_LENSES)}"
        )

    # (c) count equals the number of distinct rules enforced by ArchitectureViolationVisitor
    assert len(RULES) == 6, (
        f"RULES has {len(RULES)} entries but visitor enforces 6 rules. "
        "Add a RuleDescriptor for every new visitor rule."
    )

    # (c cont.) exact set of IDs must match the visitor's rule set
    expected_ids = frozenset(
        {"ARCH-001", "ARCH-002", "ARCH-003", "ARCH-004", "ARCH-005", "ARCH-006"}
    )
    actual_ids = frozenset(rule_ids)
    assert actual_ids == expected_ids, (
        f"RULES ID mismatch. Missing: {expected_ids - actual_ids}. "
        f"Extra: {actual_ids - expected_ids}"
    )


def test_all_rules_have_defense_standard() -> None:
    """P13 LOW: every entry in RULES must declare a defense_standard.

    Prevents future @semantic_rule additions from silently omitting
    the defense_standard field, which would break audit-defense-standards
    traceability.
    """
    missing = [r.rule_id for r in RULES if r.defense_standard is None]
    assert not missing, (
        f"RULES entries missing defense_standard: {missing}. "
        "Every architectural rule must trace to a defense standard."
    )


def test_violation_has_rule_id_and_lens_fields() -> None:
    """REQ-SYMB-003: Violation gains rule_id and lens while preserving 4 original fields."""
    v = Violation(
        file=Path("x.py"),
        line=1,
        col=0,
        message="msg",
        rule_id="ARCH-001",
        lens="operational",
    )
    assert v.rule_id == "ARCH-001"
    assert v.lens == "operational"
    # Original 4 fields preserved
    assert v.file == Path("x.py")
    assert v.line == 1
    assert v.col == 0
    assert v.message == "msg"


def test_violation_rule_id_lens_default_to_empty(tmp_path: Path) -> None:
    """Violation with only 4 args has rule_id='' and lens='' (backward-compatible construction)."""
    v = Violation(file=tmp_path / "x.py", line=1, col=0, message="SyntaxError: bad")
    assert v.rule_id == ""
    assert v.lens == ""


def test_add_populates_rule_id_and_lens(tmp_path: Path) -> None:
    """REQ-SYMB-004: _add() creates Violations with rule_id and lens from the RuleDescriptor."""
    f = tmp_path / "bad.py"
    f.write_text("print('hello')\n")
    violations = _scan(f)
    print_violations = [v for v in violations if "print" in v.message]
    assert print_violations, "Expected a print() violation"
    v = print_violations[0]
    assert v.rule_id == "ARCH-001"
    assert v.lens == "operational"


def test_violation_str_includes_rule_and_lens_prefix(tmp_path: Path) -> None:
    """REQ-SYMB-005: str(Violation) includes [ARCH-XXX / lens] as the leading element."""
    f = tmp_path / "bad.py"
    f.write_text("print('hello')\n")
    violations = _scan(f)
    print_violations = [v for v in violations if "print" in v.message]
    assert print_violations
    s = str(print_violations[0])
    assert s.startswith("[ARCH-001 / operational"), (
        f"Expected '[ARCH-001 / operational...' prefix, got: {s!r}"
    )


def test_violation_str_includes_defense_standard_when_present(tmp_path: Path) -> None:
    """REQ-SYMB-005: defense_standard appears in str(Violation) when the rule has one."""
    f = tmp_path / "bad.py"
    f.write_text("print('hello')\n")
    violations = _scan(f)
    print_violations = [v for v in violations if "print" in v.message]
    assert print_violations
    s = str(print_violations[0])
    # ARCH-001 has defense_standard="DS-003"
    assert "DS-003" in s, f"Expected 'DS-003' in violation string, got: {s!r}"


def test_violation_str_omits_defense_standard_when_absent(tmp_path: Path) -> None:
    """REQ-SYMB-005: defense_standard is absent from str(Violation) when rule has none.

    Uses a Violation with a rule_id not present in RULES so that the rule lookup
    returns None and ds_part evaluates to "".
    """
    f = tmp_path / "bad.py"
    v = Violation(
        file=f,
        line=1,
        col=0,
        message="asyncio.PIPE used directly",
        rule_id="ARCH-UNKNOWN",
        lens="process-flow",
    )
    s = str(v)
    assert "[ARCH-UNKNOWN / process-flow]" in s, (
        f"Expected '[ARCH-UNKNOWN / process-flow]' prefix, got: {s!r}"
    )
    assert "DS-" not in s, f"Unexpected defense_standard in output: {s!r}"


def test_arch004_violation_str_includes_ds002(tmp_path: Path) -> None:
    """Regression guard: ARCH-004 violation str must include DS-002 after the fix."""
    f = tmp_path / "bad.py"
    f.write_text("import asyncio\nval = asyncio.PIPE\n")
    violations = _scan(f)
    pipe_violations = [v for v in violations if "asyncio.PIPE" in v.message]
    assert pipe_violations
    s = str(pipe_violations[0])
    assert "DS-002" in s
    assert "[ARCH-004 / process-flow / DS-002]" in s


def test_violation_str_no_prefix_without_rule_id() -> None:
    """Violation with empty rule_id uses the legacy str format (no prefix)."""
    v = Violation(file=Path("src/x.py"), line=5, col=0, message="some issue", rule_id="", lens="")
    s = str(v)
    assert not s.startswith("["), f"Expected no prefix for rule_id='', got: {s!r}"
    assert "some issue" in s
