"""Architectural enforcement: AST-based rules over src/autoskillit/ source files.

Rules enforced here (compile-time, no execution required):
  1. No print() calls in production code
  2. No sensitive keyword arguments passed to logger calls
  3. No broad except without logger call or re-raise
  4. Import layer contract (each module only imports from same or lower layer)
  5. Singleton definition locality (module-level constructors only in allowed modules)
  6. MCP tool registry completeness (bidirectional equality with _gate registry)
  7. No module-level I/O (open/load_config/yaml.safe_load at module scope)
  8. asyncio.PIPE ban outside process_lifecycle.py
  9. get_logger() must be called with __name__
 10. No f-string interpolation of sensitive variables in logger positional args

Note: `import logging` and `logging.getLogger()` are enforced by ruff TID251
at pre-commit time (see pyproject.toml [tool.ruff.lint.flake8-tidy-imports]).
Those rules belong in the toolchain, not duplicated here.

Exemptions:
  - cli/app.py, cli/doctor.py: may use print() for user-facing terminal output
"""

from __future__ import annotations

import ast
import sys
from dataclasses import FrozenInstanceError, dataclass
from pathlib import Path
from typing import NamedTuple

import pytest

SRC_ROOT = Path(__file__).parent.parent / "src" / "autoskillit"

_SENSITIVE_KEYWORDS = frozenset({"token", "secret", "password", "key", "api_key", "auth"})
_LOGGER_METHODS = frozenset({"debug", "info", "warning", "error", "critical", "exception"})
_PRINT_EXEMPT = frozenset({"app.py", "_doctor.py", "quota_check.py", "remove_clone_guard.py", "skill_cmd_check.py"})
_BROAD_EXCEPTION_TYPES: frozenset[str] = frozenset({"Exception", "BaseException"})
# Standalone hook scripts: fail-open design requires silent broad excepts and print() for JSON
_BROAD_EXCEPT_EXEMPT = frozenset({"quota_check.py", "remove_clone_guard.py", "skill_cmd_check.py"})

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
        return str(path.relative_to(Path(__file__).parent.parent))
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
        defense_standard="DS-007",
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


def _runtime_import_froms(path: Path) -> list[ast.ImportFrom]:
    """Return ImportFrom nodes not inside a TYPE_CHECKING guard."""
    tree = ast.parse(path.read_text())
    result: list[ast.ImportFrom] = []

    def _walk(stmts: list) -> None:
        for stmt in stmts:
            if isinstance(stmt, ast.ImportFrom):
                result.append(stmt)
            elif isinstance(stmt, ast.If):
                test = stmt.test
                is_tc = (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
                    isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
                )
                if not is_tc:
                    _walk(stmt.body)
                    _walk(stmt.orelse)
            elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                _walk(stmt.body)
            elif isinstance(stmt, ast.ClassDef):
                _walk(stmt.body)
            elif isinstance(stmt, ast.Try):
                _walk(stmt.body)
                for handler in stmt.handlers:
                    _walk(handler.body)
                _walk(stmt.orelse)
                _walk(getattr(stmt, "finalbody", []))

    _walk(tree.body)
    return result


# ── Rule 1: Import layer enforcement ─────────────────────────────────────────
SUBPACKAGE_LAYERS: dict[str, int] = {
    # Layer 0: core/ — zero autoskillit internal imports
    "core": 0,
    # Layer 1: domain primitives — may import only from L0
    "config": 1,
    "pipeline": 1,
    "execution": 1,
    "workspace": 1,
    # Layer 2: domain services — may import from L0 and L1
    "recipe": 2,
    "migration": 2,
    # Layer 3: application layer — may import from L0–L2
    "server": 3,
    "cli": 3,
}
# Root-level isolated modules are exempt from sub-package layer enforcement.
# Their import constraints are tested by test_isolated_modules_do_not_import_server_or_cli.
_LAYER_EXEMPT_STEMS: frozenset[str] = frozenset(
    {"version", "smoke_utils", "_llm_triage", "__init__", "__main__"}
)

# ── Rule 2: Singleton definition locality ─────────────────────────────────────
# "server" allows mcp = FastMCP(...); "cli" allows app = App(...) etc.
SINGLETON_ALLOWED_MODULES: frozenset[str] = frozenset(
    {
        "__init__",  # server/__init__.py: mcp = FastMCP(...)
        "app",  # cli/app.py: app = App(...), config_app = App(...), etc.
        "store",  # migration/store.py: defensive exemption for future module-level construction
        "validator",  # recipe/validator.py: defensive exemption for decorator-based rule registry
    }
)
_SINGLETON_SAFE_CALL_NAMES: frozenset[str] = frozenset(
    {
        "frozenset",
        "set",
        "list",
        "dict",
        "tuple",
        "str",
        "int",
        "float",
        "bool",
        "type",
        "TypeVar",
        "field",
        "dataclass",
        "get_logger",
        "version",
        "compile",
        "object",
    }
)

# ── Rule 4: No module-level I/O ───────────────────────────────────────────────
_MODULE_LEVEL_IO_FUNC_NAMES: frozenset[str] = frozenset({"load_config", "open", "yaml.safe_load"})
_MODULE_LEVEL_IO_ATTR_CALLS: frozenset[tuple[str, str]] = frozenset(
    {("Path", "cwd"), ("os", "getcwd")}
)
_MODULE_LEVEL_IO_EXEMPT: frozenset[str] = frozenset({"__main__.py"})

# ── Rule 5 (visitor): asyncio.PIPE ban ────────────────────────────────────────
_ASYNCIO_PIPE_EXEMPT: frozenset[str] = frozenset({"process.py"})


# ── Helpers for new rules ─────────────────────────────────────────────────────


def _module_stem(path: Path) -> str:
    """Return the stem (filename without .py) of a source file."""
    return path.stem


def _extract_module_level_internal_imports(path: Path) -> list[tuple[str, int]]:
    """Return (imported_module_stem, lineno) for all autoskillit imports at module level.

    Only iterates tree.body (module-level statements). Import nodes inside
    function or class bodies are intentionally excluded.
    """
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    results: list[tuple[str, int]] = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module:
            parts = node.module.split(".")
            if parts[0] == "autoskillit" and len(parts) > 1:
                results.append((parts[1], node.lineno))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if parts[0] == "autoskillit" and len(parts) > 1:
                    results.append((parts[1], node.lineno))
    return results


def _collect_deferred_imports(tree: ast.Module) -> list[ast.Import | ast.ImportFrom]:
    """Return Import/ImportFrom nodes not at module level (inside function or class bodies).

    Identifies deferred imports by excluding nodes whose line number appears in
    module-level statements (tree.body). This is the complement of
    _extract_module_level_internal_imports and catches the function-body equivalent
    of layer violations.
    """
    module_level_linenos = {
        node.lineno for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))
    }
    deferred: list[ast.Import | ast.ImportFrom] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.Import, ast.ImportFrom))
            and node.lineno not in module_level_linenos
        ):
            deferred.append(node)
    return deferred


def _get_call_func_name(node: ast.Call) -> str | None:
    """Return the function name for simple calls, or None for complex expressions."""
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _scan_module_level_io(path: Path) -> list[Violation]:
    """Return Violations for module-level I/O calls in path.

    Scans only tree.body (direct module-level statements). Does not descend
    into nested function or class definitions.
    """
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []

    violations: list[Violation] = []
    for stmt in tree.body:
        # Skip function/class definitions — their bodies are not module-level I/O
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        # Walk only the direct statement (not recursing into nested scopes)
        for node in ast.walk(stmt):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            # Simple name calls: open(), load_config()
            if isinstance(func, ast.Name) and func.id in _MODULE_LEVEL_IO_FUNC_NAMES:
                violations.append(
                    Violation(
                        path,
                        node.lineno,
                        node.col_offset,
                        f"module-level I/O call: {func.id}()",
                    )
                )
            # Attribute calls: yaml.safe_load(), Path.cwd(), os.getcwd()
            elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                obj = func.value.id
                attr = func.attr
                if (obj, attr) in _MODULE_LEVEL_IO_ATTR_CALLS:
                    violations.append(
                        Violation(
                            path,
                            node.lineno,
                            node.col_offset,
                            f"module-level I/O call: {obj}.{attr}()",
                        )
                    )
                elif attr == "safe_load" and obj == "yaml":
                    violations.append(
                        Violation(
                            path,
                            node.lineno,
                            node.col_offset,
                            "module-level I/O call: yaml.safe_load()",
                        )
                    )
    return violations


