"""Keep recipe-parametrized test matrices tied to Git's tracked inventory."""

from __future__ import annotations

import ast
from collections.abc import Mapping
from pathlib import Path
from typing import NamedTuple

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.medium]

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TESTS_ROOT = _REPO_ROOT / "tests"
_GIT_SOURCES = frozenset(
    {
        "tests._git_inventory.git_ls_files",
        "tests._tracked_recipes.tracked_recipe_load_result",
        "tests._tracked_recipes.tracked_recipe_names",
        "tests._tracked_recipes.tracked_recipe_paths",
    }
)
_LIVE_SOURCES = frozenset(
    {
        "autoskillit.recipe.all_validated_recipe_names",
        "autoskillit.recipe.all_validated_recipe_paths",
        "autoskillit.recipe.list_recipes",
        "autoskillit.recipe.io.all_validated_recipe_names",
        "autoskillit.recipe.io.all_validated_recipe_paths",
        "autoskillit.recipe.io.list_recipes",
        "tests._tracked_recipes._on_disk_recipe_paths",
        "tests._tracked_recipes.analyze_untracked_recipes",
    }
)
_RECIPE_ROOT_FACTORIES = frozenset(
    {
        "autoskillit.recipe.builtin_recipes_dir",
        "autoskillit.recipe.io.builtin_recipes_dir",
    }
)
_LIVE_SCAN_METHODS = frozenset({"glob", "rglob", "iterdir", "listdir", "walk"})
_LIVE_ENUMERATION_SOURCES = _LIVE_SOURCES | _LIVE_SCAN_METHODS

_LIVE_ENUMERATION_ALLOWLIST: frozenset[tuple[str, str]] = frozenset(
    {
        # Live runtime discovery is the subject under test here.
        ("tests/recipe/test_io_discovery.py", "list_recipes"),
        # This exact-set assertion intentionally scans bundled recipes on disk.
        ("tests/recipe/test_delivery_segments.py", "glob"),
        # Contract cards are generated artifacts, rather than the recipe test matrix.
        ("tests/recipe/test_bundled_recipes_dispatch_ready.py", "_CONTRACT_STEMS"),
        # This parameter set is derived from the generated contract-card stems above.
        (
            "tests/recipe/test_bundled_recipes_dispatch_ready.py",
            "_RECIPES_WITH_CONTRACTS",
        ),
        # Contract freshness intentionally reads the generated contract-card stems.
        ("tests/arch/test_recipe_contract_freshness.py", "_CONTRACT_STEMS"),
        # This parity assertion compares live validated names with Git-tracked names.
        ("tests/arch/test_recipe_tracking_parity.py", "all_validated_recipe_names"),
        # This parity assertion compares live discovery paths with Git-tracked paths.
        ("tests/arch/test_recipe_tracking_parity.py", "list_recipes"),
    }
)


class _Resolution(NamedTuple):
    sources: frozenset[str] = frozenset()
    recipe_root: bool = False

    @property
    def is_live(self) -> bool:
        return bool(self.sources & _LIVE_ENUMERATION_SOURCES)


class _Module(NamedTuple):
    relative_path: str
    name: str
    tree: ast.Module
    assignments: dict[str, ast.expr]
    functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef]
    imports: dict[str, str]


class _SinkSite(NamedTuple):
    relative_path: str
    symbol: str
    lineno: int
    sources: tuple[str, ...]


def _merge(*resolutions: _Resolution) -> _Resolution:
    sources: set[str] = set()
    for resolution in resolutions:
        sources.update(resolution.sources)
    return _Resolution(frozenset(sources), any(item.recipe_root for item in resolutions))


def _module_name(relative_path: str) -> str:
    name = relative_path.removesuffix(".py").replace("/", ".")
    return name.removesuffix(".__init__")


def _relative_import(module_name: str, level: int, imported: str | None) -> str:
    if level == 0:
        return imported or ""
    package = module_name.split(".")[:-1]
    base = package[: len(package) - level + 1]
    return ".".join((*base, *(imported.split(".") if imported else ())))


