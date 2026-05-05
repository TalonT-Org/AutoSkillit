"""MCP tool registry + import layer contracts + cross-package rules.

Tests:
  - MCP tool registry completeness (bidirectional equality with _gate registry)
  - Import layer contract (each module only imports from same or lower layer)
  - L1 package runtime isolation
  - Cross-package submodule import restrictions
  - server/tools/tools_*.py import constraints
  - Notification and convention guards
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.arch._helpers import (
    _SOURCE_FILES,
    SRC_ROOT,
    _extract_module_level_internal_imports,
    _is_mcp_tool_decorator,
    _rel,
    _runtime_import_froms,
)
from tests.arch._rules import RuleDescriptor

# ── Sub-package layer registry ────────────────────────────────────────────────
SUBPACKAGE_LAYERS: dict[str, int] = {
    # Layer 0: core/ — zero autoskillit internal imports
    "core": 0,
    # Layer 1: domain primitives — may import only from L0
    "config": 1,
    "pipeline": 1,
    "execution": 1,
    "workspace": 1,
    "planner": 1,
    # Layer 2: domain services — may import from L0 and L1
    "recipe": 2,
    "migration": 2,
    "fleet": 2,
    # Layer 3: application layer — may import from L0–L2
    "server": 3,
    "cli": 3,
}
# Root-level isolated modules are exempt from sub-package layer enforcement.
# Their import constraints are tested by test_isolated_modules_do_not_import_server_or_cli.
_LAYER_EXEMPT_STEMS: frozenset[str] = frozenset(
    {"version", "smoke_utils", "_llm_triage", "__init__", "__main__"}
)

_CORE_SRC = SRC_ROOT / "core"

# ── REQ-ARCH layer enforcement rule descriptors ───────────────────────────────────
LAYER_RULES: dict[str, RuleDescriptor] = {
    "REQ-ARCH-001": RuleDescriptor(
        rule_id="REQ-ARCH-001",
        name="sub-package-import-layer-ordering",
        lens="module-dependency",
        description=(
            "Each autoskillit sub-package may only import from packages at the same or lower "
            "layer (L0 \u2264 L1 \u2264 L2 \u2264 L3). Upward imports introduce coupling that "
            "hinders independent testing and layering guarantees."
        ),
        rationale=(
            "Enforcing a strict layered architecture prevents circular dependencies and "
            "ensures that low-level modules (core, config) remain unaware of high-level "
            "modules (server, cli)."
        ),
        exemptions=frozenset(),
        severity="high",
        defense_standard="DS-001",
    ),
    "REQ-ARCH-003": RuleDescriptor(
        rule_id="REQ-ARCH-003",
        name="l1-packages-no-runtime-l2-l3-imports",
        lens="module-dependency",
        description=(
            "L1 sub-packages (config, pipeline, execution, workspace) must not import from "
            "L2 or L3 packages at runtime. TYPE_CHECKING-guarded imports are permitted."
        ),
        rationale=(
            "Runtime L1\u2192L2 imports would introduce cyclic dependency risk because L2 "
            "packages (recipe, migration) are permitted to import from L1."
        ),
        exemptions=frozenset(),
        severity="high",
        defense_standard="DS-001",
    ),
    "REQ-LAYER-001": RuleDescriptor(
        rule_id="REQ-LAYER-001",
        name="no-raw-ctx-notification-calls-in-tool-handlers",
        lens="module-dependency",
        description=(
            "All ctx.info/error/warning/debug calls in server/tools/tools_*.py must be "
            "replaced by _notify() from server/_notify.py."
        ),
        rationale=(
            "Raw ctx.* notification calls bypass the _notify() validation layer that "
            "enforces reserved-key safety and structured logging invariants in tool "
            "handlers. Any raw call is a developer bypass of the server-layer contract."
        ),
        exemptions=frozenset(),
        severity="error",
        defense_standard="DS-001",
    ),
    "REQ-LAYER-002": RuleDescriptor(
        rule_id="REQ-LAYER-002",
        name="no-reserved-keys-in-notify-extra-dicts",
        lens="module-dependency",
        description=(
            "No literal extra={} dict passed to _notify() in server/tools/tools_*.py may contain "
            "a key that matches a reserved LogRecord attribute."
        ),
        rationale=(
            "Reserved LogRecord keys (e.g. 'message', 'levelname', 'filename') passed "
            "as extra kwargs to structlog/logging calls shadow built-in fields, producing "
            "malformed log records. Static detection at test time prevents silent "
            "corruption of session diagnostics output."
        ),
        exemptions=frozenset(),
        severity="error",
        defense_standard="DS-001",
    ),
}


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


# ── MCP tool registry tests ───────────────────────────────────────────────────


def test_all_mcp_tools_are_registered() -> None:
    """Bidirectional check: every @mcp.tool function is in the _gate registry
    and every registry entry has a corresponding @mcp.tool function in server/.
    """
    from autoskillit.core.types import HEADLESS_TOOLS
    from autoskillit.pipeline.gate import GATED_TOOLS, UNGATED_TOOLS

    expected = GATED_TOOLS | UNGATED_TOOLS | HEADLESS_TOOLS
    server_dir = SRC_ROOT / "server"
    decorated: set[str] = set()
    for py_file in list(server_dir.glob("*.py")) + list((server_dir / "tools").glob("*.py")):
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

    for py_file in list(server_dir.glob("*.py")) + list((server_dir / "tools").glob("*.py")):
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
                    # Tier-aware guard calls may precede _require_enabled —
                    # they are valid early-exit guards, not tool logic.
                    if (
                        action_idx is None
                        and _has_await_or_return(stmt)
                        and not _has_call_to(stmt, "_require_orchestrator_or_higher")
                        and not _has_call_to(stmt, "_require_orchestrator_exact")
                        and not _has_call_to(stmt, "_require_fleet")
                    ):
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
    from autoskillit.server.tools import (
        tools_clone,
        tools_execution,
        tools_git,
        tools_github,
        tools_issue_lifecycle,
        tools_pr_ops,
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
        tools_github,
        tools_issue_lifecycle,
        tools_pr_ops,
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
    for py_file in list(server_dir.glob("*.py")) + list((server_dir / "tools").glob("*.py")):
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
    [pkg for pkg, layer in SUBPACKAGE_LAYERS.items() if layer in {1, 2}],
)
def test_l2_no_deferred_upward_imports(pkg_name: str) -> None:
    """L1/L2 sub-packages must not use deferred imports that violate layer contracts.

    Extends test_import_layer_enforcement to cover function-body (deferred) imports
    via ast.walk, not just tree.body scans. Mirrors the upward-only rule applied at
    module level: L1 packages (config, pipeline, execution, workspace) may not
    deferred-import an L2+ package; L2 packages (recipe, migration) may not
    deferred-import an L3 package (server, cli) — always forbidden.
    """
    pkg_dir = SRC_ROOT / pkg_name
    if not pkg_dir.exists():
        pytest.skip(f"{pkg_name}/ not found — prerequisite group not merged")

    pkg_layer = SUBPACKAGE_LAYERS[pkg_name]
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


def test_workspace_deferred_import_has_comment() -> None:
    """P14-F2: deferred import in cli/app.py must carry a rationale comment."""
    src = (SRC_ROOT / "cli" / "app.py").read_text()
    import_stmt = "from autoskillit.cli._workspace import run_workspace_clean"
    for line in src.splitlines():
        if import_stmt in line:
            after_import = line[line.index(import_stmt) + len(import_stmt) :]
            assert "#" in after_import, (
                "P14-F2: deferred import of run_workspace_clean in cli/app.py "
                "must have an inline comment explaining the deferral rationale"
            )
            return
    pytest.fail("Could not locate the run_workspace_clean deferred import in cli/app.py")


# ── Calibration ────────────────────────────────────────────────────────────────


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


def test_l0_no_dynamic_internal_imports() -> None:
    """L0 code must not dynamically import autoskillit subpackages."""
    violations = []
    for src_file in sorted(_CORE_SRC.rglob("*.py")):
        tree = ast.parse(src_file.read_text(), filename=str(src_file))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            is_import_module = (
                isinstance(func, ast.Attribute) and func.attr == "import_module"
            ) or (isinstance(func, ast.Name) and func.id == "import_module")
            if not is_import_module or not node.args:
                continue
            arg = node.args[0]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                if arg.value.startswith("autoskillit."):
                    violations.append(
                        f"{src_file.name}:{node.lineno}: importlib.import_module({arg.value!r})"
                    )
    assert not violations, (
        "L0 (core/) must not dynamically import autoskillit subpackages:\n" + "\n".join(violations)
    )


# ── L1 Package Runtime Isolation Tests ────────────────────────────────────────


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


# ── recipe/ and migration/ import tests ───────────────────────────────────────


def test_recipe_no_forbidden_imports() -> None:
    """T5: REQ-COMP-009 — recipe/ modules import only from core/, workspace/, recipe/ siblings."""
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


# ── REQ-ARCH-001: No cross-package submodule imports ─────────────────────────


def test_no_cross_package_submodule_imports() -> None:
    """REQ-ARCH-001: No module outside package X may import from autoskillit.X.<submodule>.

    Intra-package imports (e.g., server/__init__.py importing autoskillit.server._notify)
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