# ── Extension Point Coverage (REQ-EXT-001 to REQ-EXT-005) ───────────────────
#
# REQ-EXT-001: Adding a new MCP tool must require changes only within server/.
#   Covered by: test_import_layer_enforcement[server-3] — any logic pulled from
#   L0–L2 into server/ would cause an upward import violation in those lower-
#   layer modules. test_server_tool_handlers_have_no_business_logic ensures
#   tool handlers stay thin delegates without embedded domain logic.
#
# REQ-EXT-002: Adding a recipe semantic validation rule must require only a
#   decorated function in recipe/validator.py.
#   Covered by: test_recipe_no_forbidden_imports — validator.py can only
#   import from core/ and workspace/; any violation indicates a structural
#   change outside recipe/. The decorator-based rule registry means no
#   registration file or central list requires modification for new rules.
#
# REQ-EXT-003: Adding a migration adapter must require only subclassing
#   MigrationAdapter and registering with the default factory.
#   Covered by: test_migration_no_forbidden_imports — migration/ imports
#   only from core/, execution/, and recipe/; new adapters that violate
#   this boundary are caught at test time without manual test updates.
#
# REQ-EXT-004: Adding a new CLI command must require changes only within cli/.
#   Covered by: test_import_layer_enforcement[cli-3] — cli/ is L3; L0–L2
#   modules that grow a cli/ dependency would be caught as upward imports
#   in those modules' own layer enforcement test cases.
#
# REQ-EXT-005: Adding a new bundled skill requires only creating a directory
#   under skills/ containing a SKILL.md file; no code changes are needed.
#   Covered by: SkillResolver uses directory scanning, not a static registry.
#   test_server_file_count_under_limit (groupD) ensures server/ doesn't
#   accumulate skill-wiring boilerplate past 10 files undetected.


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


def test_sync_manifest_module_deleted():
    """REQ-SYNC-002: sync_manifest.py does not exist."""
    sync_path = Path(__file__).parent.parent / "src" / "autoskillit" / "sync_manifest.py"
    assert not sync_path.exists()


def test_no_sync_manifest_imports_in_production_code():
    """REQ-SYNC-001: No production module imports from autoskillit.sync_manifest."""
    src_dir = Path(__file__).parent.parent / "src"
    for py_file in src_dir.rglob("*.py"):
        content = py_file.read_text()
        assert "sync_manifest" not in content, f"Found sync_manifest reference in {py_file}"


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


def _is_mcp_tool_decorator(node: ast.expr) -> bool:
    """Return True if node represents @mcp.tool or @mcp.tool(...)."""
    if isinstance(node, ast.Attribute) and node.attr == "tool":
        return True
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "tool"
    ):
        return True
    return False


def _has_call_to(stmt: ast.stmt, func_name: str) -> bool:
    """Return True if stmt (recursively) contains a call to func_name."""
    for node in ast.walk(stmt):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == func_name
        ):
            return True
    return False


def _has_await_or_return(stmt: ast.stmt) -> bool:
    """Return True if stmt (recursively) contains an await expression or return."""
    for node in ast.walk(stmt):
        if isinstance(node, (ast.Await, ast.Return)):
            return True
    return False


def test_all_mcp_tools_are_registered() -> None:
    """Bidirectional check: every @mcp.tool function is in the _gate registry and
    every registry entry has a corresponding @mcp.tool function in server/."""
    from autoskillit.pipeline.gate import GATED_TOOLS, UNGATED_TOOLS

    expected = GATED_TOOLS | UNGATED_TOOLS
    server_dir = SRC_ROOT / "server"
    decorated: set[str] = set()
    for py_file in server_dir.glob("*.py"):
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                for dec in node.decorator_list:
                    if _is_mcp_tool_decorator(dec):
                        decorated.add(node.name)

    unregistered = decorated - expected
    missing = expected - decorated
    assert not unregistered, f"@mcp.tool functions not in _gate registry: {sorted(unregistered)}"
    assert not missing, f"_gate registry entries have no @mcp.tool function: {sorted(missing)}"


def test_gated_tools_call_require_enabled_first() -> None:
    """Every tool in GATED_TOOLS must call _require_enabled() before any
    await expression or return statement in its function body."""
    from autoskillit.pipeline.gate import GATED_TOOLS

    server_dir = SRC_ROOT / "server"
    violations: list[str] = []

    for py_file in server_dir.glob("*.py"):
        src = py_file.read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name not in GATED_TOOLS:
                    continue
                if not any(_is_mcp_tool_decorator(d) for d in node.decorator_list):
                    continue

                # Find the statement index of first _require_enabled() call
                # and first await/return in the function body.
                require_idx: int | None = None
                action_idx: int | None = None

                for i, stmt in enumerate(node.body):
                    if require_idx is None and _has_call_to(stmt, "_require_enabled"):
                        require_idx = i
                    if action_idx is None and _has_await_or_return(stmt):
                        action_idx = i

                if require_idx is None:
                    violations.append(f"{node.name}: _require_enabled() never called")
                elif action_idx is not None and require_idx > action_idx:
                    violations.append(
                        f"{node.name}: _require_enabled() called at stmt {require_idx} "
                        f"but await/return found at stmt {action_idx} first"
                    )

    assert not violations, (
        "Gated tools must call _require_enabled() before any await/return:\n"
        + "\n".join(f"  {v}" for v in violations)
    )


def test_ungated_tools_do_not_call_require_enabled() -> None:
    """No function named in UNGATED_TOOLS may call _require_enabled()."""
    import inspect
    import textwrap

    from autoskillit.pipeline.gate import UNGATED_TOOLS
    from autoskillit.server import (
        tools_clone,
        tools_execution,
        tools_git,
        tools_integrations,
        tools_recipe,
        tools_status,
        tools_workspace,
    )

    _all_tool_modules = [
        tools_execution,
        tools_git,
        tools_workspace,
        tools_clone,
        tools_recipe,
        tools_status,
        tools_integrations,
    ]

    violations: list[str] = []
    for module in _all_tool_modules:
        for name, fn in inspect.getmembers(module, inspect.isfunction):
            if name not in UNGATED_TOOLS:
                continue
            source = textwrap.dedent(inspect.getsource(fn))
            if "_require_enabled" in source:
                violations.append(f"{module.__name__}.{name}")
    assert not violations, (
        f"Ungated tools that call _require_enabled(): {violations}. "
        f"Ungated tools must be available without open_kitchen."
    )


def test_server_imports_gate_registry() -> None:
    """server/ package must import GATED_TOOLS and UNGATED_TOOLS from autoskillit.pipeline(.gate).

    N6 requirement: the server package is the authoritative runtime consumer of the
    gate registry, not only the test suite.  Accepts both the sub-module import
    (autoskillit.pipeline.gate) and the gateway import (autoskillit.pipeline).
    """
    server_dir = SRC_ROOT / "server"
    imported: set[str] = set()
    for py_file in server_dir.glob("*.py"):
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in (
                "autoskillit.pipeline.gate",
                "autoskillit.pipeline",
            ):
                for alias in node.names:
                    imported.add(alias.name)

    missing = {"GATED_TOOLS", "UNGATED_TOOLS"} - imported
    assert not missing, f"server/ package must import from autoskillit.pipeline: {sorted(missing)}"


# ── Rule 1: test_import_layer_enforcement ─────────────────────────────────────


@pytest.mark.parametrize(
    "pkg_name,layer",
    [(k, v) for k, v in SUBPACKAGE_LAYERS.items()],
)
def test_import_layer_enforcement(pkg_name: str, layer: int) -> None:
    """Each sub-package may only import from same or lower layer sub-packages."""
    pkg_dir = SRC_ROOT / pkg_name
    if not pkg_dir.exists():
        pytest.skip(f"{pkg_name}/ not found — prerequisite group not merged")

    violations: list[str] = []
    for py_file in pkg_dir.rglob("*.py"):
        for imported_stem, lineno in _extract_module_level_internal_imports(py_file):
            if imported_stem not in SUBPACKAGE_LAYERS:
                continue  # root-level, exempt, or external module
            imported_layer = SUBPACKAGE_LAYERS[imported_stem]
            if imported_layer > layer:
                violations.append(
                    f"  {_rel(py_file)}:{lineno}: {pkg_name} (L{layer}) imports "
                    f"{imported_stem} (L{imported_layer}) — upward import"
                )

    assert not violations, f"Layer violations in {pkg_name}/:\n" + "\n".join(violations)


