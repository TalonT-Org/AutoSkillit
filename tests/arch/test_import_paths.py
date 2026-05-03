"""
Structural import-path compliance tests.

REQ-IMP-001: No cross-package sub-module imports in production code.
REQ-IMP-002: from autoskillit.core.logging/io/types → from autoskillit.core.
REQ-IMP-003: server/tools_*.py imports from at most autoskillit.core and autoskillit.pipeline.
REQ-IMP-004: cli/app.py imports from at most autoskillit.core, .config, .pipeline, and .execution.
REQ-IMP-005: server/git.py only imports autoskillit.core at runtime (TYPE_CHECKING excluded).
REQ-IMP-006: server/tools_kitchen.py has no direct import of DefaultGateState or pipeline.gate.
REQ-IMP-010: cli/_init_helpers.py must not import autoskillit.recipe at module level.
"""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent.parent / "src" / "autoskillit"
PACKAGES = frozenset(
    [
        "core",
        "pipeline",
        "execution",
        "workspace",
        "planner",
        "recipe",
        "migration",
        "fleet",
        "config",
        "server",
        "cli",
    ]
)


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
    """No non-server/cli file may import from an internal sub-module of a different package."""
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

TOOLS_FILES = list((SRC / "server" / "tools").glob("tools_*.py"))


@pytest.mark.parametrize("path", TOOLS_FILES, ids=lambda p: p.name)
def test_req_imp_003_tools_import_namespace(path: Path) -> None:
    """tools_*.py may import from core, pipeline, config, and server."""
    allowed = frozenset(
        {
            "autoskillit.core",
            "autoskillit.pipeline",
            "autoskillit.server",
            "autoskillit.config",
            "autoskillit.fleet",
        }
    )
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
    """server/git.py runtime imports (outside TYPE_CHECKING) must be from autoskillit.core.

    Exception: autoskillit.server._editable_guard is allowed — it is a same-package
    pure-stdlib module that implements the pre-deletion editable install guard, and
    has zero upward imports into config/pipeline/execution layers.
    """
    _ALLOWED = frozenset(
        {
            "autoskillit.server._editable_guard",
            # _subprocess is a same-package helper with no upward layer imports;
            # git.py delegates timeout result processing to _process_runner_result.
            "autoskillit.server._subprocess",
            # workspace is IL-1; git.py delegates worktree removal to the
            # single IL-1 implementation rather than inlining subprocess calls.
            "autoskillit.workspace",
        }
    )
    path = SRC / "server" / "git.py"
    violations: list[str] = []
    for mod, in_tc in _parse_imports(path):
        if in_tc:
            continue
        if mod in _ALLOWED:
            continue
        top2 = ".".join(mod.split(".")[:2])
        if top2 != "autoskillit.core":
            violations.append(mod)
    assert not violations, "REQ-IMP-005 violations:\n" + "\n".join(violations)


# ---------------------------------------------------------------------------
# REQ-IMP-006: server/tools_kitchen.py has no direct GateState import
# ---------------------------------------------------------------------------


def test_req_imp_006_prompts_no_gate_state_import() -> None:
    """server/tools_kitchen.py must not directly import DefaultGateState or pipeline.gate."""
    path = SRC / "server" / "tools" / "tools_kitchen.py"
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


def test_req_imp_007_pretty_output_no_private_recipe_api_import() -> None:
    """hooks/pretty_output_hook.py TYPE_CHECKING must not use recipe._api.

    ListRecipesResult and LoadRecipeResult are re-exported via autoskillit.recipe.__all__.
    Importing from the private recipe._api sub-module bypasses the canonical surface (P14-1).
    """
    path = SRC / "hooks" / "formatters" / "pretty_output_hook.py"
    for mod, in_tc in _parse_imports(path):
        if in_tc and mod == "autoskillit.recipe._api":
            pytest.fail(
                "hooks/pretty_output_hook.py TYPE_CHECKING must use "
                "'from autoskillit.recipe import ...' "
                "instead of 'from autoskillit.recipe._api import ...' (P14-1). "
                "Both ListRecipesResult and LoadRecipeResult are in recipe.__all__."
            )


def test_server_helpers_module_deleted():
    """helpers.py should be fully removed after extraction."""
    import importlib

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("autoskillit.server.helpers")


def test_no_server_helpers_imports_in_src():
    """Verify zero references to the deleted helpers module in production code."""
    import subprocess

    result = subprocess.run(
        ["grep", "-r", "server.helpers", str(SRC.parent)],
        capture_output=True,
        text=True,
    )
    assert result.returncode in (0, 1), (
        f"grep failed with exit code {result.returncode}: {result.stderr}"
    )
    assert result.stdout == "", (
        f"Found references to deleted server.helpers module:\n{result.stdout}"
    )


def test_req_imp_009_session_skills_no_config_settings_import() -> None:
    """workspace/session_skills.py TYPE_CHECKING must use autoskillit.config.

    AutomationConfig is the first entry in autoskillit.config.__all__ and is re-exported
    from config/__init__.py. Importing from config.settings bypasses the canonical public
    surface (P14-4).
    """
    path = SRC / "workspace" / "session_skills.py"
    for mod, in_tc in _parse_imports(path):
        if in_tc and mod == "autoskillit.config.settings":
            pytest.fail(
                "workspace/session_skills.py TYPE_CHECKING must use "
                "'from autoskillit.config import AutomationConfig' "
                "instead of 'from autoskillit.config.settings import AutomationConfig' "
                "(P14-4). AutomationConfig is available via autoskillit.config.__all__."
            )


