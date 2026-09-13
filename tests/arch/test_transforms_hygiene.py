"""Structural guards for FastMCP visibility tag hygiene.

Ensures all test fixtures that touch mcp._transforms / mcp.enable / mcp.disable
use the canonical ALL_VISIBILITY_TAGS constant, and that production code only
uses tag strings present in ALL_VISIBILITY_TAGS ∪ CATEGORY_TAGS.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_TESTS_ROOT = Path(__file__).parent.parent
_SRC_ROOT = _TESTS_ROOT.parent / "src" / "autoskillit"


def _iter_tool_modules(tools_dir: Path) -> list[Path]:
    """Yield every tool source file: flat ``tools_*.py`` files plus the ``.py``
    submodules of any sibling tool package that was decomposed in issue #4663.
    """
    paths: list[Path] = []
    for entry in sorted(tools_dir.iterdir()):
        if entry.is_file() and entry.name.startswith("tools_") and entry.suffix == ".py":
            paths.append(entry)
        elif entry.is_dir() and entry.name.startswith("tools_") and entry.name != "tools_kitchen":
            # tools_kitchen.py is a flat file consumed by other tests; the
            # decomposed siblings are checked separately via their package.
            for submodule in sorted(entry.glob("*.py")):
                if submodule.name == "__init__.py":
                    continue
                paths.append(submodule)
    return paths


def _iter_tool_decorators(
    tools_dir: Path,
) -> Iterator[tuple[Path, ast.FunctionDef | ast.AsyncFunctionDef, ast.Call]]:
    for path in sorted(_iter_tool_modules(tools_dir)):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if (
                    isinstance(decorator, ast.Call)
                    and isinstance(decorator.func, ast.Attribute)
                    and decorator.func.attr == "tool"
                ):
                    yield path, node, decorator


def _iter_literal_string_tag_values(tag_set: ast.Set) -> Iterator[str]:
    for element in tag_set.elts:
        if isinstance(element, ast.Constant) and isinstance(element.value, str):
            yield element.value


def _is_pytest_fixture_decorator(decorator: ast.expr) -> bool:
    return (
        isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Attribute)
        and decorator.func.attr == "fixture"
    ) or (isinstance(decorator, ast.Attribute) and decorator.attr == "fixture")


def test_all_visibility_tags_constant_exists():
    """ALL_VISIBILITY_TAGS must be defined in _type_constants.py and exported."""
    from autoskillit.core import ALL_VISIBILITY_TAGS

    assert isinstance(ALL_VISIBILITY_TAGS, frozenset)
    assert len(ALL_VISIBILITY_TAGS) >= 5


def test_all_visibility_tags_covers_tool_subset_tags():
    """Every non-category tag in TOOL_SUBSET_TAGS must appear in ALL_VISIBILITY_TAGS."""
    from autoskillit.core import ALL_VISIBILITY_TAGS, CATEGORY_TAGS, TOOL_SUBSET_TAGS

    all_tags = {tag for tags in TOOL_SUBSET_TAGS.values() for tag in tags}
    non_category_tags = all_tags - CATEGORY_TAGS
    assert non_category_tags <= ALL_VISIBILITY_TAGS, (
        f"Non-category tags missing from ALL_VISIBILITY_TAGS: "
        f"{sorted(non_category_tags - ALL_VISIBILITY_TAGS)}"
    )


class _ConfTestVisitor(ast.NodeVisitor):
    """AST visitor that finds fixtures touching mcp._transforms/mcp.enable/mcp.disable."""

    def __init__(self) -> None:
        self.findings: list[tuple[str, int, str]] = []

    def _check_fixture_body(
        self,
        stmts: list[ast.stmt],
        fixture_name: str,
        *,
        is_fixture: bool,
    ) -> None:
        has_clear = False
        has_disable_with_tags_keyword = False

        for node in ast.walk(ast.Module(body=stmts, type_ignores=[])):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if (
                    node.func.attr == "clear"
                    and isinstance(node.func.value, ast.Attribute)
                    and node.func.value.attr == "_transforms"
                ):
                    has_clear = True

                if node.func.attr == "disable" and any(kw.arg == "tags" for kw in node.keywords):
                    has_disable_with_tags_keyword = True
                    for kw in node.keywords:
                        if kw.arg != "tags":
                            continue
                        if isinstance(kw.value, ast.Set):
                            tag_vals = {
                                elt.value for elt in kw.value.elts if isinstance(elt, ast.Constant)
                            }
                            if tag_vals:
                                self.findings.append(
                                    (
                                        fixture_name,
                                        node.lineno,
                                        f"hardcoded-tags:{sorted(tag_vals)}",
                                    )
                                )

        if is_fixture and has_disable_with_tags_keyword and not has_clear:
            self.findings.append((fixture_name, 0, "disable-without-clear"))

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        is_fixture = any(
            _is_pytest_fixture_decorator(decorator) for decorator in node.decorator_list
        )
        if not is_fixture:
            self.generic_visit(node)
            return

        touches_mcp = False
        for child in ast.walk(node):
            if isinstance(child, ast.Attribute) and child.attr in (
                "_transforms",
                "enable",
                "disable",
            ):
                touches_mcp = True
                break
        if touches_mcp:
            self._check_fixture_body(node.body, node.name, is_fixture=True)
        self.generic_visit(node)


def test_every_conftest_reset_uses_canonical_tag_set():
    """Conftest fixtures that disable tags must use ALL_VISIBILITY_TAGS, not hardcoded sets."""

    conftest_files = list(_TESTS_ROOT.rglob("conftest.py"))
    assert conftest_files, "No conftest.py files found"

    violations = []
    for path in conftest_files:
        source = path.read_text()
        if "mcp" not in source:
            continue
        tree = ast.parse(source, filename=str(path))
        visitor = _ConfTestVisitor()
        visitor.visit(tree)
        for name, lineno, detail in visitor.findings:
            rel = path.relative_to(_TESTS_ROOT)
            if detail.startswith("hardcoded-tags:"):
                violations.append(f"{rel}:{lineno} fixture={name} {detail}")
            elif detail == "disable-without-clear":
                violations.append(f"{rel}:{lineno} fixture={name} {detail}")

    assert not violations, "Conftest fixtures with tag hygiene violations:\n" + "\n".join(
        f"  {v}" for v in violations
    )


def test_root_conftest_has_transforms_cleanup():
    """Root conftest must have autouse fixture with sys.modules guard + _transforms cleanup."""
    source = (_TESTS_ROOT / "conftest.py").read_text()
    tree = ast.parse(source)

    found = False
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name != "_reset_mcp_visibility":
            continue
        is_autouse = False
        for d in node.decorator_list:
            if isinstance(d, ast.Call):
                for kw in d.keywords:
                    if (
                        kw.arg == "autouse"
                        and isinstance(kw.value, ast.Constant)
                        and kw.value.value is True
                    ):
                        is_autouse = True
        if not is_autouse:
            continue

        func_source = ast.get_source_segment(source, node) or ""
        has_sys_modules = "sys.modules" in func_source
        node_dump = ast.dump(node)
        has_clear = "_transforms" in node_dump and "clear" in node_dump

        if has_sys_modules and has_clear:
            found = True
            break

    assert found, (
        "tests/conftest.py must have an autouse fixture named _reset_mcp_visibility "
        "with sys.modules guard and mcp._transforms.clear() call"
    )


def _class_fixture_literal_disable_violations(
    fixture: ast.FunctionDef | ast.AsyncFunctionDef,
    source_path: Path,
    class_name: str,
) -> list[str]:
    violations: list[str] = []
    for child in ast.walk(fixture):
        if not (
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Attribute)
            and child.func.attr == "disable"
        ):
            continue
        for keyword in child.keywords:
            if keyword.arg == "tags" and isinstance(keyword.value, ast.Set):
                tag_vals = {
                    element.value
                    for element in keyword.value.elts
                    if isinstance(element, ast.Constant)
                }
                if tag_vals:
                    relative_path = source_path.relative_to(_TESTS_ROOT)
                    violations.append(
                        f"{relative_path}:{child.lineno} "
                        f"class={class_name} "
                        f"hardcoded={sorted(tag_vals)}"
                    )
    return violations


def _inline_transform_clear_lines(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[int]:
    return [
        child.lineno
        for child in ast.walk(function)
        if (
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Attribute)
            and child.func.attr == "clear"
            and isinstance(child.func.value, ast.Attribute)
            and child.func.value.attr == "_transforms"
        )
    ]


def test_class_level_fixtures_use_canonical_tags():
    """Class-level _reset_mcp_visibility fixtures must use ALL_VISIBILITY_TAGS."""
    violations = []

    for path in sorted(_TESTS_ROOT.rglob("test_*.py")):
        source = path.read_text()
        if "_reset_mcp_visibility" not in source:
            continue
        tree = ast.parse(source, filename=str(path))

        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for item in node.body:
                if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if item.name != "_reset_mcp_visibility":
                    continue
                violations.extend(_class_fixture_literal_disable_violations(item, path, node.name))

    assert not violations, (
        "Class-level _reset_mcp_visibility fixtures with hardcoded tag sets "
        "(must use ALL_VISIBILITY_TAGS):\n" + "\n".join(f"  {v}" for v in violations)
    )


def test_inline_transforms_clear_has_finally_guard():
    """Inline mcp._transforms.clear() calls in test functions must be inside try/finally."""
    violations = []

    for path in sorted(_TESTS_ROOT.rglob("test_*.py")):
        source = path.read_text()
        if "_transforms" not in source:
            continue
        tree = ast.parse(source, filename=str(path))

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.startswith("test_"):
                continue

            is_fixture = any(
                _is_pytest_fixture_decorator(decorator) for decorator in node.decorator_list
            )
            if is_fixture:
                continue

            clear_calls = _inline_transform_clear_lines(node)

            if len(clear_calls) < 1:
                continue

            has_try_finally = any(
                isinstance(stmt, ast.Try) and stmt.finalbody for stmt in ast.walk(node)
            )
            if not has_try_finally:
                rel = path.relative_to(_TESTS_ROOT)
                violations.append(f"{rel}:{clear_calls[0]} func={node.name}")

    assert not violations, (
        "Test functions with inline _transforms.clear() missing try/finally guard:\n"
        + "\n".join(f"  {v}" for v in violations)
    )


def test_session_type_visibility_uses_known_tags():
    """_apply_session_type_visibility must only use tags in ALL_VISIBILITY_TAGS ∪ CATEGORY_TAGS."""
    from autoskillit.core import ALL_VISIBILITY_TAGS, CATEGORY_TAGS

    allowed = ALL_VISIBILITY_TAGS | CATEGORY_TAGS

    session_type_path = _SRC_ROOT / "server" / "lifecycle" / "_session_type.py"
    source = session_type_path.read_text()
    tree = ast.parse(source, filename=str(session_type_path))

    literal_tag_violations = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in ("enable", "disable"):
            continue

        for kw in node.keywords:
            if kw.arg != "tags":
                continue
            if isinstance(kw.value, ast.Set):
                for tag in _iter_literal_string_tag_values(kw.value):
                    if tag not in allowed:
                        literal_tag_violations.append(
                            f"line {node.lineno}: {tag!r} not in "
                            f"ALL_VISIBILITY_TAGS ∪ CATEGORY_TAGS"
                        )

    assert not literal_tag_violations, (
        "Tag string literals in _session_type.py not in canonical sets:\n"
        + "\n".join(f"  {v}" for v in literal_tag_violations)
    )


def _canonical_startup_visibility_loop_variable(node: ast.For) -> str | None:
    iterable = node.iter
    uses_all_visibility_tags = (
        isinstance(iterable, ast.Name) and iterable.id == "ALL_VISIBILITY_TAGS"
    ) or (
        isinstance(iterable, ast.Call)
        and isinstance(iterable.func, ast.Name)
        and iterable.func.id == "sorted"
        and len(iterable.args) == 1
        and isinstance(iterable.args[0], ast.Name)
        and iterable.args[0].id == "ALL_VISIBILITY_TAGS"
    )
    if not uses_all_visibility_tags or not isinstance(node.target, ast.Name):
        return None
    return node.target.id


def _is_loop_variable_disable_call(
    call: ast.Call, keyword: ast.keyword, loop_variable: str
) -> bool:
    return (
        isinstance(call.func, ast.Attribute)
        and call.func.attr == "disable"
        and keyword.arg == "tags"
        and isinstance(keyword.value, ast.Set)
        and len(keyword.value.elts) == 1
        and isinstance(keyword.value.elts[0], ast.Name)
        and keyword.value.elts[0].id == loop_variable
    )


def _canonical_startup_loop_disable_count(tree: ast.Module) -> int:
    count = 0
    for node in tree.body:
        if not isinstance(node, ast.For):
            continue
        loop_variable = _canonical_startup_visibility_loop_variable(node)
        if loop_variable is None:
            continue
        for statement in node.body:
            if not isinstance(statement, ast.Expr) or not isinstance(statement.value, ast.Call):
                continue
            for keyword in statement.value.keywords:
                if _is_loop_variable_disable_call(statement.value, keyword, loop_variable):
                    count += 1
    return count


def _standalone_literal_disable_diagnostics(tree: ast.Module) -> list[str]:
    diagnostics: list[str] = []
    for node in tree.body:
        if not isinstance(node, ast.Expr):
            continue
        call = node.value
        if not (
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr == "disable"
        ):
            continue
        for keyword in call.keywords:
            if keyword.arg != "tags" or not isinstance(keyword.value, ast.Set):
                continue
            tag_vals = {
                element.value
                for element in keyword.value.elts
                if isinstance(element, ast.Constant) and isinstance(element.value, str)
            }
            if tag_vals:
                diagnostics.append(f"line {node.lineno}: mcp.disable(tags={sorted(tag_vals)})")
    return diagnostics


def test_startup_disables_all_visibility_tags():
    """server/__init__.py must disable ALL_VISIBILITY_TAGS via a for-loop, not hardcoded calls.

    Structural invariant: the startup disable block must iterate over ALL_VISIBILITY_TAGS
    (directly or via sorted()) so that any new tag added to the constant is automatically
    disabled. Standalone hardcoded mcp.disable(tags={<literal>}) calls at module level are
    forbidden — they would silently bypass future tags.
    """
    server_init = _SRC_ROOT / "server" / "__init__.py"
    source = server_init.read_text()
    tree = ast.parse(source, filename=str(server_init))

    canonical_loop_count = _canonical_startup_loop_disable_count(tree)
    standalone_literal_disables = _standalone_literal_disable_diagnostics(tree)

    assert canonical_loop_count == 1, (
        "server/__init__.py must have exactly one top-level for-loop over ALL_VISIBILITY_TAGS "
        "whose body calls mcp.disable(tags={<loop_var>}). "
        f"Found {canonical_loop_count}. "
        "Replace manual per-tag mcp.disable() calls with: "
        "for tag in sorted(ALL_VISIBILITY_TAGS): mcp.disable(tags={tag})"
    )
    assert not standalone_literal_disables, (
        "server/__init__.py must not have standalone mcp.disable(tags={<literal>}) calls "
        "at module level — they bypass future ALL_VISIBILITY_TAGS additions:\n"
        + "\n".join(f"  {d}" for d in standalone_literal_disables)
    )


def test_tool_decorators_enforce_tag_partition():
    """No @mcp.tool() decorator may carry both 'kitchen' and 'fleet'/'fleet-dispatch'."""
    tools_dir = _SRC_ROOT / "server" / "tools"
    violations = []

    for path, node, decorator in _iter_tool_decorators(tools_dir):
        tags_value = None
        for keyword in decorator.keywords:
            if keyword.arg == "tags":
                tags_value = keyword.value
                break
        if tags_value is None or not isinstance(tags_value, ast.Set):
            continue
        tag_set = set(_iter_literal_string_tag_values(tags_value))
        has_kitchen = "kitchen" in tag_set
        has_fleet_subset = bool({"fleet", "fleet-dispatch"} & tag_set)
        if has_kitchen and has_fleet_subset:
            violations.append(f"{path.name}:{node.lineno} {node.name} → {sorted(tag_set)}")

    assert not violations, (
        "Tag partition violations (kitchen + fleet/fleet-dispatch on same tool):\n"
        + "\n".join(f"  {v}" for v in violations)
    )


def test_tool_tags_are_literal_sets():
    """Every @mcp.tool(tags=...) must use a set literal, not a variable or expression."""
    tools_dir = _SRC_ROOT / "server" / "tools"
    non_literals = []

    for path, node, decorator in _iter_tool_decorators(tools_dir):
        for keyword in decorator.keywords:
            if keyword.arg == "tags" and not isinstance(keyword.value, ast.Set):
                non_literals.append(
                    f"{path.name}:{node.lineno} {node.name}"
                    f" → tags is {type(keyword.value).__name__}, not Set"
                )

    assert not non_literals, (
        "Non-literal tags values in @mcp.tool() decorators (use set literals):\n"
        + "\n".join(f"  {v}" for v in non_literals)
    )


def test_fleet_tools_carry_required_subset_tag():
    """Every FLEET_TOOLS entry must have 'fleet' in its decorator tags.

    Every FLEET_DISPATCH_TOOLS entry must have 'fleet-dispatch' in its decorator tags.
    """
    from autoskillit.core import FLEET_DISPATCH_TOOLS, FLEET_TOOLS

    tools_dir = _SRC_ROOT / "server" / "tools"
    name_to_tags: dict[str, set[str]] = {}

    for _path, node, decorator in _iter_tool_decorators(tools_dir):
        tags_value = None
        for keyword in decorator.keywords:
            if keyword.arg == "tags":
                tags_value = keyword.value
                break
        if tags_value is None or not isinstance(tags_value, ast.Set):
            continue
        name_to_tags[node.name] = set(_iter_literal_string_tag_values(tags_value))

    missing_fleet = []
    for tool in FLEET_TOOLS:
        if tool not in name_to_tags or "fleet" not in name_to_tags[tool]:
            missing_fleet.append(tool)

    missing_fd = []
    for tool in FLEET_DISPATCH_TOOLS:
        if tool not in name_to_tags or "fleet-dispatch" not in name_to_tags[tool]:
            missing_fd.append(tool)

    assert not missing_fleet, f"FLEET_TOOLS missing 'fleet' tag: {sorted(missing_fleet)}"
    assert not missing_fd, (
        f"FLEET_DISPATCH_TOOLS missing 'fleet-dispatch' tag: {sorted(missing_fd)}"
    )


def test_fleet_tools_do_not_carry_kitchen_umbrella_tag():
    """TOOL_SUBSET_TAGS entries for fleet/fleet-dispatch tools must not include 'kitchen'."""
    from autoskillit.core import FLEET_DISPATCH_TOOLS, FLEET_TOOLS, TOOL_SUBSET_TAGS

    violations = []
    for tool in FLEET_TOOLS | FLEET_DISPATCH_TOOLS:
        tags = TOOL_SUBSET_TAGS.get(tool, frozenset())
        if "kitchen" in tags:
            violations.append(f"{tool} → {sorted(tags)}")

    assert not violations, (
        "Fleet/fleet-dispatch tools with 'kitchen' in TOOL_SUBSET_TAGS:\n"
        + "\n".join(f"  {v}" for v in violations)
    )