# ── REQ-ARCH-003: server/tools/tools_*.py import only allowed packages ─────────


def test_server_tools_import_only_allowed_packages() -> None:
    """REQ-ARCH-003: server/tools/tools_*.py may only import from autoskillit.core,
    autoskillit.pipeline, autoskillit.config, and intra-package autoskillit.server.*.
    TYPE_CHECKING exempt.
    """
    ALLOWED = {"core", "pipeline", "server", "config", "fleet"}
    tools_files = [
        p for p in _SOURCE_FILES if p.parent.name == "tools" and p.stem.startswith("tools_")
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
        "server/tools/tools_*.py files import from disallowed autoskillit sub-packages:\n"
        + "\n".join(violations)
    )


def test_server_non_tools_no_cli_imports() -> None:
    """REQ-ARCH-003b: server/*.py (non-tools_*) files must not import from
    autoskillit.cli — cross-L3 peer dependency. TYPE_CHECKING exempt.
    """
    DENIED = {"cli"}
    non_tools_files = [
        p for p in _SOURCE_FILES if p.parent.name == "server" and not p.stem.startswith("tools_")
    ]
    violations: list[str] = []

    for path in non_tools_files:
        for node in _runtime_import_froms(path):
            if node.module is None:
                continue
            parts = node.module.split(".")
            if parts[0] == "autoskillit" and len(parts) >= 2:
                if parts[1] in DENIED:
                    violations.append(
                        f"{path.name}:{node.lineno} imports from "
                        f"autoskillit.{parts[1]} (cross-L3 peer, not allowed)"
                    )

    assert not violations, (
        "server/*.py (non-tools_*) files import from peer-L3 autoskillit sub-packages:\n"
        + "\n".join(violations)
    )