# ---------------------------------------------------------------------------
# REQ-IMP-007: server/ and cli/ files must not import cross-package sub-modules
# (Finding 13.3) — extends REQ-IMP-001 coverage to IL-3 layers
# ---------------------------------------------------------------------------


def test_req_imp_007_server_cli_no_unauthorized_cross_submodule_imports() -> None:
    """REQ-IMP-007: every file in server/ and cli/ must obey the same
    cross-package submodule rule that REQ-IMP-001 enforces for the IL-0–IL-2
    layers, with a small explicit allowlist:

      server/_factory.py     — Composition Root, may import any layer
      server/git.py          — REQ-IMP-005 exemption
      server/tools_kitchen.py — REQ-IMP-006 ban (covered separately)
      cli/app.py             — REQ-IMP-004 exemption (Typer composition)
      cli/_cook.py           — allowlisted composition boundary
      cli/_workspace.py      — allowlisted composition boundary

    Every other file in server/ and cli/ must import only:
      autoskillit.core, autoskillit.config, autoskillit.pipeline,
      autoskillit.{own_pkg}, and gateway-level (`autoskillit.X`, not
      `autoskillit.X.Y`) imports of other packages.
    """
    allowlist = {
        Path("server/_factory.py"),
        Path("server/git.py"),
        Path("server/tools/tools_kitchen.py"),
        Path("cli/app.py"),
        Path("cli/session/_cook.py"),
        Path("cli/_workspace.py"),
    }
    forbidden_pkgs = {"execution", "workspace", "recipe", "migration"}
    violations: list[str] = []
    for pkg in ("server", "cli"):
        for f in (SRC / pkg).rglob("*.py"):
            rel = f.relative_to(SRC)
            if rel in allowlist or "__pycache__" in rel.parts:
                continue
            for mod, in_tc in _parse_imports(f):
                if in_tc:
                    continue
                if mod.startswith("autoskillit.") and mod.count(".") >= 2:
                    parts = mod.split(".")
                    other_pkg = parts[1]
                    if other_pkg in forbidden_pkgs and other_pkg != pkg:
                        violations.append(f"{rel} → {mod}")
    assert not violations, "REQ-IMP-007 violations:\n" + "\n".join(violations)


def test_req_imp_010_init_helpers_no_toplevel_recipe_imports() -> None:
    """cli/_init_helpers.py must not import autoskillit.recipe at module level.

    list_recipes is an IL-2 dependency; deferring it to the function body
    (_prompt_recipe_choice) keeps _init_helpers.py importable without
    pulling in the recipe subpackage at startup (issue #930).
    """
    path = SRC / "cli" / "_init_helpers.py"
    tree = ast.parse(path.read_text(), filename=str(path))
    violations: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.startswith("autoskillit.recipe"):
                violations.append(f"line {node.lineno}: from {node.module} import ...")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("autoskillit.recipe"):
                    violations.append(f"line {node.lineno}: import {alias.name}")
    assert not violations, (
        "cli/_init_helpers.py has top-level recipe imports "
        "(must be deferred to function body):\n" + "\n".join(violations)
    )


# ---------------------------------------------------------------------------
# IL-008: IL-1 sibling packages must not import from each other at runtime
# ---------------------------------------------------------------------------


def test_il008_il1_independence() -> None:
    """IL-008: IL-1 sibling packages (config, pipeline, execution, workspace) must
    not import from each other at runtime.

    Exception: autoskillit.pipeline.context may import autoskillit.config.
    pipeline.context.ToolContext owns AutomationConfig as the DI wiring point
    (see pipeline/context.py and the IL-003 inline EXCEPTION comment).
    config/ depends only on IL-0 (IL-002), so no cycle is introduced.

    TYPE_CHECKING imports are excluded — pyproject.toml sets
    exclude_type_checking_imports = true and _parse_imports() respects
    the in_tc flag.
    """
    L1_PKGS = frozenset({"config", "pipeline", "execution", "workspace"})
    # (importer_pkg, imported_pkg) tuples that are explicitly allowed
    ALLOWED: frozenset[tuple[str, str]] = frozenset({("pipeline", "config")})

    violations: list[str] = []
    for path in _src_files():
        pkg = _pkg_of(path)
        if pkg not in L1_PKGS:
            continue
        for mod, in_tc in _parse_imports(path):
            if in_tc:
                continue  # TYPE_CHECKING imports are excluded from IL-008 scope
            parts = mod.split(".")
            if len(parts) >= 2 and parts[1] in L1_PKGS and parts[1] != pkg:
                if (pkg, parts[1]) not in ALLOWED:
                    violations.append(f"{path.relative_to(SRC)}: {mod}")
    assert not violations, (
        "IL-008 violations (unauthorized IL-1 sibling runtime imports):\n" + "\n".join(violations)
    )
