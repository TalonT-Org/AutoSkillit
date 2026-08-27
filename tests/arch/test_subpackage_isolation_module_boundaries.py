from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.arch._helpers import (
    SRC_ROOT,
    _extract_module_level_internal_imports,
    _is_mcp_tool_decorator,
)

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


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
    """Severity must be a top-level class in core/types/_type_enums.py (the enums sub-module)."""
    tree = _get_module_ast("core/types/_type_enums.py")
    assert "Severity" in _top_level_class_names(tree), (
        "Severity not found in core/types/_type_enums.py; it must be defined there"
    )


def test_skill_tools_defined_in_types():
    """SKILL_TOOLS must be a top-level assignment in _type_constants_registries.py."""
    tree = _get_module_ast("core/types/_type_constants_registries.py")
    assert "SKILL_TOOLS" in _top_level_assign_targets(tree), (
        "SKILL_TOOLS not found in core/types/_type_constants_registries.py;"
        " it must be defined there"
    )


def test_no_yaml_safe_load_in_migration_engine() -> None:
    """P7-2: ContractMigrationAdapter.validate must use _load_yaml, not yaml.safe_load."""
    src = (
        Path(__file__).parent.parent.parent / "src/autoskillit/migration/adapters_contract.py"
    ).read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "safe_load":
                pytest.fail(
                    f"migration/adapters_contract.py line {node.lineno}: "
                    f"direct yaml.safe_load call found; use load_yaml from core.io instead"
                )


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
    ast_module = _get_module_ast("cli/doctor/__init__.py")
    class_names = _top_level_class_names(ast_module)
    assert "Severity" not in class_names, (
        "cli/doctor/__init__.py must import Severity from autoskillit.core, not define it locally"
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
    ast_module = _get_module_ast("recipe/_contracts_types.py")
    assigns = _top_level_assign_targets(ast_module)
    assert "_CONTEXT_REF_RE" in assigns, "recipe/_contracts_types.py must define _CONTEXT_REF_RE"
    assert "INPUT_REF_RE" in assigns, "recipe/_contracts_types.py must define INPUT_REF_RE"


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


def test_core_has_no_autoskillit_imports() -> None:
    """REQ-CNST-004: core/ modules must not import from any autoskillit sub-package.

    TYPE_CHECKING-guarded imports are permitted — they are zero-runtime-cost annotations
    that do not create actual import dependencies (same exemption as test_layer_enforcement.py).
    """
    core_dir = SRC_ROOT / "core"
    assert core_dir.exists(), "core/ package must exist"
    violations: list[str] = []
    for py_file in core_dir.glob("*.py"):
        tree = ast.parse(py_file.read_text())
        tc_lines: set[int] = set()
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.If)
                and isinstance(node.test, ast.Name)
                and node.test.id == "TYPE_CHECKING"
            ):
                for stmt in node.body:
                    for child in ast.walk(stmt):
                        if isinstance(child, ast.Import | ast.ImportFrom):
                            tc_lines.add(child.lineno)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.lineno in tc_lines:
                    continue
                parts = node.module.split(".")
                if parts[0] == "autoskillit" and len(parts) > 1:
                    violations.append(f"core/{py_file.name}:{node.lineno}: imports {node.module}")
            elif isinstance(node, ast.Import):
                if node.lineno in tc_lines:
                    continue
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
    isolated_files = ["_llm_triage.py", "version.py"]
    isolated_packages = ["smoke_utils"]
    forbidden_prefixes = ("autoskillit.server", "autoskillit.cli")
    violations: list[str] = []

    def _check_file(py_file: Path, label: str) -> None:
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                mod = node.module
                if any(mod == f or mod.startswith(f + ".") for f in forbidden_prefixes):
                    violations.append(f"{label}:{node.lineno}: imports {mod}")

    for filename in isolated_files:
        py_file = SRC_ROOT / filename
        if not py_file.exists():
            continue
        _check_file(py_file, filename)

    for pkg_name in isolated_packages:
        pkg_dir = SRC_ROOT / pkg_name
        if not pkg_dir.is_dir():
            continue
        for py_file in sorted(pkg_dir.rglob("*.py")):
            label = f"{pkg_name}/{py_file.relative_to(pkg_dir)}"
            _check_file(py_file, label)

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
    tool_sources: list[Path] = []
    for py_file in (server_dir / "tools").glob("tools_*.py"):
        tool_sources.append(py_file)
    for pkg_dir in (server_dir / "tools").iterdir():
        if not pkg_dir.is_dir():
            continue
        if not pkg_dir.name.startswith("tools_"):
            continue
        for submodule in pkg_dir.glob("*.py"):
            if submodule.name == "__init__.py":
                continue
            tool_sources.append(submodule)
    tool_sources.sort()

    for py_file in tool_sources:
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