class _ModuleIndex:
    """Small AST resolver for collection-time test constants and aliases."""

    def __init__(self, sources: Mapping[str, str]) -> None:
        self.modules: dict[str, _Module] = {}
        self._symbol_cache: dict[tuple[str, str], _Resolution] = {}
        self._resolving: set[tuple[str, str]] = set()
        for relative_path, source in sources.items():
            module_name = _module_name(relative_path)
            tree = ast.parse(source, filename=relative_path)
            assignments: dict[str, ast.expr] = {}
            functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
            imports: dict[str, str] = {}
            for statement in tree.body:
                if isinstance(statement, (ast.Assign, ast.AnnAssign)):
                    value = statement.value
                    targets = (
                        statement.targets
                        if isinstance(statement, ast.Assign)
                        else [statement.target]
                    )
                    if value is not None:
                        for target in targets:
                            if isinstance(target, ast.Name):
                                assignments[target.id] = value
                elif isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    functions[statement.name] = statement
                elif isinstance(statement, ast.Import):
                    for alias in statement.names:
                        imports[alias.asname or alias.name.split(".")[0]] = (
                            alias.name if alias.asname else alias.name.split(".")[0]
                        )
                elif isinstance(statement, ast.ImportFrom):
                    imported_from = _relative_import(
                        module_name, statement.level, statement.module
                    )
                    for alias in statement.names:
                        if alias.name != "*":
                            imports[alias.asname or alias.name] = f"{imported_from}.{alias.name}"
            self.modules[module_name] = _Module(
                relative_path, module_name, tree, assignments, functions, imports
            )

    def reference(self, module: _Module, expression: ast.expr) -> str | None:
        if isinstance(expression, ast.Name):
            return module.imports.get(expression.id)
        if isinstance(expression, ast.Attribute):
            base = self.reference(module, expression.value)
            return f"{base}.{expression.attr}" if base else None
        return None

    def resolve(self, module: _Module, expression: ast.expr) -> _Resolution:
        if isinstance(expression, ast.Name):
            imported = module.imports.get(expression.id)
            if imported:
                return self._resolve_reference(imported)
            return self._resolve_symbol(module.name, expression.id)
        if isinstance(expression, ast.Attribute):
            return self.resolve(module, expression.value)
        if isinstance(expression, ast.Call):
            reference = self.reference(module, expression.func)
            if reference in _GIT_SOURCES | _LIVE_SOURCES:
                return _Resolution(frozenset({reference}))
            if reference in _RECIPE_ROOT_FACTORIES:
                return _Resolution(recipe_root=True)

            receiver = (
                self.resolve(module, expression.func.value)
                if isinstance(expression.func, ast.Attribute)
                else _Resolution()
            )
            arguments = [self.resolve(module, arg) for arg in expression.args]
            arguments.extend(
                self.resolve(module, keyword.value) for keyword in expression.keywords
            )
            if (
                isinstance(expression.func, ast.Attribute)
                and expression.func.attr in _LIVE_SCAN_METHODS
            ):
                if any(item.recipe_root for item in (receiver, *arguments)):
                    return _merge(
                        receiver,
                        *arguments,
                        _Resolution(frozenset({expression.func.attr})),
                    )
            if reference:
                resolved_function = self._resolve_reference(reference)
                if resolved_function.sources or resolved_function.recipe_root:
                    return _merge(receiver, *arguments, resolved_function)
            return _merge(receiver, *arguments)
        if isinstance(expression, (ast.List, ast.Tuple, ast.Set)):
            return _merge(*(self.resolve(module, element) for element in expression.elts))
        if isinstance(expression, ast.Dict):
            values = [*expression.keys, *expression.values]
            return _merge(*(self.resolve(module, value) for value in values if value is not None))
        if isinstance(expression, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
            values: list[ast.expr] = [expression.elt]
            for generator in expression.generators:
                values.append(generator.iter)
                values.extend(generator.ifs)
            return _merge(*(self.resolve(module, value) for value in values))
        if isinstance(expression, ast.DictComp):
            values = [expression.key, expression.value]
            for generator in expression.generators:
                values.append(generator.iter)
                values.extend(generator.ifs)
            return _merge(*(self.resolve(module, value) for value in values))
        if isinstance(expression, ast.BinOp):
            resolved = _merge(
                self.resolve(module, expression.left), self.resolve(module, expression.right)
            )
            literals = {
                node.value
                for node in ast.walk(expression)
                if isinstance(node, ast.Constant) and isinstance(node.value, str)
            }
            return _Resolution(
                resolved.sources, resolved.recipe_root or {".autoskillit", "recipes"} <= literals
            )
        if isinstance(expression, ast.Subscript):
            return _merge(
                self.resolve(module, expression.value), self.resolve(module, expression.slice)
            )
        if isinstance(expression, ast.IfExp):
            return _merge(
                self.resolve(module, expression.test),
                self.resolve(module, expression.body),
                self.resolve(module, expression.orelse),
            )
        return _Resolution()

    def _resolve_reference(self, reference: str) -> _Resolution:
        if reference in _GIT_SOURCES | _LIVE_SOURCES:
            return _Resolution(frozenset({reference}))
        if reference in _RECIPE_ROOT_FACTORIES:
            return _Resolution(recipe_root=True)
        module_name, separator, symbol = reference.rpartition(".")
        if separator and module_name in self.modules:
            return self._resolve_symbol(module_name, symbol)
        return _Resolution()

    def _resolve_symbol(self, module_name: str, symbol: str) -> _Resolution:
        key = (module_name, symbol)
        if key in self._symbol_cache:
            return self._symbol_cache[key]
        if key in self._resolving:
            return _Resolution()
        module = self.modules.get(module_name)
        if module is None:
            return _Resolution()
        self._resolving.add(key)
        try:
            if symbol in module.assignments:
                resolved = self.resolve(module, module.assignments[symbol])
            elif symbol in module.functions:
                returns = [
                    node.value
                    for node in ast.walk(module.functions[symbol])
                    if isinstance(node, ast.Return) and node.value is not None
                ]
                resolved = _merge(*(self.resolve(module, value) for value in returns))
            elif symbol in module.imports:
                resolved = self._resolve_reference(module.imports[symbol])
            else:
                resolved = _Resolution()
        finally:
            self._resolving.remove(key)
        self._symbol_cache[key] = resolved
        return resolved


def _repository_index() -> _ModuleIndex:
    return _ModuleIndex(
        {
            str(path.relative_to(_REPO_ROOT)): path.read_text(encoding="utf-8")
            for path in sorted(_TESTS_ROOT.rglob("*.py"))
        }
    )


def _decorator_values(module: _Module, index: _ModuleIndex) -> list[tuple[ast.expr, int]]:
    values: list[tuple[ast.expr, int]] = []
    for function in ast.walk(module.tree):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in function.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            reference = index.reference(module, decorator.func)
            if reference == "pytest.mark.parametrize":
                argvalues = decorator.args[1] if len(decorator.args) > 1 else None
                argvalues = next(
                    (
                        keyword.value
                        for keyword in decorator.keywords
                        if keyword.arg == "argvalues"
                    ),
                    argvalues,
                )
                if argvalues is not None:
                    values.append((argvalues, decorator.lineno))
            elif reference == "pytest.fixture":
                for keyword in decorator.keywords:
                    if keyword.arg == "params":
                        values.append((keyword.value, decorator.lineno))
    return values


def _expression_symbol(expression: ast.expr, resolution: _Resolution) -> str:
    if isinstance(expression, ast.Name):
        return expression.id
    if isinstance(expression, ast.Attribute):
        return expression.attr
    if isinstance(expression, ast.Call):
        if isinstance(expression.func, ast.Name):
            return expression.func.id
        if isinstance(expression.func, ast.Attribute):
            return expression.func.attr
    return next(iter(sorted(resolution.sources)), "<expression>").rsplit(".", 1)[-1]


def _sink_sites(index: _ModuleIndex) -> set[_SinkSite]:
    sites: set[_SinkSite] = set()
    for module in index.modules.values():
        for expression, lineno in _decorator_values(module, index):
            resolution = index.resolve(module, expression)
            if resolution.is_live:
                sites.add(
                    _SinkSite(
                        module.relative_path,
                        _expression_symbol(expression, resolution),
                        lineno,
                        tuple(sorted(resolution.sources & _LIVE_ENUMERATION_SOURCES)),
                    )
                )
    return sites


def _live_enumeration_sites(index: _ModuleIndex) -> set[tuple[str, str]]:
    sites: set[tuple[str, str]] = set()
    for module in index.modules.values():
        for symbol, expression in module.assignments.items():
            if index.resolve(module, expression).is_live:
                sites.add((module.relative_path, symbol))
        for node in ast.walk(module.tree):
            if not isinstance(node, ast.Call):
                continue
            resolution = index.resolve(module, node)
            if resolution.is_live:
                for source in resolution.sources & _LIVE_SOURCES:
                    sites.add((module.relative_path, source.rsplit(".", 1)[-1]))
                for scan_method in _LIVE_SCAN_METHODS & resolution.sources:
                    sites.add((module.relative_path, scan_method))
    return sites


def _assert_sinks_use_declared_authority(
    sites: set[_SinkSite], allowlist: frozenset[tuple[str, str]] = _LIVE_ENUMERATION_ALLOWLIST
) -> None:
    violations = [
        site for site in sorted(sites) if (site.relative_path, site.symbol) not in allowlist
    ]
    assert not violations, "\n".join(
        f"{site.relative_path}:{site.lineno} uses live {', '.join(site.sources)} via {site.symbol}"
        for site in violations
    )


def _assert_allowlist_is_live(
    sites: set[tuple[str, str]],
    allowlist: frozenset[tuple[str, str]] = _LIVE_ENUMERATION_ALLOWLIST,
) -> None:
    stale = sorted(allowlist - sites)
    assert not stale, f"Stale live-enumeration allowlist entries: {stale}"


def _index_for_test(sources: Mapping[str, str]) -> _ModuleIndex:
    return _ModuleIndex(sources)


def test_recipe_parametrization_uses_declared_git_authority() -> None:
    _assert_sinks_use_declared_authority(_sink_sites(_repository_index()))


def test_live_enumeration_allowlist_entries_remain_live() -> None:
    _assert_allowlist_is_live(_live_enumeration_sites(_repository_index()))


def test_repository_scan_has_a_nonempty_floor() -> None:
    assert len(_repository_index().modules) >= 100


def test_real_authority_references_are_detected_but_comments_and_strings_are_not() -> None:
    index = _index_for_test(
        {
            "tests/synthetic.py": """
import pytest
from tests._tracked_recipes import tracked_recipe_paths
# tracked_recipe_paths(ROOT)
_TEXT = "tracked_recipe_paths(ROOT)"
@pytest.mark.parametrize("recipe", tracked_recipe_paths(ROOT))
def test_real(recipe): pass
"""
        }
    )

    module = index.modules["tests.synthetic"]
    values = _decorator_values(module, index)
    assert len(values) == 1
    assert index.resolve(module, values[0][0]).sources == {
        "tests._tracked_recipes.tracked_recipe_paths"
    }
    assert not _sink_sites(index)
    assert index.resolve(module, module.assignments["_TEXT"]) == _Resolution()


def test_import_aliases_resolve_at_parametrize_and_fixture_sinks() -> None:
    index = _index_for_test(
        {
            "tests/synthetic.py": """
import pytest as pt
import tests._tracked_recipes as inventory
from pytest import fixture as fx
from tests._tracked_recipes import tracked_recipe_names as names
@pt.mark.parametrize("recipe", names(ROOT))
def test_names(recipe): pass
@fx(params=inventory.tracked_recipe_paths(ROOT))
def recipe_path(request): return request.param
"""
        }
    )

    module = index.modules["tests.synthetic"]
    assert {
        index.resolve(module, value).sources for value, _lineno in _decorator_values(module, index)
    } == {
        frozenset({"tests._tracked_recipes.tracked_recipe_names"}),
        frozenset({"tests._tracked_recipes.tracked_recipe_paths"}),
    }
    assert not _sink_sites(index)


def test_imported_constant_resolves_across_test_modules() -> None:
    index = _index_for_test(
        {
            "tests/catalog.py": """
from tests._tracked_recipes import tracked_recipe_paths
RECIPES = tracked_recipe_paths(ROOT)
""",
            "tests/consumer.py": """
import pytest
from tests.catalog import RECIPES as PARAMS
@pytest.mark.parametrize("recipe", PARAMS)
def test_recipe(recipe): pass
""",
        }
    )

    module = index.modules["tests.consumer"]
    value, _lineno = _decorator_values(module, index)[0]
    assert index.resolve(module, value).sources == {"tests._tracked_recipes.tracked_recipe_paths"}
    assert not _sink_sites(index)


def test_repository_gate_rejects_live_recipe_enumeration() -> None:
    index = _index_for_test(
        {
            "tests/synthetic.py": """
import pytest
from autoskillit.recipe import all_validated_recipe_names as live_names
@pytest.mark.parametrize("recipe", live_names(ROOT))
def test_recipe(recipe): pass
"""
        }
    )

    with pytest.raises(AssertionError, match="live_names"):
        _assert_sinks_use_declared_authority(_sink_sites(index), frozenset())


def test_allowlist_liveness_rejects_stale_entry() -> None:
    with pytest.raises(AssertionError, match="stale.py"):
        _assert_allowlist_is_live(set(), frozenset({("tests/stale.py", "list_recipes")}))