# ── Notification and convention guards ────────────────────────────────────────


def test_no_raw_ctx_notification_calls_in_tool_handlers() -> None:
    """Architecture guard: all ctx.info/error/warning/debug calls in server/tools/tools_*.py
    must be replaced by _notify. If any raw ctx.* call exists, a developer has
    bypassed the validation layer and this test fails immediately.
    """
    server_dir = SRC_ROOT / "server"
    violations = []
    for path in sorted((server_dir / "tools").glob("tools_*.py")):
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
        "Raw ctx notification calls found — use _notify() from server/_notify.py:\n"
        + "\n".join(violations)
    )


def test_all_tool_extra_keys_are_not_reserved() -> None:
    """Architecture guard: statically verify that no literal extra={} dict passed
    to _notify() in server/tools/tools_*.py contains a key matching a reserved LogRecord
    attribute. Catches reserved-key collisions at test time, before any runtime.
    """
    from autoskillit.core.types import RESERVED_LOG_RECORD_KEYS

    server_dir = SRC_ROOT / "server"
    violations = []
    for path in sorted((server_dir / "tools").glob("tools_*.py")):
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


# ── Path resolution guard ─────────────────────────────────────────────────────


def test_no_file_based_path_resolution_outside_paths_module() -> None:
    """All __file__-based path construction must go through core/paths.py.

    No module other than core/paths.py may use Path(__file__).parent for
    resource or directory resolution. This rule is automatically enforced
    so future developers receive an immediate CI failure with a clear message.
    """
    violations = []
    src_root = Path(__file__).parent.parent.parent / "src" / "autoskillit"
    allowed_files = {
        src_root / "core" / "paths.py",
        # _dispatch.py is stdlib-only (runs as subprocess by Claude Code host)
        # and cannot import core.paths.pkg_root()
        src_root / "hooks" / "_dispatch.py",
    }

    for py_file in src_root.rglob("*.py"):
        if py_file in allowed_files:
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
    from pathlib import Path

    factory_src = (
        Path(__file__).parent.parent.parent / "src/autoskillit/server/_factory.py"
    ).read_text()
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


