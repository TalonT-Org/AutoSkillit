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
from pathlib import Path
from typing import NamedTuple

import pytest

SRC_ROOT = Path(__file__).parent.parent / "src" / "autoskillit"

_SENSITIVE_KEYWORDS = frozenset({"token", "secret", "password", "key", "api_key", "auth"})
_LOGGER_METHODS = frozenset({"debug", "info", "warning", "error", "critical", "exception"})
_PRINT_EXEMPT = frozenset({"app.py", "_doctor.py"})
_BROAD_EXCEPTION_TYPES: frozenset[str] = frozenset({"Exception", "BaseException"})


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

    def __str__(self) -> str:
        return f"{_rel(self.file)}:{self.line}:{self.col}: {self.message}"


class ArchitectureViolationVisitor(ast.NodeVisitor):
    def __init__(self, filepath: Path) -> None:
        self.filepath = filepath
        self.violations: list[Violation] = []
        self._print_exempt = filepath.name in _PRINT_EXEMPT
        self._asyncio_pipe_exempt = filepath.name in _ASYNCIO_PIPE_EXEMPT

    def _add(self, node: ast.AST, message: str) -> None:
        self.violations.append(
            Violation(
                self.filepath,
                node.lineno,  # type: ignore[attr-defined]
                node.col_offset,  # type: ignore[attr-defined]
                message,
            )
        )

    def visit_Attribute(self, node: ast.Attribute) -> None:
        """Rule 5 (visitor): asyncio.PIPE is banned outside process_lifecycle.py."""
        if (
            not self._asyncio_pipe_exempt
            and node.attr == "PIPE"
            and isinstance(node.value, ast.Name)
            and node.value.id == "asyncio"
        ):
            self.violations.append(
                Violation(
                    self.filepath,
                    node.lineno,
                    node.col_offset,
                    "asyncio.PIPE is banned; use create_temp_io() from process_lifecycle",
                )
            )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        # Rule 1 (visitor): no print() — ruff cannot enforce this in production-only files
        if not self._print_exempt and isinstance(node.func, ast.Name) and node.func.id == "print":
            self._add(node, "print() call — use logger instead")

        # Rule 2 (visitor): no sensitive kwargs in logger calls — not expressible in ruff
        if isinstance(node.func, ast.Attribute) and node.func.attr in _LOGGER_METHODS:
            for kw in node.keywords:
                if kw.arg and any(s in kw.arg.lower() for s in _SENSITIVE_KEYWORDS):
                    self._add(node, f"sensitive kwarg '{kw.arg}' passed to logger")

        # Rule 6 (visitor): get_logger must be called with __name__
        func = node.func
        func_name = func.id if isinstance(func, ast.Name) else None
        if func_name == "get_logger" and node.args:
            first_arg = node.args[0]
            if not (isinstance(first_arg, ast.Name) and first_arg.id == "__name__"):
                self._add(
                    node,
                    "get_logger() must be called with __name__, not a literal or other value",
                )

        # Rule 7 (visitor): no f-string with sensitive variable names in logger positional args
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
                                    f"f-string log message interpolates sensitive variable "
                                    f"'{var_name}' — use structlog kwargs instead",
                                )

        self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        """Rule 3 (visitor): broad except without logger call or re-raise → silent swallow."""
        is_broad = node.type is None or (
            isinstance(node.type, ast.Name) and node.type.id in _BROAD_EXCEPTION_TYPES
        )
        if is_broad and not _has_log_call(node.body) and not _has_reraise(node.body):
            type_label = ast.unparse(node.type) if node.type else "bare except"
            self._add(
                node,
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

# ── Rule 1: Import layer enforcement ─────────────────────────────────────────
LAYER_ASSIGNMENTS: dict[str, int] = {
    # ── Layer 0: Foundation ── no autoskillit imports ─────────────────────────
    # Note: core/, config/, pipeline/gate.py are sub-packages/modules. Their
    # flat .py equivalents (types, _logging, _io, _yaml, _gate, config) no
    # longer exist — entries below that lack a matching .py file are skipped.
    "core": 0,
    "gate": 0,  # pipeline/gate.py (formerly _gate.py)
    "version": 0,
    "smoke_utils": 0,
    # ── Layer 1: Basic Services ── import only L0 ─────────────────────────────
    # flat-module equivalents removed; sub-module stems are checked when present
    "audit": 1,  # pipeline/audit.py
    "tokens": 1,  # pipeline/tokens.py
    "session": 1,  # execution/session.py
    "process": 1,  # execution/process.py
    "testing": 1,  # execution/testing.py
    "db": 1,  # execution/db.py
    "cleanup": 1,  # workspace/cleanup.py
    "skills": 1,  # workspace/skills.py
    # ── Layer 2: Domain Services ── import L0 + L1 ────────────────────────────
    "context": 2,  # pipeline/context.py (formerly _context.py)
    "recipe": 2,  # L2 recipe/ sub-package
    "migration": 2,  # L2 migration/ sub-package
    # ── Layer 3: Orchestration + Server ── import L0–L2 ───────────────────────
    "headless": 3,  # execution/headless.py
    "server": 3,  # server/ package (skipped if server.py absent — uses server/ dir)
}
_LAYER_EXEMPT: frozenset[str] = frozenset({"cli", "__init__", "__main__"})

# ── Rule 2: Singleton definition locality ─────────────────────────────────────
# "server" allows mcp = FastMCP(...); "cli" allows app = App(...) etc.
SINGLETON_ALLOWED_MODULES: frozenset[str] = frozenset(
    {
        # pipeline/ package: singletons _audit_log and _token_log
        "audit",
        "tokens",
        # server/__init__.py: mcp = FastMCP(...)
        "__init__",
        # cli/app.py: app = App(...), config_app = App(...), etc.
        "app",
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


def test_server_imports_gate_registry() -> None:
    """server/ package must import GATED_TOOLS and UNGATED_TOOLS from autoskillit.pipeline.gate.

    N6 requirement: the server package is the authoritative runtime consumer of the
    gate registry, not only the test suite.
    """
    server_dir = SRC_ROOT / "server"
    imported: set[str] = set()
    for py_file in server_dir.glob("*.py"):
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "autoskillit.pipeline.gate":
                for alias in node.names:
                    imported.add(alias.name)

    missing = {"GATED_TOOLS", "UNGATED_TOOLS"} - imported
    assert not missing, (
        f"server/ package must import from autoskillit.pipeline.gate: {sorted(missing)}"
    )


# ── Rule 1: test_import_layer_enforcement ─────────────────────────────────────


@pytest.mark.parametrize(
    "mod_name,layer",
    [(k, v) for k, v in LAYER_ASSIGNMENTS.items()],
)
def test_import_layer_enforcement(mod_name: str, layer: int) -> None:
    """Each module may only import from same or lower layer modules (no upward imports)."""
    src_file = SRC_ROOT / f"{mod_name}.py"
    if not src_file.exists():
        pytest.skip(f"{mod_name}.py not found — prerequisite group not merged")

    violations: list[str] = []
    for imported_stem, lineno in _extract_module_level_internal_imports(src_file):
        if imported_stem not in LAYER_ASSIGNMENTS:
            # Unknown module — not in the assignment table; skip (new module, update table)
            continue
        imported_layer = LAYER_ASSIGNMENTS[imported_stem]
        if imported_layer > layer:
            violations.append(
                f"  line {lineno}: {mod_name} (L{layer}) imports "
                f"{imported_stem} (L{imported_layer}) — upward import"
            )

    assert not violations, f"Layer violations in {mod_name}.py:\n" + "\n".join(violations)


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
                if LAYER_ASSIGNMENTS.get(imported, 0) > 1:
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

    Prevents undocumented modules from silently accumulating after
    a new module is added without updating the Architecture section.
    """
    claude_path = Path(__file__).parent.parent / "CLAUDE.md"
    content = claude_path.read_text()
    src_root = Path(__file__).parent.parent / "src" / "autoskillit"

    missing = [
        py_file.name for py_file in sorted(src_root.glob("*.py")) if py_file.name not in content
    ]

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
    ), "server/ package must import recipe discovery functions from recipe.io"
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
    """T7: REQ-DSGN-007 — _llm_triage.py imports from recipe/contracts, not recipe/validator."""
    src = (SRC_ROOT / "_llm_triage.py").read_text()
    assert "recipe.contracts" in src or "recipe/contracts" in src, (
        "_llm_triage.py must import from recipe.contracts"
    )
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
    """server/ must not exceed 10 Python files (REQ-DSGN-002)."""
    py_files = list((SRC_ROOT / "server").glob("*.py"))
    assert len(py_files) <= 10, f"server/ has {len(py_files)} files, max is 10"


def test_git_operations_moved_to_server_package() -> None:
    """git_operations.py must be removed; its logic lives in server/git.py."""
    assert not (SRC_ROOT / "git_operations.py").exists()
    assert (SRC_ROOT / "server" / "git.py").exists()


def test_doctor_moved_to_cli_package() -> None:
    """_doctor.py must be removed; its logic lives in cli/_doctor.py."""
    assert not (SRC_ROOT / "_doctor.py").exists()
    assert (SRC_ROOT / "cli" / "_doctor.py").exists()
