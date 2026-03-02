"""
Structural import-path compliance tests.

REQ-IMP-001: No cross-package sub-module imports in production code.
REQ-IMP-002: from autoskillit.core.logging/io/types → from autoskillit.core.
REQ-IMP-003: server/tools_*.py imports from at most autoskillit.core and autoskillit.pipeline.
REQ-IMP-004: cli/app.py imports from at most autoskillit.core, .config, .pipeline, and .execution.
REQ-IMP-005: server/git.py only imports autoskillit.core at runtime (TYPE_CHECKING excluded).
REQ-IMP-006: server/prompts.py has no direct import of DefaultGateState or pipeline.gate.
"""

import ast
from pathlib import Path

import pytest

from tests.arch._rules import RuleDescriptor

SRC = Path(__file__).parent.parent.parent / "src" / "autoskillit"
PACKAGES = frozenset(
    [
        "core",
        "pipeline",
        "execution",
        "workspace",
        "recipe",
        "migration",
        "config",
        "server",
        "cli",
    ]
)


IMP_RULES: tuple[RuleDescriptor, ...] = (
    RuleDescriptor(
        rule_id="REQ-IMP-001",
        name="no-cross-package-submodule-imports",
        lens="module-dependency",
        description=(
            "No module outside package X may import from autoskillit.X.<submodule> "
            "in L0-L2 packages. Excludes server/ and cli/ -- see REQ-ARCH-001."
        ),
        rationale=(
            "Enforces package encapsulation: consumers must use a package's public "
            "__init__ surface, not internal submodule paths. Excludes server/ and cli/ "
            "-- see REQ-ARCH-001 in test_layer_enforcement.py for the full-scope version."
        ),
        exemptions=frozenset(
            {
                "server/",  # covered by REQ-ARCH-001 in test_layer_enforcement.py
                "cli/",  # covered by REQ-ARCH-001 in test_layer_enforcement.py
                "TYPE_CHECKING",  # TYPE_CHECKING blocks are excluded from scan
            }
        ),
        severity="high",
        defense_standard="DS-008",
    ),
    RuleDescriptor(
        rule_id="REQ-IMP-002",
        name="no-core-submodule-imports",
        lens="module-dependency",
        description=(
            "Callers outside core/server/cli must import autoskillit.core, "
            "not autoskillit.core.logging etc."
        ),
        rationale=(
            "Callers outside core/server/cli must import autoskillit.core, "
            "not autoskillit.core.logging etc. This ensures all consumers use the "
            "canonical gateway surface."
        ),
        exemptions=frozenset(),
        severity="high",
        defense_standard="DS-008",
    ),
    RuleDescriptor(
        rule_id="REQ-IMP-003",
        name="tools-import-namespace",
        lens="module-dependency",
        description=(
            "server/tools_*.py may only import autoskillit.core, "
            "autoskillit.pipeline, autoskillit.server."
        ),
        rationale=(
            "Tool handlers must not reach into domain sub-packages directly; "
            "all domain access goes through the ToolContext DI container."
        ),
        exemptions=frozenset(),
        severity="high",
        defense_standard="DS-008",
    ),
    RuleDescriptor(
        rule_id="REQ-IMP-004",
        name="cli-app-namespace-limit",
        lens="module-dependency",
        description=(
            "cli/app.py may only import at gateway level -- no cross-package submodule paths."
        ),
        rationale=(
            "CLI entry point must not bypass package gateway surfaces. "
            "All access must go through package __init__ re-exports."
        ),
        exemptions=frozenset(),
        severity="high",
        defense_standard="DS-008",
    ),
    RuleDescriptor(
        rule_id="REQ-IMP-005",
        name="git-only-core-at-runtime",
        lens="module-dependency",
        description=(
            "server/git.py runtime imports (outside TYPE_CHECKING) "
            "must only be from autoskillit.core."
        ),
        rationale=(
            "server/git.py is the merge workflow service. Keeping runtime imports "
            "limited to autoskillit.core ensures it can be tested in isolation "
            "without importing the full server layer."
        ),
        exemptions=frozenset({"TYPE_CHECKING"}),
        severity="high",
        defense_standard="DS-008",
    ),
    RuleDescriptor(
        rule_id="REQ-IMP-006",
        name="prompts-no-gate-state-import",
        lens="module-dependency",
        description=("server/prompts.py must not import DefaultGateState or the gate submodule."),
        rationale=(
            "Prompt handlers must not depend on the concrete gate implementation; "
            "gate state is managed by the server layer, not prompt handlers."
        ),
        exemptions=frozenset(),
        severity="high",
        defense_standard="DS-008",
    ),
)