class TestVersionArchitecture:
    def test_version_module_has_no_upward_imports(self):
        """version.py must not import any autoskillit submodule except __init__."""
        src = (SRC_ROOT / "version.py").read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                parts = node.module.split(".")
                if parts[0] == "autoskillit" and len(parts) > 1:
                    pytest.fail(f"version.py must not import autoskillit.{parts[1]}")

    def test_doctor_imports_version_not_server(self):
        """cli/_doctor.py must not import version_info from autoskillit.server."""
        src = (SRC_ROOT / "cli" / "doctor" / "__init__.py").read_text()
        assert "from autoskillit.server import version_info" not in src
        # Doctor install sub-module uses importlib.metadata for version info
        install_src = (SRC_ROOT / "cli" / "doctor" / "_doctor_install.py").read_text()
        assert "importlib.metadata" in install_src


def test_native_tool_guard_absent_from_hook_registry():
    """native_tool_guard.py must not be registered as a Claude Code hook."""
    from autoskillit.hook_registry import HOOK_REGISTRY

    all_scripts = [s for h in HOOK_REGISTRY for s in h.scripts]
    assert "native_tool_guard.py" not in all_scripts


def test_hook_config_filename_and_dir_match_quota_check():
    """Hook config path constants must agree across all readers and the server writer.

    The server (tools_kitchen.py) writes the config; all hooks read it.
    Two reader contracts must hold:
    - _fmt_primitives._HOOK_CONFIG_PATH_COMPONENTS == server._misc._hook_config_path
    - _hook_settings.py constants == server._misc constants
    """
    import importlib
    from pathlib import Path

    from autoskillit.hooks.formatters._fmt_primitives import _HOOK_CONFIG_PATH_COMPONENTS
    from autoskillit.server._misc import (
        _HOOK_CONFIG_FILENAME,
        _HOOK_DIR_COMPONENTS,
        _hook_config_path,
    )

    root = Path("/tmp/project-root")
    expected = root.joinpath(*_HOOK_CONFIG_PATH_COMPONENTS)
    actual = _hook_config_path(root)

    assert actual == expected, (
        f"server._misc._hook_config_path({root!r})={actual!r} "
        f"does not match path derived from "
        f"_fmt_primitives._HOOK_CONFIG_PATH_COMPONENTS={expected!r}"
    )

    settings_mod = importlib.import_module("autoskillit.hooks._hook_settings")

    assert settings_mod.HOOK_CONFIG_FILENAME == _HOOK_CONFIG_FILENAME, (
        f"_hook_settings.HOOK_CONFIG_FILENAME={settings_mod.HOOK_CONFIG_FILENAME!r} "
        f"does not match server._misc._HOOK_CONFIG_FILENAME={_HOOK_CONFIG_FILENAME!r}"
    )
    assert settings_mod.HOOK_DIR_COMPONENTS == _HOOK_DIR_COMPONENTS, (
        f"_hook_settings.HOOK_DIR_COMPONENTS={settings_mod.HOOK_DIR_COMPONENTS!r} "
        f"does not match server._misc._HOOK_DIR_COMPONENTS={_HOOK_DIR_COMPONENTS!r}"
    )


# ── Tool metadata single source of truth ──────────────────────────────────────


def test_display_categories_sync() -> None:
    """_DISPLAY_CATEGORIES canonical copy covers all registered tools exactly."""
    from autoskillit.config.ingredient_defaults import _DISPLAY_CATEGORIES
    from autoskillit.core import FREE_RANGE_TOOLS, GATED_TOOLS, HEADLESS_TOOLS

    all_registered = GATED_TOOLS | HEADLESS_TOOLS | FREE_RANGE_TOOLS

    flat: list[str] = []
    for _name, tools in _DISPLAY_CATEGORIES:
        flat.extend(tools)
    as_set = set(flat)
    assert len(flat) == len(as_set), "Duplicates in _DISPLAY_CATEGORIES"
    assert as_set == all_registered, (
        f"_DISPLAY_CATEGORIES mismatch:\n"
        f"  Missing: {all_registered - as_set}\n"
        f"  Extra: {as_set - all_registered}"
    )