def test_semantic_rule_functions_defined_in_rule_submodules() -> None:
    """P8: Semantic rule functions must be defined in rules_*.py submodules."""
    from autoskillit.recipe.validator import _check_outdated_version

    assert _check_outdated_version.__module__ == "autoskillit.recipe.rules.rules_inputs"


def test_installed_version_in_core_types() -> None:
    """P3-F2: AUTOSKILLIT_INSTALLED_VERSION must be in autoskillit.core."""
    from autoskillit.core import AUTOSKILLIT_INSTALLED_VERSION

    assert isinstance(AUTOSKILLIT_INSTALLED_VERSION, str) and AUTOSKILLIT_INSTALLED_VERSION


def test_rule_submodules_no_autoskillit_init_import() -> None:
    """P3-F2: rules_*.py submodules must not import from autoskillit top-level __init__."""
    rule_files = sorted((SRC_ROOT / "recipe" / "rules").rglob("rules_*.py"))
    assert len(rule_files) >= 5, f"Expected >=5 rules_*.py files, found {len(rule_files)}"
    for rules_path in rule_files:
        assert "from autoskillit import __version__" not in rules_path.read_text(), (
            f"{rules_path.name} must not import from autoskillit top-level __init__"
        )


def test_recipe_api_module_exists() -> None:
    """P14-F1/F2: recipe/_api.py must exist and be importable."""
    import autoskillit.recipe._api  # noqa: F401


def test_default_recipe_repository_in_repository_module() -> None:
    """P2-F1: DefaultRecipeRepository must live in recipe/repository.py."""
    from autoskillit.recipe.repository import DefaultRecipeRepository  # noqa: F401


def test_recipe_lister_callsites_use_protocol_typing() -> None:
    """REQ-ARCH-006: callsites in recipe/ that consume the skill listing
    must reference the SkillLister Protocol (parameter type), so the
    deferred DefaultSkillResolver() instantiation is a default-factory fallback
    rather than the only path.

    contracts.py uses .resolve() and therefore references SkillResolver,
    not SkillLister. That is checked separately below.
    """
    lister_targets = {
        "src/autoskillit/recipe/_skill_helpers.py",
        "src/autoskillit/recipe/_api_orchestration.py",
    }
    src_root = Path(__file__).resolve().parents[2]
    missing: list[str] = []
    for relpath in lister_targets:
        text = (src_root / relpath).read_text()
        if "SkillLister" not in text:
            missing.append(relpath)
    assert not missing, (
        f"These files still consume SkillResolver without SkillLister Protocol typing: {missing}"
    )
    # contracts.py uses .resolve() — must reference SkillResolver, not SkillLister
    contracts_text = (src_root / "src/autoskillit/recipe/_contracts_staleness.py").read_text()
    assert "SkillResolver" in contracts_text, (
        "_contracts_staleness.py must reference SkillResolver for the resolver parameter"
    )


def test_default_recipe_repository_not_in_io() -> None:
    """P2-F1: DefaultRecipeRepository must be removed from recipe/io.py."""
    io_path = SRC_ROOT / "recipe" / "io.py"
    assert "class DefaultRecipeRepository" not in io_path.read_text()


def test_only_yaml_imports_yaml_directly() -> None:
    """Only core/io.py may contain 'import yaml' at any scope."""
    src_dir = SRC_ROOT
    allowed_rel = str(Path("core") / "io.py")
    violations = []
    for py_file in sorted(src_dir.rglob("*.py")):
        rel = str(py_file.relative_to(src_dir))
        if rel == allowed_rel:
            continue
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "yaml" or alias.name.startswith("yaml."):
                        violations.append(f"{rel}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                if (node.module or "").startswith("yaml"):
                    violations.append(f"{rel}: from {node.module} import ...")
    assert not violations, f"Direct yaml imports found outside core/io.py: {violations}"