@pytest.mark.parametrize(
    "pkg_name",
    [pkg for pkg, layer in SUBPACKAGE_LAYERS.items() if layer == 2],
)
def test_l2_no_deferred_upward_imports(pkg_name: str) -> None:
    """L2 sub-packages must not use deferred imports that violate layer contracts.

    Extends test_import_layer_enforcement to cover function-body (deferred) imports
    via ast.walk, not just tree.body scans. Mirrors the upward-only rule applied at
    module level: L2 package (recipe, migration) may not deferred-import an L3 package
    (server, cli) — always forbidden.
    """
    pkg_dir = SRC_ROOT / pkg_name
    if not pkg_dir.exists():
        pytest.skip(f"{pkg_name}/ not found — prerequisite group not merged")

    pkg_layer = SUBPACKAGE_LAYERS[pkg_name]  # == 2 for all L2 packages
    violations: list[str] = []

    for py_file in pkg_dir.rglob("*.py"):
        source = py_file.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(py_file))
        deferred_nodes = _collect_deferred_imports(tree)

        for node in deferred_nodes:
            stems_to_check: list[str] = []
            if isinstance(node, ast.ImportFrom) and node.module:
                parts = node.module.split(".")
                if parts[0] == "autoskillit" and len(parts) > 1:
                    stems_to_check = [parts[1]]
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    parts = alias.name.split(".")
                    if parts[0] == "autoskillit" and len(parts) > 1:
                        stems_to_check.append(parts[1])

            for imported_stem in stems_to_check:
                if imported_stem not in SUBPACKAGE_LAYERS:
                    continue
                imported_layer = SUBPACKAGE_LAYERS[imported_stem]
                if imported_layer > pkg_layer:
                    violations.append(
                        f"  {_rel(py_file)}:{node.lineno}: {pkg_name} (L{pkg_layer}) "
                        f"deferred-imports {imported_stem} (L{imported_layer})"
                        f" — upward deferred import"
                    )

    assert not violations, f"Deferred layer violations in {pkg_name}/:\n" + "\n".join(violations)


# ── Rule 2: test_singleton_definition_locality ────────────────────────────────


@pytest.mark.parametrize("source_file", _SOURCE_FILES)
def test_singleton_definition_locality(source_file: Path) -> None:
    """Module-level constructor calls are only permitted in SINGLETON_ALLOWED_MODULES."""
    mod_stem = source_file.stem
    if mod_stem in SINGLETON_ALLOWED_MODULES:
        return  # exempt

    tree = ast.parse(source_file.read_text())
    violations: list[str] = []
    for node in tree.body:  # module-level only
        rhs: ast.expr | None = None
        if isinstance(node, ast.Assign) and node.value:
            rhs = node.value
        elif isinstance(node, ast.AnnAssign) and node.value:
            rhs = node.value
        if rhs is None or not isinstance(rhs, ast.Call):
            continue
        func_name = _get_call_func_name(rhs)
        if func_name in _SINGLETON_SAFE_CALL_NAMES:
            continue
        if func_name is None:
            continue  # complex expression, skip
        violations.append(
            f"  line {node.lineno}: module-level call to '{func_name}()' — "
            f"add {mod_stem!r} to SINGLETON_ALLOWED_MODULES if intentional"
        )

    assert not violations, f"Singleton locality violations in {_rel(source_file)}:\n" + "\n".join(
        violations
    )


# ── Rule 4: test_no_module_level_io ───────────────────────────────────────────


@pytest.mark.parametrize(
    "source_file",
    [f for f in _SOURCE_FILES if f.name not in _MODULE_LEVEL_IO_EXEMPT],
)
def test_no_module_level_io(source_file: Path) -> None:
    """Production modules must not call open/load_config/yaml.safe_load at module scope."""
    violations = _scan_module_level_io(source_file)
    assert not violations, "Module-level I/O calls found:\n" + "\n".join(
        str(v) for v in violations
    )


# ── Calibration tests ──────────────────────────────────────────────────────────


# Rule 1 calibration


def test_layer_enforcement_detects_upward_import(tmp_path: Path) -> None:
    """A L1 module importing a L3 module triggers a violation."""
    f = tmp_path / "fake_l1.py"
    f.write_text("from autoskillit.server import mcp\n")
    violations: list[str] = []
    tree = ast.parse(f.read_text())
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module:
            parts = node.module.split(".")
            if parts[0] == "autoskillit" and len(parts) > 1:
                imported = parts[1]
                # synthetic module is L1; upward = strictly greater
                if SUBPACKAGE_LAYERS.get(imported, 0) > 1:
                    violations.append(imported)
    assert violations  # must detect the upward import


# Rule 2 calibration


def test_singleton_locality_detects_non_allowed(tmp_path: Path) -> None:
    snippet = "class Foo: pass\nfoo = Foo()\n"
    f = tmp_path / "fake_module.py"
    f.write_text(snippet)
    tree = ast.parse(snippet)
    found = False
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            func_name = _get_call_func_name(node.value)
            if func_name and func_name not in _SINGLETON_SAFE_CALL_NAMES:
                found = True
    assert found


# Rule 4 calibration


def test_no_module_level_io_detects_open_call(tmp_path: Path) -> None:
    f = tmp_path / "fake.py"
    f.write_text("_f = open('config.yaml')\n")
    assert _scan_module_level_io(f)


def test_no_module_level_io_detects_yaml_load(tmp_path: Path) -> None:
    f = tmp_path / "fake.py"
    f.write_text("import yaml\n_data = yaml.safe_load(open('x'))\n")
    assert _scan_module_level_io(f)


# Rule 5 calibration (exercised via _scan + visitor)


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


# Rule 6 calibration


def test_get_logger_name_enforcement_detects_literal(tmp_path: Path) -> None:
    f = tmp_path / "some.py"
    f.write_text("from autoskillit._logging import get_logger\nlogger = get_logger('mymodule')\n")
    violations = _scan(f)
    assert any("get_logger" in v.message for v in violations)


# Rule 7 calibration


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


def _get_module_ast(filename: str) -> ast.Module:
    return ast.parse((SRC_ROOT / filename).read_text())


def _top_level_class_names(tree: ast.Module) -> set[str]:
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and node.col_offset == 0
    }


def _top_level_assign_targets(tree: ast.Module) -> set[str]:
    names = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def test_severity_defined_in_types():
    """Severity must be a top-level class in core/types.py."""
    tree = _get_module_ast("core/types.py")
    assert "Severity" in _top_level_class_names(tree), (
        "Severity not found in core/types.py; it must be defined there"
    )


def test_skill_tools_defined_in_types():
    """SKILL_TOOLS must be a top-level assignment in core/types.py."""
    tree = _get_module_ast("core/types.py")
    assert "SKILL_TOOLS" in _top_level_assign_targets(tree), (
        "SKILL_TOOLS not found in core/types.py; it must be defined there"
    )


def test_claude_md_documents_all_source_modules() -> None:
    """Every .py file in src/autoskillit/ must appear by name in CLAUDE.md.

    For __init__.py files, the containing package directory name must appear.
    For all other files, the filename must appear somewhere in CLAUDE.md.
    """
    claude_path = Path(__file__).parent.parent / "CLAUDE.md"
    content = claude_path.read_text()
    src_root = Path(__file__).parent.parent / "src" / "autoskillit"

    missing = []
    for py_file in sorted(src_root.rglob("*.py")):
        if "__pycache__" in py_file.parts:
            continue
        rel = py_file.relative_to(src_root)
        if py_file.name == "__init__.py":
            # For sub-package inits, verify the package directory is documented
            parent = rel.parent
            if parent != Path(".") and (parent.name + "/") not in content:
                missing.append(str(rel))
        else:
            if py_file.name not in content:
                missing.append(str(rel))

    assert not missing, (
        f"Modules not documented in CLAUDE.md: {', '.join(missing)}. "
        "Update the Architecture section in CLAUDE.md."
    )


def test_pyproject_cyclopts_minimum_version() -> None:
    """cyclopts lower bound in pyproject.toml must be >=4.0, not >=3.0.

    cyclopts 3.x and 4.x have incompatible APIs. A >=3.0 constraint allows
    a conservative resolver to silently install 3.x, which fails at runtime.
    """
    import re

    toml_path = Path(__file__).parent.parent / "pyproject.toml"
    content = toml_path.read_text()
    match = re.search(r'"cyclopts>=([\d.]+)"', content)
    assert match is not None, "cyclopts dependency not found in pyproject.toml"
    major = int(match.group(1).split(".")[0])
    assert major >= 4, (
        f"cyclopts minimum version is {match.group(1)}, expected >=4.0. "
        "cyclopts 3.x API is incompatible with the 4.x API used in this codebase."
    )