def test_tool_categories_not_in_core() -> None:
    """TOOL_CATEGORIES must not be exported from the L0 core layer."""
    import autoskillit.core
    import autoskillit.core.types._type_constants

    assert "TOOL_CATEGORIES" not in dir(autoskillit.core)
    assert "TOOL_CATEGORIES" not in dir(autoskillit.core.types._type_constants)
    assert "TOOL_CATEGORIES" not in autoskillit.core.__all__
    assert "TOOL_CATEGORIES" not in autoskillit.core.types._type_constants.__all__


def test_ci_tools_not_in_github_category() -> None:
    """CI tools must be in 'CI & Automation', not in 'GitHub'."""
    from autoskillit.config.ingredient_defaults import _DISPLAY_CATEGORIES

    ci_tools = {"wait_for_ci", "wait_for_merge_queue", "toggle_auto_merge", "get_ci_status"}

    github_tools: set[str] = set()
    ci_cat_tools: set[str] = set()
    for name, tools in _DISPLAY_CATEGORIES:
        if name == "GitHub":
            github_tools = set(tools)
        elif name == "CI & Automation":
            ci_cat_tools = set(tools)
    assert not (ci_tools & github_tools), (
        f"CI tools found in GitHub category: {ci_tools & github_tools}"
    )
    assert ci_tools <= ci_cat_tools, (
        f"CI tools missing from CI & Automation: {ci_tools - ci_cat_tools}"
    )


def test_tool_subset_tags_match_decorators() -> None:
    """TOOL_SUBSET_TAGS matches actual @mcp.tool(tags=...) functional category tags.

    Functional tags = decorator tags - {"autoskillit", "kitchen", "headless"}.
    Tools with non-empty functional tags must be in TOOL_SUBSET_TAGS with the matching
    frozenset. Tools NOT in TOOL_SUBSET_TAGS must have empty functional tags.
    Every tool in TOOL_SUBSET_TAGS must have a matching @mcp.tool decorator.
    """
    from autoskillit.core import TOOL_SUBSET_TAGS

    BASE_TAGS: frozenset[str] = frozenset({"autoskillit", "kitchen", "headless"})
    server_dir = SRC_ROOT / "server"
    decorator_tags: dict[str, frozenset[str]] = {}

    for py_file in (server_dir / "tools").glob("tools_*.py"):
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                if not _is_mcp_tool_decorator(dec):
                    continue
                tags: set[str] = set()
                if isinstance(dec, ast.Call):
                    for kw in dec.keywords:
                        if kw.arg == "tags" and isinstance(kw.value, ast.Set):
                            for elt in kw.value.elts:
                                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                    tags.add(elt.value)
                decorator_tags[node.name] = frozenset(tags - BASE_TAGS)

    mismatches: list[str] = []
    for tool_name, functional in decorator_tags.items():
        expected = TOOL_SUBSET_TAGS.get(tool_name, frozenset())
        if functional != expected:
            mismatches.append(
                f"{tool_name}: decorator functional tags={sorted(functional)}, "
                f"TOOL_SUBSET_TAGS={sorted(expected)}"
            )
    for tool_name in TOOL_SUBSET_TAGS:
        if tool_name not in decorator_tags:
            mismatches.append(f"{tool_name}: in TOOL_SUBSET_TAGS but has no @mcp.tool decorator")

    assert not mismatches, (
        "TOOL_SUBSET_TAGS does not match decorator functional tags:\n"
        + "\n".join(f"  {m}" for m in mismatches)
    )


