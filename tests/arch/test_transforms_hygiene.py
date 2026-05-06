"""Structural guards for FastMCP visibility tag hygiene.

Ensures all test fixtures that touch mcp._transforms / mcp.enable / mcp.disable
use the canonical ALL_VISIBILITY_TAGS constant, and that production code only
uses tag strings present in ALL_VISIBILITY_TAGS ∪ CATEGORY_TAGS.
"""

from __future__ import annotations

import ast
from pathlib import Path

_TESTS_ROOT = Path(__file__).parent.parent
_SRC_ROOT = _TESTS_ROOT.parent / "src" / "autoskillit"


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
            (
                isinstance(d, ast.Call)
                and isinstance(d.func, ast.Attribute)
                and d.func.attr == "fixture"
            )
            or (isinstance(d, ast.Attribute) and d.attr == "fixture")
            for d in node.decorator_list
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

                for child in ast.walk(item):
                    if (
                        isinstance(child, ast.Call)
                        and isinstance(child.func, ast.Attribute)
                        and child.func.attr == "disable"
                    ):
                        for kw in child.keywords:
                            if kw.arg == "tags" and isinstance(kw.value, ast.Set):
                                tag_vals = {
                                    elt.value
                                    for elt in kw.value.elts
                                    if isinstance(elt, ast.Constant)
                                }
                                if tag_vals:
                                    rel = path.relative_to(_TESTS_ROOT)
                                    violations.append(
                                        f"{rel}:{child.lineno} "
                                        f"class={node.name} "
                                        f"hardcoded={sorted(tag_vals)}"
                                    )

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
                (
                    isinstance(d, ast.Call)
                    and isinstance(d.func, ast.Attribute)
                    and d.func.attr == "fixture"
                )
                or (isinstance(d, ast.Attribute) and d.attr == "fixture")
                for d in node.decorator_list
            )
            if is_fixture:
                continue

            clear_calls = []
            for child in ast.walk(node):
                if (
                    isinstance(child, ast.Call)
                    and isinstance(child.func, ast.Attribute)
                    and child.func.attr == "clear"
                    and isinstance(child.func.value, ast.Attribute)
                    and child.func.value.attr == "_transforms"
                ):
                    clear_calls.append(child.lineno)

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

    session_type_path = _SRC_ROOT / "server" / "_session_type.py"
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
                for elt in kw.value.elts:
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                        if elt.value not in allowed:
                            literal_tag_violations.append(
                                f"line {node.lineno}: {elt.value!r} not in "
                                f"ALL_VISIBILITY_TAGS ∪ CATEGORY_TAGS"
                            )

    assert not literal_tag_violations, (
        "Tag string literals in _session_type.py not in canonical sets:\n"
        + "\n".join(f"  {v}" for v in literal_tag_violations)
    )


def test_tool_decorators_enforce_tag_partition():
    """No @mcp.tool() decorator may carry both 'kitchen' and 'fleet'/'fleet-dispatch'."""
    tools_dir = _SRC_ROOT / "server" / "tools"
    violations = []

    for path in sorted(tools_dir.glob("tools_*.py")):
        source = path.read_text()
        tree = ast.parse(source, filename=str(path))

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue

            func_name = node.name

            for decorator in node.decorator_list:
                if not (
                    isinstance(decorator, ast.Call)
                    and isinstance(decorator.func, ast.Attribute)
                    and decorator.func.attr == "tool"
                ):
                    continue

                tags_value = None
                for kw in decorator.keywords:
                    if kw.arg == "tags":
                        tags_value = kw.value
                        break

                if tags_value is None or not isinstance(tags_value, ast.Set):
                    continue

                tag_set = {
                    elt.value
                    for elt in tags_value.elts
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                }

                has_kitchen = "kitchen" in tag_set
                has_fleet_subset = bool({"fleet", "fleet-dispatch"} & tag_set)

                if has_kitchen and has_fleet_subset:
                    violations.append(f"{path.name}:{node.lineno} {func_name} → {sorted(tag_set)}")

    assert not violations, (
        "Tag partition violations (kitchen + fleet/fleet-dispatch on same tool):\n"
        + "\n".join(f"  {v}" for v in violations)
    )


def test_fleet_tools_carry_required_subset_tag():
    """Every FLEET_TOOLS entry must have 'fleet' in its decorator tags.

    Every FLEET_DISPATCH_TOOLS entry must have 'fleet-dispatch' in its decorator tags.
    """
    from autoskillit.core import FLEET_DISPATCH_TOOLS, FLEET_TOOLS

    tools_dir = _SRC_ROOT / "server" / "tools"
    name_to_tags: dict[str, set[str]] = {}

    for path in sorted(tools_dir.glob("tools_*.py")):
        source = path.read_text()
        tree = ast.parse(source, filename=str(path))

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue

            func_name = node.name

            for decorator in node.decorator_list:
                if not (
                    isinstance(decorator, ast.Call)
                    and isinstance(decorator.func, ast.Attribute)
                    and decorator.func.attr == "tool"
                ):
                    continue

                tags_value = None
                for kw in decorator.keywords:
                    if kw.arg == "tags":
                        tags_value = kw.value
                        break

                if tags_value is None or not isinstance(tags_value, ast.Set):
                    continue

                tag_set = {
                    elt.value
                    for elt in tags_value.elts
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                }
                name_to_tags[func_name] = tag_set

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