def test_no_yaml_safe_load_in_migration_engine() -> None:
    """P7-2: ContractMigrationAdapter.validate must use _load_yaml, not yaml.safe_load."""
    src = (Path(__file__).parent.parent / "src/autoskillit/migration/engine.py").read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "safe_load":
                pytest.fail(
                    f"migration/engine.py line {node.lineno}: "
                    f"direct yaml.safe_load call found; use load_yaml from core.io instead"
                )


def test_pytest_asyncio_version_bound() -> None:
    """P11-2: pytest-asyncio lower bound must match the published 0.x stable series."""
    import tomllib

    pyproject = Path(__file__).parent.parent / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text())
    deps = data["project"]["optional-dependencies"]["dev"]
    asyncio_dep = next(d for d in deps if d.startswith("pytest-asyncio"))
    assert ">=0.23" in asyncio_dep, f"Expected pytest-asyncio>=0.23.x, got: {asyncio_dep!r}"


def test_severity_not_defined_locally_in_recipe_validator() -> None:
    """Severity must be imported from types, not locally defined in recipe sub-modules."""
    for filename in ("recipe/validator.py", "recipe/contracts.py"):
        ast_module = _get_module_ast(filename)
        class_names = _top_level_class_names(ast_module)
        assert "Severity" not in class_names, (
            f"Severity must live in core/types.py, not {filename}"
        )


def test_severity_not_locally_defined_in_doctor() -> None:
    """cli/_doctor.py must not define its own Severity — it must import from core."""
    ast_module = _get_module_ast("cli/_doctor.py")
    class_names = _top_level_class_names(ast_module)
    assert "Severity" not in class_names, (
        "cli/_doctor.py must import Severity from autoskillit.core, not define it locally"
    )


def test_skill_tools_not_defined_in_recipe_io() -> None:
    """SKILL_TOOLS must not be defined locally in recipe/io.py."""
    ast_module = _get_module_ast("recipe/io.py")
    assigns = _top_level_assign_targets(ast_module)
    assert "SKILL_TOOLS" not in assigns and "_SKILL_TOOLS" not in assigns, (
        "SKILL_TOOLS must be imported from core/types, not defined in recipe/io.py"
    )


def test_skill_tools_not_defined_in_recipe_validator() -> None:
    """SKILL_TOOLS must not be defined locally in recipe/validator.py or recipe/contracts.py."""
    for filename in ("recipe/validator.py", "recipe/contracts.py"):
        ast_module = _get_module_ast(filename)
        assigns = _top_level_assign_targets(ast_module)
        assert "SKILL_TOOLS" not in assigns and "_SKILL_TOOLS" not in assigns, (
            f"SKILL_TOOLS must be imported from core/types, not defined in {filename}"
        )


def test_contract_validator_module_deleted() -> None:
    """contract_validator.py must not exist — functionality merged into recipe_validator.py."""
    cv_path = SRC_ROOT / "contract_validator.py"
    assert not cv_path.exists(), (
        "contract_validator.py should be deleted; its code lives in recipe_validator.py"
    )


def test_recipe_validator_has_regex_patterns() -> None:
    """recipe/contracts.py must define context/input regex patterns."""
    ast_module = _get_module_ast("recipe/contracts.py")
    assigns = _top_level_assign_targets(ast_module)
    assert "_CONTEXT_REF_RE" in assigns, "recipe/contracts.py must define _CONTEXT_REF_RE"
    assert "_INPUT_REF_RE" in assigns, "recipe/contracts.py must define _INPUT_REF_RE"


def test_recipe_validator_no_process_lifecycle_import() -> None:
    """recipe/validator.py and recipe/contracts.py must not import from process_lifecycle."""
    for filename in ("recipe/validator.py", "recipe/contracts.py"):
        import_pairs = _extract_module_level_internal_imports(SRC_ROOT / filename)
        import_stems = [stem for stem, _ in import_pairs]
        assert "process_lifecycle" not in import_stems, (
            f"{filename} must not import from process_lifecycle"
        )


def test_server_uses_recipe_io_not_recipe_loader_for_discovery() -> None:
    """server/ package must import recipe discovery from recipe.io, not from recipe.loader."""
    server_dir = SRC_ROOT / "server"
    combined_src = "\n".join(p.read_text() for p in server_dir.glob("*.py"))
    assert (
        "from autoskillit.recipe.io import" in combined_src
        or "from .recipe.io import" in combined_src
        or "from autoskillit.recipe import" in combined_src
    ), "server/ package must import recipe discovery functions from recipe.io or recipe package"
    assert "from autoskillit.recipe.loader import list_recipes" not in combined_src
    assert "from autoskillit.recipe.loader import load_recipe" not in combined_src


# ── L1 Package Runtime Isolation Tests ────────────────────────────────────────


def _type_checking_lines(tree: ast.AST) -> set[int]:
    """Return line numbers of all imports inside ``if TYPE_CHECKING:`` blocks."""
    lines: set[int] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.If)
            and isinstance(node.test, ast.Name)
            and node.test.id == "TYPE_CHECKING"
        ):
            for child in ast.walk(node):
                if isinstance(child, ast.Import | ast.ImportFrom):
                    lines.add(child.lineno)
    return lines