def test_server_docstring_counts_accurate() -> None:
    """server/__init__.py docstring numeric claims match actual frozenset sizes."""
    from autoskillit.core.types import FREE_RANGE_TOOLS, GATED_TOOLS, HEADLESS_TOOLS

    docstring = (SRC_ROOT / "server" / "__init__.py").read_text()
    expected_substrings: dict[str, str] = {
        "gated": f"{len(GATED_TOOLS)} gated",
        "headless-tagged": f"{len(HEADLESS_TOOLS)} headless-tagged",
        "free-range": f"{len(FREE_RANGE_TOOLS)} free-range",
        "kitchen-tagged": f"{len(GATED_TOOLS) + len(HEADLESS_TOOLS)} kitchen-tagged",
    }

    mismatches = [
        f"'{label}': expected '{claim}' in docstring"
        for label, claim in expected_substrings.items()
        if claim not in docstring
    ]

    assert not mismatches, (
        "server/__init__.py docstring count claims do not match frozenset sizes:\n"
        + "\n".join(f"  {m}" for m in mismatches)
    )


# ---------------------------------------------------------------------------
# REQ-P12-002: Default* classes may only be instantiated in the Composition Root
# (Finding 13.1) — AST gate with explicit allowlist
# ---------------------------------------------------------------------------


def test_default_classes_only_instantiated_inside_factory_or_allowlist() -> None:
    """REQ-P12-002: Default* classes must be instantiated only in
    server/_factory.py (the Composition Root). Five allowlisted exception
    sites are recognized — they must remain in-place; introducing a sixth
    requires either routing through make_context() or an explicit allowlist
    update via this test."""
    import ast

    allowlist: dict[Path, set[str]] = {
        Path("server/_factory.py"): {"*"},  # Composition Root
        Path("cli/_workspace.py"): {"DefaultSubprocessRunner"},  # CLI worktree listing
        Path("cli/session/_cook.py"): {"DefaultSessionSkillManager"},  # interactive cook
        Path("cli/fleet/__init__.py"): {
            "DefaultSessionSkillManager",  # interactive cleanup
        },
        Path("cli/fleet/_fleet_display.py"): {
            "DefaultTokenLog",  # cross-check token diagnostic
        },
        Path("cli/fleet/_fleet_lifecycle.py"): {
            "DefaultWorkspaceManager",  # signal guard cleanup
        },
        Path("cli/app.py"): {"DefaultSkillResolver"},  # skill listing command
        Path("execution/recording.py"): {"DefaultSubprocessRunner"},  # lazy fallback
        Path("pipeline/context.py"): {  # __post_init__ +
            "DefaultBackgroundSupervisor",  # field default_factory
            "DefaultMcpResponseLog",
        },
        Path("recipe/_api.py"): {"DefaultSkillResolver"},  # deferred default factory fallback
        Path("recipe/contracts.py"): {"DefaultSkillResolver"},  # deferred default factory fallback
        Path("recipe/rules/rules_skill_content.py"): {
            "DefaultSkillResolver"
        },  # deferred default factory fallback
        Path("recipe/rules/rules_skills.py"): {
            "DefaultSkillResolver"
        },  # deferred default factory fallback
        Path("recipe/rules/rules_features.py"): {
            "DefaultSkillResolver"
        },  # deferred default factory fallback
        Path("recipe/_skill_helpers.py"): {
            "DefaultSkillResolver"
        },  # shared helper, deferred default factory fallback
        Path("workspace/session_skills.py"): {
            "DefaultSkillResolver"
        },  # ephemeral session resolver fallback
        Path("smoke_utils.py"): {"DefaultTokenLog"},  # run_python callable
    }

    violations: list[str] = []
    for f in SRC_ROOT.rglob("*.py"):
        rel = f.relative_to(SRC_ROOT)
        if "__pycache__" in rel.parts:
            continue
        if rel in allowlist and "*" in allowlist[rel]:
            continue
        tree = ast.parse(f.read_text(), filename=str(f))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (
                func.id
                if isinstance(func, ast.Name)
                else func.attr
                if isinstance(func, ast.Attribute)
                else None
            )
            if not (name and name.startswith("Default") and len(name) > 7 and name[7].isupper()):
                continue
            permitted = allowlist.get(rel, set())
            if name not in permitted:
                violations.append(f"{rel}:{node.lineno} {name}()")
    assert not violations, (
        "Default* classes may only be constructed in server/_factory.py "
        "or in the allowlisted CLI/recording/context fallback sites:\n" + "\n".join(violations)
    )