def test_imp_rules_complete() -> None:
    """P13-4: IMP_RULES must contain RuleDescriptors for all 6 REQ-IMP rules."""
    assert {r.rule_id for r in IMP_RULES} == {
        "REQ-IMP-001",
        "REQ-IMP-002",
        "REQ-IMP-003",
        "REQ-IMP-004",
        "REQ-IMP-005",
        "REQ-IMP-006",
    }
    for r in IMP_RULES:
        assert r.defense_standard is not None
        assert r.rationale


def _parse_imports(path: Path) -> list[tuple[str, bool]]:
    """Return (module_path, inside_type_checking) for every autoskillit ImportFrom."""
    tree = ast.parse(path.read_text(), filename=str(path))
    results: list[tuple[str, bool]] = []

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self._in_type_checking = False

        def visit_If(self, node: ast.If) -> None:
            test = node.test
            is_tc = (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
                isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
            )
            if is_tc:
                prev = self._in_type_checking
                self._in_type_checking = True
                self.generic_visit(node)
                self._in_type_checking = prev
            else:
                self.generic_visit(node)

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            if node.module and node.module.startswith("autoskillit."):
                results.append((node.module, self._in_type_checking))

    Visitor().visit(tree)
    return results


def _src_files(exclude_dirs: set[str] | None = None) -> list[Path]:
    """All production .py files under src/autoskillit/, excluding __pycache__."""
    return [
        p
        for p in SRC.rglob("*.py")
        if "__pycache__" not in p.parts
        and (exclude_dirs is None or not any(d in p.parts for d in exclude_dirs))
    ]


def _pkg_of(path: Path) -> str | None:
    """Return the immediate sub-package a file belongs to, or None for root files."""
    rel = path.relative_to(SRC)
    return rel.parts[0] if len(rel.parts) > 1 else None


# ---------------------------------------------------------------------------
# REQ-IMP-002: no autoskillit.core.{logging,io,types} imports outside core/
# server/ and cli/ are Part B scope; REQ-IMP-003/004/005/006 cover those.
# ---------------------------------------------------------------------------


def test_req_imp_002_no_core_submodule_imports() -> None:
    """Files outside core/, server/, cli/ must use autoskillit.core, not sub-modules."""
    forbidden_prefixes = (
        "autoskillit.core.logging",
        "autoskillit.core.io",
        "autoskillit.core.types",
    )
    violations: list[str] = []
    for path in _src_files(exclude_dirs={"server", "cli"}):
        if _pkg_of(path) == "core":
            continue  # intra-package: exempt
        for mod, _in_tc in _parse_imports(path):
            if any(mod == p or mod.startswith(p + ".") for p in forbidden_prefixes):
                violations.append(f"{path.relative_to(SRC)}: {mod}")
    assert not violations, "REQ-IMP-002 violations:\n" + "\n".join(violations)


# ---------------------------------------------------------------------------
# REQ-IMP-001: no cross-package sub-module imports in non-server/cli production code
# server/ and cli/ are Part B scope; REQ-IMP-003/004/005/006 cover those.
# ---------------------------------------------------------------------------


def test_req_imp_001_no_cross_package_submodule_imports() -> None:
    """REQ-IMP-001: No cross-package sub-module imports outside server/ and cli/.

    Files in L0-L2 packages must not import autoskillit.X.<submodule> from a different
    package. TYPE_CHECKING-guarded imports are excluded.

    Scope: Excludes server/ and cli/ (see line 111). Those packages are covered by
    REQ-ARCH-001 in test_layer_enforcement.test_no_cross_package_submodule_imports,
    which scans all source packages.
    """
    violations: list[str] = []
    for path in _src_files(exclude_dirs={"server", "cli"}):
        this_pkg = _pkg_of(path)
        for mod, in_tc in _parse_imports(path):
            if in_tc:
                continue  # TYPE_CHECKING imports are exempt (no runtime coupling)
            parts = mod.split(".")
            # autoskillit.<pkg>.<submodule> where pkg is a known package
            if len(parts) >= 3 and parts[1] in PACKAGES:
                target_pkg = parts[1]
                if target_pkg != this_pkg:
                    violations.append(f"{path.relative_to(SRC)}: {mod}")
    assert not violations, "REQ-IMP-001 violations:\n" + "\n".join(violations)


# ---------------------------------------------------------------------------
# REQ-IMP-003: server/tools_*.py imports only core and pipeline (+ intra-server)
# ---------------------------------------------------------------------------

TOOLS_FILES = list((SRC / "server").glob("tools_*.py"))