def test_execution_imports_only_core() -> None:
    """execution/ must not import from pipeline/, config/, or workspace/ at runtime."""
    forbidden = {"autoskillit.pipeline", "autoskillit.config", "autoskillit.workspace"}
    pkg = SRC_ROOT / "execution"
    assert pkg.exists(), "execution/ package must exist"
    violations = []
    for py in pkg.rglob("*.py"):
        tree = ast.parse(py.read_text())
        tc_lines = _type_checking_lines(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.lineno not in tc_lines:
                mod = node.module or ""
                if any(mod == f or mod.startswith(f + ".") for f in forbidden):
                    violations.append(f"{py.name}: {mod}")
    assert not violations, f"execution/ has forbidden runtime imports: {violations}"


def test_workspace_imports_only_core() -> None:
    """workspace/ must not import from pipeline/, config/, or execution/ at runtime."""
    forbidden = {"autoskillit.pipeline", "autoskillit.config", "autoskillit.execution"}
    pkg = SRC_ROOT / "workspace"
    assert pkg.exists(), "workspace/ package must exist"
    violations = []
    for py in pkg.rglob("*.py"):
        tree = ast.parse(py.read_text())
        tc_lines = _type_checking_lines(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.lineno not in tc_lines:
                mod = node.module or ""
                if any(mod == f or mod.startswith(f + ".") for f in forbidden):
                    violations.append(f"{py.name}: {mod}")
    assert not violations, f"workspace/ has forbidden imports: {violations}"


def test_pipeline_non_context_modules_import_only_core() -> None:
    """pipeline/audit.py, pipeline/gate.py, pipeline/tokens.py must not import config/."""
    restricted = ["audit.py", "gate.py", "tokens.py"]
    pkg = SRC_ROOT / "pipeline"
    assert pkg.exists(), "pipeline/ package must exist"
    violations = []
    for py in (pkg / name for name in restricted if (pkg / name).exists()):
        tree = ast.parse(py.read_text())
        tc_lines = _type_checking_lines(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.lineno not in tc_lines:
                mod = node.module or ""
                if "autoskillit.config" in mod:
                    violations.append(f"{py.name}: {mod}")
    assert not violations, f"Non-context pipeline modules import config/: {violations}"


def test_only_pipeline_context_imports_config() -> None:
    """Only pipeline/context.py may import from autoskillit.config."""
    pkg = SRC_ROOT / "pipeline"
    assert pkg.exists(), "pipeline/ package must exist"
    for py in pkg.rglob("*.py"):
        if py.name == "context.py":
            continue
        tree = ast.parse(py.read_text())
        tc_lines = _type_checking_lines(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.lineno not in tc_lines:
                mod = node.module or ""
                assert "autoskillit.config" not in mod, (
                    f"{py.name} must not import autoskillit.config (only context.py may)"
                )


# ── New L2 sub-package tests (T1–T9 from groupC plan) ─────────────────────────


def test_recipe_subpackage_importable() -> None:
    """T1: recipe/ package exposes all expected symbols."""
    from autoskillit.recipe import (  # noqa: F401
        Recipe,
        RecipeStep,
        analyze_dataflow,
        check_contract_staleness,
        find_recipe_by_name,
        generate_recipe_card,
        iter_steps_with_context,
        list_recipes,
        load_bundled_manifest,
        load_recipe,
        load_recipe_card,
        run_semantic_rules,
        validate_recipe,
        validate_recipe_cards,
    )


def test_contracts_module_has_staleitem() -> None:
    """T2: recipe/contracts.py exposes StaleItem and load_bundled_manifest."""
    from autoskillit.recipe.contracts import StaleItem, load_bundled_manifest  # noqa: F401

    assert StaleItem is not None
    assert load_bundled_manifest is not None


def test_validator_module_has_validate() -> None:
    """T3: recipe/validator.py exposes validate_recipe, run_semantic_rules, analyze_dataflow."""
    from autoskillit.recipe.validator import (  # noqa: F401
        analyze_dataflow,
        run_semantic_rules,
        validate_recipe,
    )

    assert validate_recipe is not None
    assert run_semantic_rules is not None
    assert analyze_dataflow is not None


def test_migration_subpackage_importable() -> None:
    """T4: migration/ package exposes MigrationEngine, applicable_migrations, FailureStore."""
    from autoskillit.migration import (  # noqa: F401
        FailureStore,
        MigrationEngine,
        applicable_migrations,
    )

    assert MigrationEngine is not None
    assert applicable_migrations is not None
    assert FailureStore is not None


def test_recipe_no_forbidden_imports() -> None:
    """T5: REQ-COMP-009 — recipe/ modules import only from core/ and workspace/."""
    recipe_pkg = SRC_ROOT / "recipe"
    assert recipe_pkg.exists(), "recipe/ package must exist"
    violations: list[str] = []
    for py in recipe_pkg.glob("*.py"):
        if py.name == "__init__.py":
            continue
        for stem, lineno in _extract_module_level_internal_imports(py):
            if stem not in {"core", "workspace", "recipe"}:
                violations.append(
                    f"recipe/{py.name}:{lineno} imports {stem} — forbidden by REQ-COMP-009"
                )
    assert not violations, "\n".join(violations)


def test_recipe_does_not_import_migration() -> None:
    """REQ-CNST-005: No module in recipe/ may import from migration/."""
    src = SRC_ROOT / "recipe"
    assert src.exists(), "recipe/ package must exist"
    violations: list[str] = []
    for py_file in sorted(src.rglob("*.py")):
        if "__pycache__" in py_file.parts:
            continue
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if "autoskillit.migration" in module:
                    violations.append(f"{py_file.name}: imports {module}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if "autoskillit.migration" in alias.name:
                        violations.append(f"{py_file.name}: imports {alias.name}")
    assert not violations, f"recipe/ imports migration/: {violations}"


def test_migration_no_forbidden_imports() -> None:
    """T6: REQ-COMP-010 — migration/ imports only from core/, execution/, and recipe/."""
    migration_pkg = SRC_ROOT / "migration"
    assert migration_pkg.exists(), "migration/ package must exist"
    violations: list[str] = []
    for py in migration_pkg.glob("*.py"):
        if py.name == "__init__.py":
            continue
        for stem, lineno in _extract_module_level_internal_imports(py):
            if stem not in {"core", "execution", "recipe", "migration"}:
                violations.append(
                    f"migration/{py.name}:{lineno} imports {stem} — forbidden by REQ-COMP-010"
                )
    assert not violations, "\n".join(violations)


def test_llm_triage_imports_from_contracts_not_validator() -> None:
    """T7: REQ-DSGN-007 — _llm_triage.py imports contract types, not recipe/validator.

    Accepts both direct sub-module import (recipe.contracts) and gateway import
    (autoskillit.recipe) since REQ-IMP-001 requires gateway imports for non-server/cli files.
    """
    src = (SRC_ROOT / "_llm_triage.py").read_text()
    assert (
        "recipe.contracts" in src
        or "recipe/contracts" in src
        or "from autoskillit.recipe import" in src
    ), "_llm_triage.py must import contract types from recipe package"
    assert "recipe.validator" not in src and "recipe_validator" not in src, (
        "_llm_triage.py must not import from recipe.validator or old recipe_validator"
    )


def test_old_flat_recipe_modules_removed() -> None:
    """T9a: old flat recipe modules must be deleted after sub-package migration."""
    for name in ("recipe_schema.py", "recipe_io.py", "recipe_loader.py", "recipe_validator.py"):
        assert not (SRC_ROOT / name).exists(), (
            f"{name} should be removed — code now lives in recipe/ sub-package"
        )


def test_old_flat_migration_modules_removed() -> None:
    """T9b: old flat migration modules must be deleted after sub-package migration."""
    for name in ("migration_engine.py", "migration_loader.py", "failure_store.py"):
        assert not (SRC_ROOT / name).exists(), (
            f"{name} should be removed — code now lives in migration/ sub-package"
        )


# ── New L3 package tests (groupD plan) ────────────────────────────────────────


def test_server_is_package() -> None:
    """server/ must be a package directory, not a flat module."""
    assert (SRC_ROOT / "server").is_dir(), "server/ directory must exist"
    assert (SRC_ROOT / "server" / "__init__.py").exists()
    assert not (SRC_ROOT / "server.py").exists(), "server.py flat module must be deleted"


def test_cli_is_package() -> None:
    """cli/ must be a package directory, not a flat module."""
    assert (SRC_ROOT / "cli").is_dir(), "cli/ directory must exist"
    assert (SRC_ROOT / "cli" / "__init__.py").exists()
    assert not (SRC_ROOT / "cli.py").exists(), "cli.py flat module must be deleted"


def test_server_file_count_under_limit() -> None:
    """server/ must not exceed 12 Python files (REQ-DSGN-002)."""
    py_files = list((SRC_ROOT / "server").glob("*.py"))
    assert len(py_files) <= 12, f"server/ has {len(py_files)} files, max is 12"


def test_git_operations_moved_to_server_package() -> None:
    """git_operations.py must be removed; its logic lives in server/git.py."""
    assert not (SRC_ROOT / "git_operations.py").exists()
    assert (SRC_ROOT / "server" / "git.py").exists()


def test_doctor_moved_to_cli_package() -> None:
    """_doctor.py must be removed; its logic lives in cli/_doctor.py."""
    assert not (SRC_ROOT / "_doctor.py").exists()
    assert (SRC_ROOT / "cli" / "_doctor.py").exists()


# ── New REQ-CNST tests (groupE) ───────────────────────────────────────────────


def test_no_file_exceeds_1000_lines() -> None:
    """REQ-CNST-002: No Python file in src/autoskillit/ may exceed 1,000 lines."""
    violations: list[str] = []
    for py_file in SRC_ROOT.rglob("*.py"):
        if "__pycache__" in py_file.parts:
            continue
        line_count = len(py_file.read_text().splitlines())
        if line_count > 1000:
            violations.append(f"{_rel(py_file)}: {line_count} lines")
    assert not violations, "Files exceeding 1,000 lines:\n" + "\n".join(
        f"  {v}" for v in violations
    )


def test_no_subpackage_exceeds_10_files() -> None:
    """REQ-CNST-003: No sub-package directory may contain more than 10 Python files.

    server/ is exempt at 12 files to accommodate tools_clone and tools_integrations modules.
    recipe/ is exempt at 11 files to accommodate staleness_cache module (Part A).
    """
    EXEMPTIONS: dict[str, int] = {"server": 12, "recipe": 11}
    violations: list[str] = []
    for sub_dir in sorted(SRC_ROOT.iterdir()):
        if not sub_dir.is_dir() or sub_dir.name.startswith("_") or sub_dir.name == "__pycache__":
            continue
        py_files = list(sub_dir.glob("*.py"))
        limit = EXEMPTIONS.get(sub_dir.name, 10)
        if len(py_files) > limit:
            violations.append(f"{sub_dir.name}/: {len(py_files)} Python files (max {limit})")
    assert not violations, "Sub-packages exceeding 10 Python files:\n" + "\n".join(
        f"  {v}" for v in violations
    )


def test_core_has_no_autoskillit_imports() -> None:
    """REQ-CNST-004: core/ modules must not import from any autoskillit sub-package."""
    core_dir = SRC_ROOT / "core"
    assert core_dir.exists(), "core/ package must exist"
    violations: list[str] = []
    for py_file in core_dir.glob("*.py"):
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                parts = node.module.split(".")
                if parts[0] == "autoskillit" and len(parts) > 1:
                    violations.append(f"core/{py_file.name}:{node.lineno}: imports {node.module}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    parts = alias.name.split(".")
                    if parts[0] == "autoskillit" and len(parts) > 1:
                        violations.append(
                            f"core/{py_file.name}:{node.lineno}: imports {alias.name}"
                        )
    assert not violations, "core/ has autoskillit internal imports:\n" + "\n".join(
        f"  {v}" for v in violations
    )


def test_isolated_modules_do_not_import_server_or_cli() -> None:
    """REQ-CNST-007: Root-level isolated modules must not import from server/ or cli/."""
    isolated = ["_llm_triage.py", "smoke_utils.py", "version.py"]
    forbidden_prefixes = ("autoskillit.server", "autoskillit.cli")
    violations: list[str] = []
    for filename in isolated:
        py_file = SRC_ROOT / filename
        if not py_file.exists():
            continue
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                mod = node.module
                if any(mod == f or mod.startswith(f + ".") for f in forbidden_prefixes):
                    violations.append(f"{filename}:{node.lineno}: imports {mod}")
    assert not violations, "Root-level isolated modules import server/ or cli/:\n" + "\n".join(
        f"  {v}" for v in violations
    )


def test_server_tool_handlers_have_no_business_logic() -> None:
    """REQ-CNST-008: @mcp.tool handler functions must contain no comprehensions or for-loops.

    Tool handlers must only: call _require_enabled(), delegate to domain functions,
    and return results. Comprehensions and for-loops indicate logic that belongs
    in a domain layer module.
    """
    server_dir = SRC_ROOT / "server"
    violations: list[str] = []
    for py_file in sorted(server_dir.glob("tools_*.py")):
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not any(_is_mcp_tool_decorator(d) for d in node.decorator_list):
                continue
            # Walk only the function body for business-logic patterns
            body_module = ast.Module(body=node.body, type_ignores=[])
            for child in ast.walk(body_module):
                if isinstance(child, (ast.ListComp, ast.DictComp, ast.SetComp, ast.GeneratorExp)):
                    violations.append(
                        f"server/{py_file.name}: {node.name}() line {child.lineno}: "
                        f"comprehension found — move to domain layer"
                    )
                elif isinstance(child, ast.For):
                    violations.append(
                        f"server/{py_file.name}: {node.name}() line {child.lineno}: "
                        f"for-loop found — move to domain layer"
                    )
    assert not violations, "Tool handlers contain business logic:\n" + "\n".join(
        f"  {v}" for v in violations
    )


# ── REQ-ARCH-001: No cross-package submodule imports ─────────────────────────


def test_no_cross_package_submodule_imports() -> None:
    """REQ-ARCH-001: No module outside package X may import from autoskillit.X.<submodule>.

    Intra-package imports (e.g., server/__init__.py importing autoskillit.server.helpers)
    are explicitly allowed. TYPE_CHECKING-guarded imports are excluded.
    """
    AUTOSKILLIT_ROOT = SRC_ROOT
    violations: list[str] = []

    for path in _SOURCE_FILES:
        rel = path.relative_to(AUTOSKILLIT_ROOT)
        # Determine this file's immediate package (None for root-level modules)
        file_package: str | None = rel.parts[0] if len(rel.parts) > 1 else None

        for node in _runtime_import_froms(path):
            if node.module is None:
                continue
            parts = node.module.split(".")
            # Flag: autoskillit.<pkg>.<submod> where <pkg> != file_package
            if len(parts) >= 3 and parts[0] == "autoskillit":
                target_package = parts[1]
                if file_package != target_package:
                    violations.append(
                        f"{path.relative_to(AUTOSKILLIT_ROOT)}:{node.lineno} "
                        f"imports from autoskillit.{target_package}.{parts[2]}"
                    )

    assert not violations, (
        "Cross-package submodule imports detected (use package __init__ instead):\n"
        + "\n".join(violations)
    )


# ── REQ-ARCH-002: ToolContext service fields use Protocol types ───────────────


def test_tool_context_service_fields_use_protocol_types() -> None:
    """REQ-ARCH-002: Every non-exempt ToolContext field must use a Protocol from core/types.py.

    Exempt fields:
    - plugin_dir: str primitive (explicitly stated in the requirement)
    - config: AutomationConfig dataclass (configuration container, not a service interface)
    """
    AUTOSKILLIT_ROOT = SRC_ROOT

    # Collect Protocol class names from core/types.py via AST
    types_path = AUTOSKILLIT_ROOT / "core" / "types.py"
    types_tree = ast.parse(types_path.read_text())
    core_protocols: set[str] = set()
    for node in ast.walk(types_tree):
        if isinstance(node, ast.ClassDef):
            for base in node.bases:
                base_str = ast.unparse(base)
                if "Protocol" in base_str:
                    core_protocols.add(node.name)
                    break

    # Collect ToolContext field annotations via AST
    context_path = AUTOSKILLIT_ROOT / "pipeline" / "context.py"
    context_tree = ast.parse(context_path.read_text())

    EXEMPT = {"plugin_dir", "config"}
    violations: list[str] = []

    for node in ast.walk(context_tree):
        if isinstance(node, ast.ClassDef) and node.name == "ToolContext":
            for item in node.body:
                if not isinstance(item, ast.AnnAssign):
                    continue
                field_name = ast.unparse(item.target)
                if field_name in EXEMPT:
                    continue

                # Collect all type names from annotation (unwraps Union/Optional)
                ann_str = ast.unparse(item.annotation)
                # Strip Optional[...] / X | None wrappers; collect bare names
                type_names = {
                    n.strip().strip("[]")
                    for n in ann_str.replace("|", ",").split(",")
                    if n.strip() not in ("None", "")
                }
                # Remove generic parameters, e.g. "list[str]" → "list"
                type_names = {n.split("[")[0] for n in type_names}

                for type_name in type_names:
                    if type_name not in core_protocols and type_name not in (
                        "str",
                        "int",
                        "float",
                        "bool",
                        "bytes",
                        "None",
                    ):
                        violations.append(
                            f"ToolContext.{field_name}: '{type_name}' is not a "
                            f"Protocol in core/types.py"
                        )

    assert not violations, (
        "ToolContext fields use concrete types instead of core/types.py Protocols:\n"
        + "\n".join(violations)
    )


def test_make_context_wires_all_optional_toolcontext_fields() -> None:
    """REQ-ARCH-002: make_context() must assign every optional ToolContext field.

    Self-closing: parses server/_factory.py via AST to discover all field assignments
    inside make_context(), then cross-checks against all ToolContext fields that have
    field(default=None). Fails if any optional field exists in ToolContext but is
    neither assigned in the ToolContext() constructor call nor in a post-construction
    assignment within make_context().
    """
    from autoskillit.pipeline.context import ToolContext

    # All optional service fields (field(default=None))
    optional_field_names = {
        name for name, f in ToolContext.__dataclass_fields__.items() if f.default is None
    }

    # Parse server/_factory.py via AST
    factory_path = SRC_ROOT / "server" / "_factory.py"
    tree = ast.parse(factory_path.read_text())

    # Find make_context() function body
    assigned_fields: set[str] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name == "make_context"):
            continue
        for stmt in ast.walk(ast.Module(body=node.body, type_ignores=[])):
            # Capture keyword args in ToolContext(...) constructor call
            if isinstance(stmt, ast.Call):
                func_str = ast.unparse(stmt.func)
                if "ToolContext" in func_str:
                    for kw in stmt.keywords:
                        if kw.arg:
                            assigned_fields.add(kw.arg)
            # Capture post-construction assignments: ctx.field_name = ...
            if isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name):
                        assigned_fields.add(target.attr)

    unwired = optional_field_names - assigned_fields
    assert not unwired, (
        f"make_context() does not assign these optional ToolContext fields: {unwired}. "
        "Add wiring in server/_factory.py make_context()."
    )


# ── REQ-ARCH-003: server/tools_*.py import only allowed packages ──────────────


def test_server_tools_import_only_allowed_packages() -> None:
    """REQ-ARCH-003: server/tools_*.py may only import from autoskillit.core,
    autoskillit.pipeline, and intra-package autoskillit.server.*. TYPE_CHECKING exempt.
    """
    ALLOWED = {"core", "pipeline", "server"}
    tools_files = [
        p for p in _SOURCE_FILES if p.parent.name == "server" and p.stem.startswith("tools_")
    ]
    violations: list[str] = []

    for path in tools_files:
        for node in _runtime_import_froms(path):
            if node.module is None:
                continue
            parts = node.module.split(".")
            if parts[0] == "autoskillit" and len(parts) >= 2:
                if parts[1] not in ALLOWED:
                    violations.append(
                        f"{path.name}:{node.lineno} imports from "
                        f"autoskillit.{parts[1]} (not in allowed set {ALLOWED})"
                    )

    assert not violations, (
        "server/tools_*.py files import from disallowed autoskillit sub-packages:\n"
        + "\n".join(violations)
    )


# ── REQ-ARCH-004: __all__ completeness ───────────────────────────────────────


def test_package_all_matches_exports() -> None:
    """REQ-ARCH-004: Each package __init__.__all__ must match its exported symbol set.

    Two checks:
    1. Every name in __all__ is importable from the package (no dead entries).
    2. Every public name re-exported via relative or autoskillit.* imports in __init__.py
       appears in __all__ (no undeclared exports).

    Packages without __all__ (server, root autoskillit) are skipped.
    """
    import importlib

    AUTOSKILLIT_ROOT = SRC_ROOT
    PACKAGES_WITH_ALL = [
        "core",
        "config",
        "pipeline",
        "execution",
        "workspace",
        "recipe",
        "migration",
        "cli",
    ]
    violations: list[str] = []

    for pkg_name in PACKAGES_WITH_ALL:
        module = importlib.import_module(f"autoskillit.{pkg_name}")
        all_list: list[str] = getattr(module, "__all__", None)  # type: ignore[assignment]
        if all_list is None:
            continue  # package opted out of __all__ — skip

        # Check 1: every __all__ entry is importable
        for name in all_list:
            if not hasattr(module, name):
                violations.append(
                    f"autoskillit.{pkg_name}: '{name}' in __all__ but not importable"
                )

        # Check 2: every public name from relative / intra-package imports is in __all__
        # Only intra-package absolute imports (autoskillit.{pkg_name}.*) are checked —
        # cross-package imports (e.g. `from autoskillit.core import get_logger` in
        # recipe/__init__.py) are internal helpers, not re-exports, and must be excluded.
        init_path = AUTOSKILLIT_ROOT / pkg_name / "__init__.py"
        for node in _runtime_import_froms(init_path):
            is_relative = node.level and node.level > 0
            is_intra_package = node.module and node.module.startswith(f"autoskillit.{pkg_name}.")
            if not (is_relative or is_intra_package):
                continue  # skip stdlib / third-party / cross-package imports

            for alias in node.names:
                name = alias.asname if alias.asname else alias.name
                if name.startswith("_") or name == "*":
                    continue
                if name not in all_list:
                    violations.append(
                        f"autoskillit.{pkg_name}: '{name}' re-exported via import "
                        f"but not in __all__"
                    )

    assert not violations, "__all__ completeness violations:\n" + "\n".join(violations)


# ── Symbolic Rule Registry tests (groupG) ─────────────────────────────────────


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


def test_violation_str_no_prefix_without_rule_id() -> None:
    """Violation with empty rule_id uses the legacy str format (no prefix)."""
    v = Violation(file=Path("src/x.py"), line=5, col=0, message="some issue", rule_id="", lens="")
    s = str(v)
    assert not s.startswith("["), f"Expected no prefix for rule_id='', got: {s!r}"
    assert "some issue" in s


def test_no_raw_ctx_notification_calls_in_tool_handlers() -> None:
    """Architecture guard: all ctx.info/error/warning/debug calls in tools_*.py
    must be replaced by _notify. If any raw ctx.* call exists, a developer has
    bypassed the validation layer and this test fails immediately.
    """
    import ast

    server_dir = Path("src/autoskillit/server")
    violations = []
    for path in sorted(server_dir.glob("tools_*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "ctx"
                and node.func.attr in ("info", "error", "warning", "debug")
            ):
                violations.append(f"{path.name}:{node.lineno}")
    assert not violations, (
        "Raw ctx notification calls found — use _notify() from server/helpers.py:\n"
        + "\n".join(violations)
    )


def test_all_tool_extra_keys_are_not_reserved() -> None:
    """Architecture guard: statically verify that no literal extra={} dict passed
    to _notify() in tools_*.py contains a key matching a reserved LogRecord
    attribute. Catches reserved-key collisions at test time, before any runtime.
    """
    import ast

    from autoskillit.core.types import RESERVED_LOG_RECORD_KEYS

    server_dir = Path("src/autoskillit/server")
    violations = []
    for path in sorted(server_dir.glob("tools_*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            # Find: _notify(ctx, level, msg, logger_name, extra={...})
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "_notify"
            ):
                for kw in node.keywords:
                    if kw.arg == "extra" and isinstance(kw.value, ast.Dict):
                        for key_node in kw.value.keys:
                            if isinstance(key_node, ast.Constant) and isinstance(
                                key_node.value, str
                            ):
                                if key_node.value in RESERVED_LOG_RECORD_KEYS:
                                    violations.append(
                                        f"{path.name}:{node.lineno}: "
                                        f"extra key {key_node.value!r} is reserved"
                                    )
    assert not violations, "Reserved LogRecord keys found in _notify() extra dicts:\n" + "\n".join(
        violations
    )


def test_default_prefix_convention_enforced() -> None:
    """P12-F1..F4: All concrete protocol implementations in pipeline/ and execution/
    must use the Default* naming convention.

    Old names (RealSubprocessRunner, GateState, AuditLog, TokenLog) must not exist.
    New names must be importable.
    """
    import importlib

    # Renames that must be complete
    renames = [
        ("autoskillit.execution.process", "DefaultSubprocessRunner", "RealSubprocessRunner"),
        ("autoskillit.pipeline.gate", "DefaultGateState", "GateState"),
        ("autoskillit.pipeline.audit", "DefaultAuditLog", "AuditLog"),
        ("autoskillit.pipeline.tokens", "DefaultTokenLog", "TokenLog"),
    ]
    missing_new: list[str] = []
    surviving_old: list[str] = []
    for module_name, new_name, old_name in renames:
        mod = importlib.import_module(module_name)
        if not hasattr(mod, new_name):
            missing_new.append(f"{module_name}.{new_name}")
        if hasattr(mod, old_name):
            surviving_old.append(f"{module_name}.{old_name}")
    assert not missing_new, f"New Default* names not found: {missing_new}"
    assert not surviving_old, f"Old names still present: {surviving_old}"


# ---------------------------------------------------------------------------
# groupC Part A tests
# ---------------------------------------------------------------------------


def test_recipe_rules_module_exists() -> None:
    """P8: recipe/rules.py must exist and be importable."""
    from autoskillit.recipe import rules  # noqa: F401

    assert rules is not None


def test_semantic_rule_functions_defined_in_rules_module() -> None:
    """P8: Semantic rule functions must be defined in recipe/rules.py."""
    from autoskillit.recipe.validator import _check_outdated_version

    assert _check_outdated_version.__module__ == "autoskillit.recipe.rules"


def test_installed_version_in_core_types() -> None:
    """P3-F2: AUTOSKILLIT_INSTALLED_VERSION must be in autoskillit.core."""
    from autoskillit.core import AUTOSKILLIT_INSTALLED_VERSION

    assert isinstance(AUTOSKILLIT_INSTALLED_VERSION, str) and AUTOSKILLIT_INSTALLED_VERSION


def test_rules_module_no_autoskillit_init_import() -> None:
    """P3-F2: recipe/rules.py must not import from autoskillit top-level __init__."""
    rules_path = SRC_ROOT / "recipe" / "rules.py"
    assert "from autoskillit import __version__" not in rules_path.read_text()


def test_recipe_api_module_exists() -> None:
    """P14-F1/F2: recipe/_api.py must exist and be importable."""
    import autoskillit.recipe._api  # noqa: F401


def test_default_recipe_repository_in_repository_module() -> None:
    """P2-F1: DefaultRecipeRepository must live in recipe/repository.py."""
    from autoskillit.recipe.repository import DefaultRecipeRepository  # noqa: F401


def test_default_recipe_repository_not_in_io() -> None:
    """P2-F1: DefaultRecipeRepository must be removed from recipe/io.py."""
    io_path = SRC_ROOT / "recipe" / "io.py"
    assert "class DefaultRecipeRepository" not in io_path.read_text()


def test_migration_api_module_exists() -> None:
    """P14-F3: migration/_api.py must exist and be importable."""
    import autoskillit.migration._api  # noqa: F401


def test_migration_engine_no_module_level_recipe_imports() -> None:
    """P4-F1: migration/engine.py must have no module-level recipe imports."""
    engine_path = SRC_ROOT / "migration" / "engine.py"
    recipe_violations = [
        (stem, ln)
        for stem, ln in _extract_module_level_internal_imports(engine_path)
        if stem == "recipe"
    ]
    assert not recipe_violations, f"module-level recipe imports remain: {recipe_violations}"


def test_no_file_based_path_resolution_outside_paths_module() -> None:
    """All __file__-based path construction must go through core/paths.py.

    No module other than core/paths.py may use Path(__file__).parent for
    resource or directory resolution. This rule is automatically enforced
    so future developers receive an immediate CI failure with a clear message.
    """
    import ast

    violations = []
    src_root = Path(__file__).parent.parent / "src" / "autoskillit"
    allowed_file = src_root / "core" / "paths.py"

    for py_file in src_root.rglob("*.py"):
        if py_file == allowed_file:
            continue
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            # Detect: Path(__file__).parent or Path(__file__).parent.parent
            if (
                isinstance(node, ast.Attribute)
                and node.attr == "parent"
                and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id == "Path"
                and len(node.value.args) == 1
                and isinstance(node.value.args[0], ast.Name)
                and node.value.args[0].id == "__file__"
            ):
                violations.append(
                    f"{py_file.relative_to(src_root)}:{node.lineno} — "
                    "Path(__file__).parent used for path resolution. "
                    "Use core.paths.pkg_root() instead."
                )

    assert not violations, (
        "Forbidden __file__-based path resolution found outside core/paths.py:\n"
        + "\n".join(violations)
    )


def test_make_context_no_isinstance_against_concrete_migration() -> None:
    """REQ-P12-001: _factory.py must not isinstance-check DefaultMigrationService."""
    import ast
    from pathlib import Path

    factory_src = (Path(__file__).parent.parent / "src/autoskillit/server/_factory.py").read_text()
    tree = ast.parse(factory_src)

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "isinstance":
                args = node.args
                if len(args) == 2:
                    second = args[1]
                    name = (
                        second.id
                        if isinstance(second, ast.Name)
                        else (second.attr if isinstance(second, ast.Attribute) else None)
                    )
                    assert name != "DefaultMigrationService", (
                        "_factory.py must not downcast to DefaultMigrationService via isinstance. "
                        "Wire using Protocol only."
                    )


# ---------------------------------------------------------------------------
# ARCH-007: Exhaustive TerminationReason dispatch
# ---------------------------------------------------------------------------


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


def _check_channel_confirmation_dispatch_exhaustive(src_dir: Path) -> list[str]:
    """
    T7 / ARCH-007 extension: Detect functions that dispatch over ChannelConfirmation
    via if/elif chains rather than exhaustive match/case + assert_never.

    A "dispatch table" is detected when a single FunctionDef contains comparisons
    to ≥2 distinct ChannelConfirmation.* values (CHANNEL_A, CHANNEL_B, UNMONITORED).
    A single-value guard is exempt.

    Returns a list of violation strings for failing tests.
    """
    violations = []
    for py_file in src_dir.rglob("*.py"):
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            cc_values: set[str] = set()
            has_assert_never = False
            has_match = False
            for child in ast.walk(node):
                if isinstance(child, ast.Compare):
                    for comparator in child.comparators:
                        if (
                            isinstance(comparator, ast.Attribute)
                            and isinstance(comparator.value, ast.Name)
                            and comparator.value.id == "ChannelConfirmation"
                        ):
                            cc_values.add(comparator.attr)
                        elif isinstance(comparator, ast.Tuple):
                            for elt in comparator.elts:
                                if (
                                    isinstance(elt, ast.Attribute)
                                    and isinstance(elt.value, ast.Name)
                                    and elt.value.id == "ChannelConfirmation"
                                ):
                                    cc_values.add(elt.attr)
                if hasattr(ast, "Match") and isinstance(child, ast.Match):
                    has_match = True
                if (
                    isinstance(child, ast.Call)
                    and isinstance(child.func, ast.Name)
                    and child.func.id == "assert_never"
                ):
                    has_assert_never = True
            if len(cc_values) >= 2 and not (has_match and has_assert_never):
                violations.append(
                    f"{py_file.relative_to(src_dir.parent.parent)}:{node.lineno}: "
                    f"{node.name}() dispatches on {cc_values} via if/elif — "
                    f"use match/case + assert_never"
                )
    return violations


def test_arch007_channel_confirmation_dispatch_uses_match_case() -> None:
    """
    T7 / ARCH-007 extension: Any function in execution/ that dispatches on ≥2
    distinct ChannelConfirmation values via if/elif must use match/case with
    assert_never. Single-value guard checks are exempt.
    """
    violations = _check_channel_confirmation_dispatch_exhaustive(SRC_ROOT / "execution")
    assert violations == [], (
        "Non-exhaustive ChannelConfirmation dispatch tables found:\n" + "\n".join(violations)
    )


def _find_enclosing_function(node: ast.AST, tree: ast.AST) -> str | None:
    for parent in ast.walk(tree):
        if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for child in ast.walk(parent):
                if child is node:
                    return parent.name
    return None


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


def test_monkeypatch_targets_do_not_bypass_package_reexports() -> None:
    """Every monkeypatch.setattr path must target the namespace production code resolves.

    When autoskillit.X re-exports 'name' from autoskillit.X.submodule via __init__.py,
    patching autoskillit.X.submodule.name does NOT affect autoskillit.X.name.
    All patches of the form autoskillit.X.submodule.name, where X is a sub-package
    that re-exports 'name' FROM that exact submodule, are wrong and must be corrected.

    Note: patching autoskillit.X.B.name where X imports 'name' from a DIFFERENT submodule
    (not B) is correct — it targets the local binding in B, which is the namespace that
    module B's own functions resolve.
    """
    import ast
    import importlib
    import inspect
    import re

    # Match string literals in monkeypatch.setattr("autoskillit.A.B.C", ...)
    # where A is a sub-package, B is a submodule, C is the name.
    pattern = re.compile(
        r'monkeypatch\.setattr\s*\(\s*["\']'
        r"(autoskillit\.\w+\.\w+\.\w+)"
        r'["\']'
    )

    violations: list[str] = []
    tests_dir = Path(__file__).parent

    for test_file in sorted(tests_dir.glob("test_*.py")):
        source = test_file.read_text()
        for match in pattern.finditer(source):
            full_path = match.group(1)
            # Split: autoskillit . pkg . submodule . name
            parts = full_path.split(".")
            if len(parts) != 4:
                continue
            _, pkg, submod, name = parts
            parent_pkg = f"autoskillit.{pkg}"
            try:
                parent_mod = importlib.import_module(parent_pkg)
            except ImportError:
                continue
            if not hasattr(parent_mod, name):
                continue
            # Refine: only flag if the parent pkg actually imports 'name' FROM this
            # exact submodule. If it imports 'name' from a different module (e.g.
            # autoskillit.migration imports applicable_migrations from .loader, not
            # .engine), then the patch targets a local binding in 'submod' — which
            # is the correct mock target for module-level imports in that submodule.
            try:
                parent_source = inspect.getsource(parent_mod)
                tree = ast.parse(parent_source)
            except Exception:
                # Can't inspect source — conservatively flag as violation.
                imports_from_this_submod = True
            else:
                imports_from_this_submod = False
                for node in ast.walk(tree):
                    if not isinstance(node, ast.ImportFrom):
                        continue
                    is_relative_from_submod = node.level == 1 and node.module == submod
                    is_absolute_from_submod = (
                        node.level == 0 and node.module == f"autoskillit.{pkg}.{submod}"
                    )
                    if is_relative_from_submod or is_absolute_from_submod:
                        for alias in node.names:
                            if (alias.asname or alias.name) == name:
                                imports_from_this_submod = True
                                break
                    if imports_from_this_submod:
                        break
            if imports_from_this_submod:
                line_no = source[: match.start()].count("\n") + 1
                violations.append(
                    f"{test_file.name}:{line_no}: patches {full_path!r} "
                    f"but '{name}' is re-exported at '{parent_pkg}.{name}'. "
                    f"Patch '{parent_pkg}.{name}' instead."
                )

    assert not violations, "Monkeypatch paths bypass package re-exports:\n" + "\n".join(
        f"  {v}" for v in violations
    )