# ── Test file layer boundary enforcement ─────────────────────────────────────

_TESTS_ROOT = Path(__file__).parent.parent  # = tests/

# Allowed autoskillit top-level packages per test layer directory.
# L3 dirs (server, cli) use wildcard "autoskillit" meaning any sub-package is allowed.
_TEST_LAYER_ALLOWED: dict[str, frozenset[str]] = {
    "tests/core": frozenset({"autoskillit.core"}),
    "tests/config": frozenset({"autoskillit.core", "autoskillit.config"}),
    "tests/pipeline": frozenset({"autoskillit.core", "autoskillit.pipeline"}),
    "tests/execution": frozenset({"autoskillit.core", "autoskillit.execution"}),
    "tests/workspace": frozenset({"autoskillit.core", "autoskillit.workspace"}),
    "tests/planner": frozenset({"autoskillit.core", "autoskillit.planner"}),
    "tests/recipe": frozenset({"autoskillit.core", "autoskillit.recipe"}),
    "tests/migration": frozenset({"autoskillit.core", "autoskillit.migration"}),
    "tests/fleet": frozenset(
        {
            "autoskillit.core",
            "autoskillit.fleet",
            "autoskillit.recipe",
            "autoskillit.server",
            "autoskillit.config",
            "autoskillit.pipeline",
            "autoskillit.hook_registry",
        }
    ),
    "tests/server": frozenset({"autoskillit"}),  # wildcard: any autoskillit.* import allowed
    "tests/cli": frozenset({"autoskillit"}),  # wildcard: any autoskillit.* import allowed
}

# Known intentional cross-refs: file path → extra allowed top-level packages.
# Group D (plan-specified allowlisted benign cross-refs):
#   test_context.py needs config to construct ToolContext (DI container under test).
#   test_skills.py needs load_config() (file-reading fn — not wrappable as a helper).
# Additional benign cross-refs discovered during enforcement rollout:
#   Each entry carries the rationale for why the cross-ref is intentional.
_TEST_LAYER_ALLOWLIST: dict[str, frozenset[str]] = {
    # pipeline tests
    "tests/pipeline/test_context.py": frozenset({"autoskillit.config"}),
    "tests/pipeline/test_gate.py": frozenset({"autoskillit.server"}),
    # core tests — protocol conformance checks require concrete implementations
    "tests/core/test_core_terminal_table.py": frozenset({"autoskillit.cli"}),
    "tests/core/test_types.py": frozenset({"autoskillit.execution"}),
    # execution tests — clone_guard/headless/commands use sibling layers
    "tests/execution/test_clone_guard.py": frozenset({"autoskillit.pipeline"}),
    "tests/execution/test_commands.py": frozenset({"autoskillit.cli"}),
    "tests/execution/test_headless_core.py": frozenset({"autoskillit.pipeline"}),
    "tests/execution/test_headless_result_write_reconciliation.py": frozenset(
        {"autoskillit.pipeline"}
    ),
    "tests/execution/test_session_log_flush.py": frozenset({"autoskillit.pipeline"}),
    "tests/execution/test_headless_path_validation.py": frozenset(
        {"autoskillit.pipeline", "autoskillit.recipe"}
    ),
    # write detection sync guard validates recipe contract patterns against test fixtures
    "tests/execution/test_zero_write_detection.py": frozenset({"autoskillit.recipe"}),
    # quota tests cross into config to validate the contract between vocab constants
    # (execution layer) and config defaults — intentional, documented cross-ref
    "tests/execution/test_quota_binding.py": frozenset({"autoskillit.config"}),
    "tests/execution/test_quota_io.py": frozenset({"autoskillit.config"}),
    "tests/execution/test_quota_sleep.py": frozenset({"autoskillit.hooks", "autoskillit.config"}),
    "tests/execution/test_quota_http.py": frozenset({"autoskillit.config"}),
    # workspace tests
    "tests/workspace/test_clone_ci_contract.py": frozenset({"autoskillit.execution"}),
    "tests/workspace/test_skills.py": frozenset({"autoskillit.config"}),
    # recipe tests — recipe layer is L2 and may use workspace (L1 sibling)
    "tests/recipe/test_contracts.py": frozenset({"autoskillit.workspace"}),
    "tests/recipe/test_rules_skill_content.py": frozenset({"autoskillit.workspace"}),
    # review loop routing integration imports root-level smoke_utils
    "tests/recipe/test_review_loop_routing_integration.py": frozenset({"autoskillit.smoke_utils"}),
    # migration tests — migration engine integrates with execution.session
    "tests/migration/test_engine.py": frozenset({"autoskillit.execution"}),
    # fleet e2e test exercises execution + cli layers end-to-end
    "tests/fleet/test_fleet_e2e.py": frozenset({"autoskillit.execution", "autoskillit.cli"}),
    # session_log retention tests verify callback injection into
    # _enforce_retention — needs fleet.state
    "tests/execution/test_session_log_retention.py": frozenset({"autoskillit.fleet"}),
    # provider forwarding test verifies budget guard preserves provider_used — needs DefaultAuditLog
    "tests/execution/test_headless_provider_forwarding.py": frozenset({"autoskillit.pipeline"}),
}


