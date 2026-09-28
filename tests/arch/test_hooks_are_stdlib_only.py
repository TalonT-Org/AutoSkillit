"""Architectural guard: REQ-AST-001 runtime imports of Python in installed plugin trees."""

from __future__ import annotations

import ast
from collections.abc import Mapping
from functools import cache
from pathlib import Path
from textwrap import dedent
from types import MappingProxyType

import pytest

from autoskillit.workspace._installed._projection_assets import _PUBLIC_PLUGIN_ASSET_NAMES
from tests._hook_import_closure import (
    PACKAGE_MODE_FACADES,
    ImportClosureReport,
    runtime_imports,
    scan_import_closure,
    scan_shipped_import_closure,
    shipped_python_files,
)
from tests.arch._helpers import SRC_ROOT
from tests.arch._rules import RuleDescriptor

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

HOOKS_STDLIB_RULE = RuleDescriptor(
    rule_id="REQ-AST-001",
    name="hooks-are-stdlib-only",
    lens="security",
    description=(
        "Python shipped into installed plugin trees (hooks/ and the package-root "
        "modules they load) must import only the stdlib, modules under hooks/, or "
        "package-root modules listed in _PUBLIC_PLUGIN_ASSET_NAMES."
    ),
    rationale=(
        "Hook scripts run as subprocesses without the autoskillit venv, from a tree "
        "containing only what _PUBLIC_PLUGIN_ASSET_NAMES admits. An import resolving "
        "anywhere else raises at runtime and silently kills the hook (#4526, #5221). "
        "Only the body of `if TYPE_CHECKING:` is exempt; its else-branch runs."
    ),
    exemptions=frozenset({"TYPE_CHECKING"}),
    severity="error",
    defense_standard="DS-001",
)

# Non-literal imports require a reviewed script-mode target that ships.
_DYNAMIC_IMPORT_SITES: Mapping[tuple[str, str], str] = MappingProxyType(
    {
        ("hooks/_capture/_authority.py", "<module>"): (
            "Dual identity: script mode imports hooks-local `_capture_lifecycle`."
        ),
        ("hooks/_child_outcome_snapshot/_snapshot.py", "_resolve_sibling"): (
            "Script mode imports `name` bare; callers pass hooks-local "
            "`_hook_settings`/`_session_binding`."
        ),
        ("hooks/_runtime/__init__.py", "__getattr__"): (
            "Lazy re-export of `_runtime` submodules via f'{__name__}.{submod}'."
        ),
        ("hooks/_runtime/_hook_settings.py", "_registry_bridge_call"): (
            "Script mode imports hooks-local `_session_registry_bridge`."
        ),
        ("hooks/_runtime/_hook_settings.py", "session_join_admission"): (
            "Script mode imports hooks-local `_session_binding`."
        ),
        ("hooks/_runtime/_session_scope_authority.py", "enforce_script_session_scope"): (
            "Script mode imports hooks-local `_hook_scope_table`."
        ),
        ("hooks/formatters/_fmt_primitives.py", "__getattr__"): (
            "Script mode imports hooks-local `_fmt_response_spill`."
        ),
    }
)

# _recipe_delivery_framing.py is reached through _fmt_recipe.py's TYPE_CHECKING else.
_ROOT_DEPENDENCY_ANCHORS = frozenset(
    {"quota_constraints.py", "_parent_assistant_turns.py", "_recipe_delivery_framing.py"}
)


@cache
def _report() -> ImportClosureReport:
    return scan_shipped_import_closure(SRC_ROOT)


def test_hooks_are_stdlib_only() -> None:
    violations = _report().violations
    assert not violations, HOOKS_STDLIB_RULE.description + "\n" + "\n".join(violations)


def test_hook_root_dependencies_are_published() -> None:
    deps = _report().root_dependencies
    assert _ROOT_DEPENDENCY_ANCHORS <= deps.keys(), "The scanner lost a known importer: " + repr(
        sorted(_ROOT_DEPENDENCY_ANCHORS - deps.keys())
    )
    unpublished = {
        name: sorted(importers)
        for name, importers in deps.items()
        if name not in _PUBLIC_PLUGIN_ASSET_NAMES
    }
    assert not unpublished, (
        "Hook root dependencies are unpublished: "
        f"{unpublished}; add each to `_PUBLIC_PLUGIN_ASSET_NAMES` in "
        "`src/autoskillit/workspace/_installed/_projection_assets.py`, or it is absent from "
        "every installed plugin tree (Claude projections, Codex generations, marketplace installs)"
    )


def test_package_mode_facades_are_unreachable_from_shipped_code() -> None:
    shipped = {p.relative_to(SRC_ROOT).as_posix() for p in shipped_python_files(SRC_ROOT)}
    for rel, rationale in PACKAGE_MODE_FACADES.items():
        assert rel in shipped, f"Stale package-mode facade exemption: {rel}"
        assert rationale.strip(), f"Package-mode facade needs a rationale: {rel}"
        assert rel.split("/")[0] not in _report().root_dependencies, (
            f"A shipped import reaches exempt package-mode facade {rel}"
        )


