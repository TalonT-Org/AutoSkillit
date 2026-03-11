"""MCP tool registry + import layer contracts + cross-package rules.

Tests:
  - MCP tool registry completeness (bidirectional equality with _gate registry)
  - Import layer contract (each module only imports from same or lower layer)
  - L1 package runtime isolation
  - Cross-package submodule import restrictions
  - server/tools_*.py import constraints
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
                    # _require_not_headless guard calls may precede _require_enabled —
                    # they are valid early-exit guards, not tool logic.
                    if (
                        action_idx is None
                        and _has_await_or_return(stmt)
                        and not _has_call_to(stmt, "_require_not_headless")
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


# ── Notification and convention guards ────────────────────────────────────────


def test_no_raw_ctx_notification_calls_in_tool_handlers() -> None:
    """Architecture guard: all ctx.info/error/warning/debug calls in tools_*.py
    must be replaced by _notify. If any raw ctx.* call exists, a developer has
    bypassed the validation layer and this test fails immediately.
    """
    server_dir = SRC_ROOT / "server"
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
    from autoskillit.core.types import RESERVED_LOG_RECORD_KEYS

    server_dir = SRC_ROOT / "server"
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


# ── Path resolution guard ─────────────────────────────────────────────────────


def test_no_file_based_path_resolution_outside_paths_module() -> None:
    """All __file__-based path construction must go through core/paths.py.

    No module other than core/paths.py may use Path(__file__).parent for
    resource or directory resolution. This rule is automatically enforced
    so future developers receive an immediate CI failure with a clear message.
    """
    violations = []
    src_root = Path(__file__).parent.parent.parent / "src" / "autoskillit"
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
        src = (SRC_ROOT / "cli" / "_doctor.py").read_text()
        assert "from autoskillit.server import version_info" not in src
        # Doctor uses importlib.metadata for version info (not autoskillit.version)
        assert "importlib.metadata" in src


def test_native_tool_guard_absent_from_hook_registry():
    """native_tool_guard.py must not be registered as a Claude Code hook."""
    from autoskillit.hook_registry import HOOK_REGISTRY

    all_scripts = [s for h in HOOK_REGISTRY for s in h.scripts]
    assert "native_tool_guard.py" not in all_scripts


def test_hook_config_filename_and_dir_match_quota_check():
    """quota_check.py must agree with tools_kitchen on the hook config path constants.

    The server (tools_kitchen.py) writes the config; the hook (quota_check.py) reads it.
    They must agree on both the filename and the directory components, or the quota hook
    will silently fail to read its configuration.
    """
    import importlib

    from autoskillit.server.helpers import (
        _HOOK_CONFIG_FILENAME,
        _HOOK_DIR_COMPONENTS,
    )

    quota_mod = importlib.import_module("autoskillit.hooks.quota_check")

    assert quota_mod.HOOK_CONFIG_FILENAME == _HOOK_CONFIG_FILENAME, (
        f"quota_check.HOOK_CONFIG_FILENAME={quota_mod.HOOK_CONFIG_FILENAME!r} "
        f"does not match tools_kitchen._HOOK_CONFIG_FILENAME={_HOOK_CONFIG_FILENAME!r}"
    )
    assert quota_mod.HOOK_DIR_COMPONENTS == _HOOK_DIR_COMPONENTS, (
        f"quota_check.HOOK_DIR_COMPONENTS={quota_mod.HOOK_DIR_COMPONENTS!r} "
        f"does not match tools_kitchen._HOOK_DIR_COMPONENTS={_HOOK_DIR_COMPONENTS!r}"
    )