def _autoskillit_top_package(module: str) -> str | None:
    """Return the top-level autoskillit package for a dotted module string.

    Examples:
      'autoskillit.config.settings' → 'autoskillit.config'
      'autoskillit.config'          → 'autoskillit.config'
      'autoskillit'                 → None  (bare package import, always allowed)
      'structlog'                   → None
    """
    if not module.startswith("autoskillit"):
        return None
    parts = module.split(".")
    if len(parts) >= 2:
        return f"{parts[0]}.{parts[1]}"
    return None  # bare 'import autoskillit' — root package, always allowed


def _collect_layer_test_params() -> list[tuple[str, str, frozenset[str]]]:
    """Return (rel_path, layer_dir, allowed) for every test_*.py in layered dirs."""
    missing = [
        layer_dir
        for layer_dir in _TEST_LAYER_ALLOWED
        if not (_TESTS_ROOT / layer_dir.split("/", 1)[1]).exists()
    ]
    if missing:
        raise AssertionError(
            f"Configured layer directories not found (renamed or deleted?): {missing}"
        )
    params: list[tuple[str, str, frozenset[str]]] = []
    for layer_dir, allowed in _TEST_LAYER_ALLOWED.items():
        subdir = layer_dir.split("/", 1)[1]  # strip leading "tests/"
        layer_path = _TESTS_ROOT / subdir
        for py_file in sorted(layer_path.glob("test_*.py")):
            rel = f"{layer_dir}/{py_file.name}"
            params.append((rel, layer_dir, allowed))
    return params


_LAYER_TEST_PARAMS = _collect_layer_test_params()


@pytest.mark.parametrize(
    "rel_path,layer_dir,allowed",
    _LAYER_TEST_PARAMS,
    ids=[p[0] for p in _LAYER_TEST_PARAMS],
)
def test_test_files_respect_layer_boundaries(
    rel_path: str, layer_dir: str, allowed: frozenset[str]
) -> None:
    """Each test_*.py in a layered directory may only import from its allowed autoskillit packages.

    L3 test dirs (server/, cli/) use a wildcard and may import from any autoskillit sub-package.
    Known intentional cross-refs are listed in _TEST_LAYER_ALLOWLIST with rationale.
    conftest.py files are exempt (only test_*.py is scanned).
    """
    file_path = _TESTS_ROOT / rel_path.split("/", 1)[1]
    source = file_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(file_path))

    wildcard = frozenset({"autoskillit"}) == allowed
    allowlist_extras = _TEST_LAYER_ALLOWLIST.get(rel_path, frozenset())

    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if not node.module:
                continue
            pkg = _autoskillit_top_package(node.module)
            if pkg is None or wildcard or pkg in allowed or pkg in allowlist_extras:
                continue
            violations.append(
                f"  line {node.lineno}: from {node.module} import ... "
                f"({pkg} not allowed in {layer_dir})"
            )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                pkg = _autoskillit_top_package(alias.name)
                if pkg is None or wildcard or pkg in allowed or pkg in allowlist_extras:
                    continue
                violations.append(
                    f"  line {node.lineno}: import {alias.name} ({pkg} not allowed in {layer_dir})"
                )

    assert not violations, f"{rel_path}: cross-layer import violations:\n" + "\n".join(violations)