def test_dynamic_import_sites_are_inventoried() -> None:
    actual = _report().dynamic_sites
    inventoried = set(_DYNAMIC_IMPORT_SITES)
    assert actual == inventoried, (
        "Dynamic import inventory mismatch:\n"
        f"new site: add it with a rationale after proving its script-mode target ships: "
        f"{sorted(actual - inventoried)}\n"
        f"stale entry: remove it: {sorted(inventoried - actual)}"
    )
    for site, rationale in _DYNAMIC_IMPORT_SITES.items():
        assert rationale.strip(), f"Dynamic import site needs a rationale: {site}"


@pytest.mark.parametrize(
    ("guard", "expected"),
    [
        ("TYPE_CHECKING", {"b"}),
        ("typing.TYPE_CHECKING", {"b"}),
        ("TYPE_CHECKING or __package__", {"a", "b"}),
    ],
)
def test_scanner_keeps_type_checking_else_branch(guard: str, expected: set[str]) -> None:
    imports = runtime_imports(ast.parse(f"if {guard}:\n    import a\nelse:\n    import b"))
    assert {ref.module for ref in imports.refs} == expected


def test_scanner_finds_function_and_class_local_imports() -> None:
    imports = runtime_imports(
        ast.parse(
            dedent("""\
                def f():
                    import a
                class C:
                    from b import value
                    async def m(self):
                        import c
                """)
        )
    )
    assert {ref.module for ref in imports.refs} == {"a", "b", "c"}


def test_scanner_walks_both_arms_of_import_fallbacks() -> None:
    imports = runtime_imports(ast.parse("try:\n    import a\nexcept ImportError:\n    import b"))
    assert {ref.module for ref in imports.refs} == {"a", "b"}


def test_scanner_finds_literal_dynamic_imports() -> None:
    imports = runtime_imports(
        ast.parse('importlib.import_module("x")\nimport_module("y")\n__import__("z")')
    )
    assert {ref.module for ref in imports.refs} == {"x", "y", "z"}
    assert not imports.dynamic_sites


def test_scanner_records_non_literal_dynamic_import_sites() -> None:
    imports = runtime_imports(
        ast.parse(
            dedent("""\
                class C:
                    def m(self, n):
                        return importlib.import_module(n)
                def r():
                    return importlib.import_module(".x", package="p")
                """)
        )
    )
    assert imports.dynamic_sites == {"C.m", "r"}
    assert not imports.refs


@pytest.mark.parametrize(
    "call",
    [
        "import_module()",
        'import_module(name="x")',
        '__import__("x", globals())',
        'import_module(".x")',
        "import_module(1)",
    ],
)
def test_scanner_requires_one_absolute_literal_loader_argument(call: str) -> None:
    imports = runtime_imports(ast.parse(call))
    assert imports.dynamic_sites == {"<module>"}
    assert not imports.refs


def test_scanner_records_opaque_loaders_as_dynamic_sites() -> None:
    imports = runtime_imports(
        ast.parse(
            dedent("""\
                def f(p):
                    importlib.util.spec_from_file_location("x", p)
                def g(p):
                    runpy.run_path(p)
                def h(s):
                    exec(s)
                runpy.run_module("y")
                """)
        )
    )
    assert imports.dynamic_sites == {"f", "g", "h"}
    assert {ref.module for ref in imports.refs} == {"y"}


@pytest.mark.parametrize(
    ("source", "scope"),
    [
        (
            "@import_module(decorator)\ndef f(arg=import_module(default)):\n    pass",
            "<module>",
        ),
        (
            "class C(import_module(base), metaclass=import_module(meta)):\n    pass",
            "<module>",
        ),
        (
            "class C:\n    @import_module(decorator)\n"
            "    async def m(self, arg=import_module(default)):\n        pass",
            "C",
        ),
    ],
)
def test_scanner_attributes_definition_expressions_to_enclosing_scope(
    source: str, scope: str
) -> None:
    imports = runtime_imports(ast.parse(source))
    assert imports.dynamic_sites == {scope}


def test_scanner_classifies_synthetic_tree(tmp_path: Path) -> None:
    root = tmp_path / "pkg"
    runtime_dir = root / "hooks" / "_runtime"
    runtime_dir.mkdir(parents=True)
    (root / "rootmod.py").write_text("", encoding="utf-8")
    # A root entry shadows a same-named hooks-local module.
    (runtime_dir / "rootmod.py").write_text("", encoding="utf-8")
    sibling = runtime_dir / "sibling.py"
    sibling.write_text("", encoding="utf-8")
    hook = root / "hooks" / "h.py"
    hook.write_text(
        dedent("""\
            import rootmod
            import json
            import sibling
            import missing_mod
            from .. import x
            if TYPE_CHECKING:
                import autoskillit.core
            """),
        encoding="utf-8",
    )

    report = scan_import_closure(root, [hook, sibling])

    assert report.root_dependencies == {"rootmod.py": {"hooks/h.py"}}
    assert len(report.violations) == 2
    assert any("'missing_mod'" in violation for violation in report.violations)
    assert any("escapes hooks/" in violation for violation in report.violations)
