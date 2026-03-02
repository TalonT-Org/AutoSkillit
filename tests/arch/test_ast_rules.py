"""Architectural enforcement: AST-based visitor rules (ARCH-001 through ARCH-007).

Rules enforced here (compile-time, no execution required):
  1. No print() calls in production code
  2. No sensitive keyword arguments passed to logger calls
  3. No broad except without logger call or re-raise
  4. asyncio.PIPE ban outside process.py
  5. get_logger() must be called with __name__
  6. No f-string interpolation of sensitive variables in logger positional args
  7. Exhaustive TerminationReason dispatch (match/case + assert_never)

Note: `import logging` and `logging.getLogger()` are enforced by ruff TID251
at pre-commit time (see pyproject.toml [tool.ruff.lint.flake8-tidy-imports]).
Those rules belong in the toolchain, not duplicated here.

Exemptions:
  - cli/app.py, cli/_doctor.py: may use print() for user-facing terminal output
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

import pytest

SRC_ROOT = Path(__file__).parent.parent.parent / "src" / "autoskillit"

_SENSITIVE_KEYWORDS = frozenset({"token", "secret", "password", "key", "api_key", "auth"})
_LOGGER_METHODS = frozenset({"debug", "info", "warning", "error", "critical", "exception"})
_PRINT_EXEMPT = frozenset({"app.py", "_doctor.py", "quota_check.py", "remove_clone_guard.py"})
_BROAD_EXCEPTION_TYPES: frozenset[str] = frozenset({"Exception", "BaseException"})
# Standalone hook scripts: fail-open design requires silent broad excepts and print() for JSON
_BROAD_EXCEPT_EXEMPT = frozenset({"quota_check.py", "remove_clone_guard.py"})

# ARCH-007: Functions that check TerminationReason as sequential early-exit guards
# (single-value checks), not as dispatch tables (≥2 values). Exempt from ARCH-007.
_DISPATCH_TABLE_EXEMPT_FUNCTIONS: frozenset[str] = frozenset(
    {
        "_build_skill_result",  # sequential early-exit guards, not a dispatch table
    }
)


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

_RULE: dict[str, RuleDescriptor] = {r.rule_id: r for r in RULES}

# ── Rule 5 (visitor): asyncio.PIPE ban ────────────────────────────────────────
_ASYNCIO_PIPE_EXEMPT: frozenset[str] = frozenset({"process.py"})


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
        """Rule ARCH-004 (visitor): asyncio.PIPE is banned outside process.py."""
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
        # Rule ARCH-001 (visitor): no print() — ruff cannot enforce this in production-only files
        if not self._print_exempt and isinstance(node.func, ast.Name) and node.func.id == "print":
            self._add(node, _RULE["ARCH-001"], "print() call — use logger instead")

        # Rule ARCH-002 (visitor): no sensitive kwargs in logger calls — not expressible in ruff
        if isinstance(node.func, ast.Attribute) and node.func.attr in _LOGGER_METHODS:
            for kw in node.keywords:
                if kw.arg and any(s in kw.arg.lower() for s in _SENSITIVE_KEYWORDS):
                    self._add(
                        node, _RULE["ARCH-002"], f"sensitive kwarg '{kw.arg}' passed to logger"
                    )

        # Rule ARCH-005 (visitor): get_logger must be called with __name__
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

        # Rule ARCH-006 (visitor): no f-string with sensitive variable names in logger args
        if isinstance(func, ast.Attribute) and func.attr in _LOGGER_METHODS:
            for arg in node.args:
                if isinstance(arg, ast.JoinedStr):  # f-string
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
        """Rule ARCH-003 (visitor): broad except without logger or re-raise → silent swallow."""
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


_SOURCE_FILES = sorted(SRC_ROOT.rglob("*.py"))


def _check_termination_dispatch_exhaustive(src_dir: Path) -> list[str]:
    """
    ARCH-007: Detect functions that dispatch over TerminationReason via if/elif
    chains (dispatch tables) rather than exhaustive match/case + assert_never.

    A "dispatch table" is detected when a single FunctionDef contains comparisons
    to ≥2 distinct TerminationReason.* values (including values inside tuple
    membership tests like `termination in (TerminationReason.X, TerminationReason.Y)`).
    A single comparison (guard) is exempt. Functions in
    _DISPATCH_TABLE_EXEMPT_FUNCTIONS are also exempt.

    Returns a list of violation strings for failing tests.
    """
    violations = []
    for py_file in src_dir.rglob("*.py"):
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            if node.name in _DISPATCH_TABLE_EXEMPT_FUNCTIONS:
                continue
            # Collect all TerminationReason.VALUE names compared with == or in
            tr_values: set[str] = set()
            has_assert_never = False
            has_match = False
            for child in ast.walk(node):
                # Detect: termination == TerminationReason.SOME_VALUE
                # and: termination in (TerminationReason.X, TerminationReason.Y)
                if isinstance(child, ast.Compare):
                    for comparator in child.comparators:
                        if (
                            isinstance(comparator, ast.Attribute)
                            and isinstance(comparator.value, ast.Name)
                            and comparator.value.id == "TerminationReason"
                        ):
                            tr_values.add(comparator.attr)
                        elif isinstance(comparator, ast.Tuple):
                            # Handle: termination in (TerminationReason.X, TerminationReason.Y)
                            for elt in comparator.elts:
                                if (
                                    isinstance(elt, ast.Attribute)
                                    and isinstance(elt.value, ast.Name)
                                    and elt.value.id == "TerminationReason"
                                ):
                                    tr_values.add(elt.attr)
                # Detect match statements (Python 3.10+: ast.Match)
                if hasattr(ast, "Match") and isinstance(child, ast.Match):
                    has_match = True
                # Detect assert_never calls
                if (
                    isinstance(child, ast.Call)
                    and isinstance(child.func, ast.Name)
                    and child.func.id == "assert_never"
                ):
                    has_assert_never = True
            # Dispatch table = ≥2 distinct TerminationReason values checked
            if len(tr_values) >= 2 and not (has_match and has_assert_never):
                violations.append(
                    f"{py_file.relative_to(src_dir.parent.parent)}:{node.lineno}: "
                    f"{node.name}() dispatches on {tr_values} via if/elif — "
                    f"use match/case + assert_never"
                )
    return violations


def _find_enclosing_function(node: ast.AST, tree: ast.AST) -> str | None:
    for parent in ast.walk(tree):
        if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for child in ast.walk(parent):
                if child is node:
                    return parent.name
    return None


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_tmp_path_is_ram_backed(tmp_path: Path) -> None:
    """On Linux/WSL2, tmp_path must resolve to /dev/shm (RAM-backed tmpfs).

    On macOS no assertion is made — disk-backed /tmp is acceptable there.
    Fails intentionally on Linux when pytest is invoked directly without --basetemp.
    Always run tests via 'task test-all', not pytest directly.
    """
    if sys.platform == "linux":
        path_str = str(tmp_path)
        assert path_str.startswith("/dev/shm"), (
            f"tmp_path ({path_str!r}) is not in /dev/shm. "
            "Run tests via 'task test-all', which passes "
            "--basetemp=/dev/shm/pytest-tmp."
        )


class TestArchitectureEnforcement:
    """Parametrized AST checks over every .py file in src/autoskillit/."""

    @pytest.mark.parametrize(
        "source_file",
        _SOURCE_FILES,
        ids=[_rel(f) for f in _SOURCE_FILES],
    )
    def test_no_violations(self, source_file: Path) -> None:
        violations = _scan(source_file)
        if violations:
            report = "\n".join(f"  {v}" for v in violations)
            pytest.fail(
                f"Architectural violations in {_rel(source_file)}:\n{report}",
                pytrace=False,
            )


# ── ARCH-003 calibration tests ────────────────────────────────────────────────


def test_broad_except_exception_without_log_is_violation(tmp_path: Path) -> None:
    """Rule 3: except Exception: pass with no logger call must be flagged."""
    f = tmp_path / "bad.py"
    f.write_text("try:\n    pass\nexcept Exception:\n    pass\n")
    violations = _scan(f)
    assert violations, "Expected violation for broad except Exception without logger"
    messages = " ".join(v.message for v in violations)
    assert "except" in messages.lower()
    assert "logger" in messages.lower()


def test_broad_except_base_exception_without_log_is_violation(tmp_path: Path) -> None:
    """Rule 3: except BaseException: pass with no logger call must be flagged."""
    f = tmp_path / "bad.py"
    f.write_text("try:\n    pass\nexcept BaseException:\n    pass\n")
    violations = _scan(f)
    assert violations, "Expected violation for broad except BaseException without logger"


def test_bare_except_without_log_is_violation(tmp_path: Path) -> None:
    """Rule 3: bare except: pass with no logger call must be flagged."""
    f = tmp_path / "bad.py"
    f.write_text("try:\n    pass\nexcept:\n    pass\n")
    violations = _scan(f)
    assert violations, "Expected violation for bare except without logger"


def test_broad_except_with_log_call_is_not_violation(tmp_path: Path) -> None:
    """Rule 3: except Exception with a logger call is not a violation."""
    f = tmp_path / "ok.py"
    f.write_text(
        "import logging\n"
        "logger = logging.getLogger(__name__)\n"
        "try:\n"
        "    pass\n"
        "except Exception:\n"
        "    logger.warning('failed')\n"
    )
    violations = _scan(f)
    except_violations = [v for v in violations if "except" in v.message.lower()]
    assert not except_violations, f"Unexpected except violation: {except_violations}"


def test_specific_except_without_log_is_not_violation(tmp_path: Path) -> None:
    """Rule 3: except OSError (specific type) without logger is not a violation."""
    f = tmp_path / "ok.py"
    f.write_text("try:\n    pass\nexcept OSError:\n    pass\n")
    violations = _scan(f)
    except_violations = [v for v in violations if "except" in v.message.lower()]
    assert not except_violations, f"Unexpected except violation: {except_violations}"


def test_broad_except_with_reraise_is_not_violation(tmp_path: Path) -> None:
    """Rule 3: except Exception with unconditional re-raise is not a violation."""
    f = tmp_path / "ok.py"
    f.write_text("try:\n    pass\nexcept Exception:\n    raise\n")
    violations = _scan(f)
    except_violations = [v for v in violations if "except" in v.message.lower()]
    assert not except_violations, f"Unexpected except violation: {except_violations}"


# ── ARCH-004/005/006 calibration tests ───────────────────────────────────────


def test_asyncio_pipe_ban_detects_violation(tmp_path: Path) -> None:
    f = tmp_path / "some_module.py"
    f.write_text("import asyncio\nval = asyncio.PIPE\n")
    violations = _scan(f)
    assert any("asyncio.PIPE" in v.message for v in violations)


def test_asyncio_pipe_ban_exempt_in_process(tmp_path: Path) -> None:
    f = tmp_path / "process.py"
    f.write_text("import asyncio\nval = asyncio.PIPE\n")
    violations = _scan(f)
    assert not any("asyncio.PIPE" in v.message for v in violations)


def test_get_logger_name_enforcement_detects_literal(tmp_path: Path) -> None:
    f = tmp_path / "some.py"
    f.write_text("from autoskillit._logging import get_logger\nlogger = get_logger('mymodule')\n")
    violations = _scan(f)
    assert any("get_logger" in v.message for v in violations)


def test_fstring_secret_detects_token_var(tmp_path: Path) -> None:
    f = tmp_path / "some.py"
    f.write_text("logger.info(f'Using {token}')\n")
    violations = _scan(f)
    assert any("token" in v.message for v in violations)


def test_fstring_secret_safe_for_nonsensitive(tmp_path: Path) -> None:
    f = tmp_path / "some.py"
    f.write_text("logger.info(f'Count: {count}')\n")
    violations = _scan(f)
    assert not any("f-string" in v.message for v in violations)


# ── ARCH-007 test ──────────────────────────────────────────────────────────────


def test_arch007_termination_dispatch_tables_use_exhaustive_match() -> None:
    """
    ARCH-007: Any function in execution/ that dispatches on ≥2 distinct
    TerminationReason values via if/elif must use match/case with assert_never.
    Single-value guard checks (e.g., `if termination == TIMED_OUT:`) are exempt.
    """
    violations = _check_termination_dispatch_exhaustive(SRC_ROOT / "execution")
    assert violations == [], (
        "Non-exhaustive TerminationReason dispatch tables found:\n" + "\n".join(violations)
    )


def test_no_raw_claude_list_construction() -> None:
    """No list literal starting with 'claude' may be constructed outside the ALLOWED set.

    Enforces that all claude command construction goes through the canonical
    builders in execution/commands.py, preventing ad-hoc command assembly
    that bypasses established safety flags.
    """
    ALLOWED = {
        ("app.py", "install"),
        ("_llm_triage.py", "triage_staleness"),
        ("commands.py", "build_interactive_cmd"),
        ("commands.py", "build_headless_cmd"),
    }
    violations: list[str] = []
    for path in SRC_ROOT.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.List)
                and node.elts
                and isinstance(node.elts[0], ast.Constant)
                and node.elts[0].value == "claude"
            ):
                fn_name = _find_enclosing_function(node, tree)
                if (path.name, fn_name) not in ALLOWED:
                    violations.append(
                        f"{path.relative_to(SRC_ROOT.parent.parent)}:{node.lineno}: "
                        f"raw ['claude', ...] list in {path.name}:{fn_name or '<module>'}"
                    )
    assert not violations, (
        "Raw ['claude', ...] list construction found outside allowed locations:\n"
        + "\n".join(f"  {v}" for v in violations)
    )