@pytest.mark.parametrize("path", TOOLS_FILES, ids=lambda p: p.name)
def test_req_imp_003_tools_import_namespace(path: Path) -> None:
    """tools_*.py may only import from autoskillit.core and autoskillit.pipeline."""
    allowed = frozenset({"autoskillit.core", "autoskillit.pipeline", "autoskillit.server"})
    violations: list[str] = []
    for mod, _in_tc in _parse_imports(path):
        top2 = ".".join(mod.split(".")[:2])
        if top2 not in allowed and mod != "autoskillit":
            violations.append(mod)
    assert not violations, f"REQ-IMP-003 violations in {path.name}:\n" + "\n".join(violations)


# ---------------------------------------------------------------------------
# REQ-IMP-004: cli/app.py imports from at most core, config, pipeline
# ---------------------------------------------------------------------------


def test_req_imp_004_cli_app_namespace_limit() -> None:
    """cli/app.py must not import from package sub-modules (autoskillit.X.Y).

    Gateway-level imports (autoskillit.X) are allowed.
    Intra-package imports (autoskillit.cli.*) are allowed.
    """
    path = SRC / "cli" / "app.py"
    # Packages accessible at gateway level (autoskillit.X imports are OK)
    gateway_allowed = frozenset(
        {
            "autoskillit.core",
            "autoskillit.config",
            "autoskillit.pipeline",
            "autoskillit.server",
            "autoskillit.recipe",
            "autoskillit.migration",
            "autoskillit.workspace",
            "autoskillit.execution",  # quota_status CLI command needs check_and_sleep_if_needed
            "autoskillit.cli",  # intra-package
        }
    )
    violations: list[str] = []
    for mod, _in_tc in _parse_imports(path):
        if mod == "autoskillit":
            continue
        parts = mod.split(".")
        top2 = ".".join(parts[:2])
        if top2 not in gateway_allowed:
            violations.append(mod)
        elif len(parts) >= 3 and top2 not in {"autoskillit.core", "autoskillit.cli"}:
            # Sub-module import within a gateway package: forbidden (autoskillit.X.Y)
            violations.append(mod)
    assert not violations, "REQ-IMP-004 violations:\n" + "\n".join(violations)


# ---------------------------------------------------------------------------
# REQ-IMP-005: server/git.py only imports autoskillit.core at runtime
# ---------------------------------------------------------------------------


def test_req_imp_005_git_only_core_at_runtime() -> None:
    """server/git.py runtime imports (outside TYPE_CHECKING) must be from autoskillit.core."""
    path = SRC / "server" / "git.py"
    violations: list[str] = []
    for mod, in_tc in _parse_imports(path):
        if in_tc:
            continue
        top2 = ".".join(mod.split(".")[:2])
        if top2 != "autoskillit.core":
            violations.append(mod)
    assert not violations, "REQ-IMP-005 violations:\n" + "\n".join(violations)


# ---------------------------------------------------------------------------
# REQ-IMP-006: server/prompts.py has no direct GateState import
# ---------------------------------------------------------------------------


def test_req_imp_006_prompts_no_gate_state_import() -> None:
    """server/prompts.py must not directly import DefaultGateState or pipeline.gate sub-module."""
    path = SRC / "server" / "prompts.py"
    tree = ast.parse(path.read_text())
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            # Ban any import of DefaultGateState by name
            names = [alias.name for alias in node.names]
            if "DefaultGateState" in names:
                violations.append(f"import of DefaultGateState from {node.module}")
            # Ban direct sub-module import of pipeline.gate
            if node.module == "autoskillit.pipeline.gate":
                violations.append(f"sub-module import: {node.module}")
    assert not violations, "REQ-IMP-006 violations:\n" + "\n".join(violations)


# ---------------------------------------------------------------------------
# REQ-IMP-007: migration/_api.py has no module-level autoskillit.recipe imports
# ---------------------------------------------------------------------------


def test_req_imp_007_migration_api_no_toplevel_recipe_import() -> None:
    """migration/_api.py must not import from autoskillit.recipe at module level.

    Recipe imports must be deferred to function bodies (P4-1).
    """
    path = SRC / "migration" / "_api.py"
    tree = ast.parse(path.read_text(), filename=str(path))
    # Only top-level statements (direct children of Module) are module-level imports.
    violations = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module and node.module.startswith("autoskillit.recipe"):
                violations.append(node.module)
    assert not violations, (
        "REQ-IMP-007: migration/_api.py has module-level recipe imports "
        "(must be deferred to function bodies):\n" + "\n".join(violations)
    )
