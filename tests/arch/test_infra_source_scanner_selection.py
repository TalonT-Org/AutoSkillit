"""Selection contract: an infra or hooks test that scans the whole source package reads
code by path, so no import edge selects it; it must be named in the matching
unconditional set. Detects `.rglob` on a `Path(__file__)`-rooted chain, directly or via
up to four name bindings. Conservative syntax matching may over-select fixture roots or
names rebound in another scope.
"""

from __future__ import annotations

import ast
import copy
import textwrap
from pathlib import Path

import pytest

from tests._test_filter import _HOOKS_UNCONDITIONAL_FILES, _INFRA_UNCONDITIONAL_FILES

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


def _bindings(tree: ast.AST) -> dict[str, list[ast.expr]]:
    bindings: dict[str, list[ast.expr]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name):
                bindings.setdefault(target.id, []).append(node.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.value is not None:
                bindings.setdefault(node.target.id, []).append(node.value)
    return bindings


def _receiver_forms(receiver: ast.expr, bindings: dict[str, list[ast.expr]]) -> list[ast.expr]:
    forms = [receiver]
    frontier = [receiver]
    seen = {ast.dump(receiver)}
    for _ in range(4):
        next_frontier: list[ast.expr] = []
        for form in frontier:
            names = sorted({node.id for node in ast.walk(form) if isinstance(node, ast.Name)})
            for name in names:
                for value in bindings.get(name, []):

                    class _ReplaceName(ast.NodeTransformer):
                        def visit_Name(self, node: ast.Name) -> ast.AST:
                            if node.id == name:
                                return copy.deepcopy(value)
                            return node

                    expanded = _ReplaceName().visit(copy.deepcopy(form))
                    key = ast.dump(expanded)
                    if key not in seen:
                        seen.add(key)
                        next_frontier.append(expanded)
        if not next_frontier:
            break
        forms.extend(next_frontier)
        frontier = next_frontier
    return forms


def _is_package_root(expression: ast.expr) -> bool:
    suffix: list[str] = []
    base = expression
    while isinstance(base, ast.BinOp) and isinstance(base.op, ast.Div):
        if not isinstance(base.right, ast.Constant) or not isinstance(base.right.value, str):
            break
        suffix.append(base.right.value)
        base = base.left
    return any(
        isinstance(node, ast.Name) and node.id == "__file__" for node in ast.walk(base)
    ) and tuple(reversed(suffix)) in {("src",), ("src", "autoskillit")}


def _root_scan_lines(tree: ast.AST) -> list[int]:
    bindings = _bindings(tree)
    lines: list[int] = []
    for call in ast.walk(tree):
        if (
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr == "rglob"
            and any(_is_package_root(form) for form in _receiver_forms(call.func.value, bindings))
        ):
            lines.append(call.lineno)
    return sorted(lines)


@pytest.mark.parametrize(
    ("source", "expected_hits"),
    [
        pytest.param(
            """
            from pathlib import Path
            TESTS_ROOT = Path(__file__).parent
            SRC = Path(__file__).resolve().parents[2] / "src" / "autoskillit"
            TESTS_ROOT.rglob("test_*.py")
            SRC.rglob("*.py")
            """,
            1,
            id="bound-source-package-root",
        ),
        pytest.param(
            """
            from pathlib import Path
            REPO_ROOT = Path(__file__).resolve().parents[2]
            (REPO_ROOT / "src").rglob("*.py")
            """,
            1,
            id="repository-root-plus-src",
        ),
        pytest.param(
            """
            from pathlib import Path
            def scan():
                root = Path(__file__).parents[2] / "src" / "autoskillit"
                return list(root.rglob("*.py"))
            """,
            1,
            id="function-local-binding",
        ),
        pytest.param(
            """
            from pathlib import Path
            _REPO_ROOT = Path(__file__).resolve().parents[2]
            _SRC_ROOT = _REPO_ROOT / "src" / "autoskillit"
            _SRC_ROOT.rglob("*.py")
            """,
            1,
            id="two-hop-binding",
        ),
        pytest.param(
            """
            from pathlib import Path
            SRC = Path(__file__).resolve().parents[2] / "src" / "autoskillit"
            (SRC / "hooks").rglob("*.py")
            """,
            0,
            id="non-root-subdirectory",
        ),
        pytest.param(
            """
            from pathlib import Path
            SRC = Path(__file__).resolve().parents[2] / "src" / "autoskillit"
            SRC.glob("*.py")
            """,
            0,
            id="glob-is-not-rglob",
        ),
        pytest.param(
            """
            from pathlib import Path
            TESTS_ROOT = Path(__file__).parent
            SRC = Path(__file__).resolve().parents[2] / "src" / "autoskillit"
            TESTS_ROOT.rglob("test_*.py")
            """,
            0,
            id="tests-root-does-not-match",
        ),
        pytest.param(
            """
            from pathlib import Path
            SRC = Path("__file__").parents[2] / "src" / "autoskillit"
            SRC.rglob("*.py")
            """,
            0,
            id="file-spelling-is-not-file-name",
        ),
    ],
)
def test_detector_classifies_receivers(source: str, expected_hits: int) -> None:
    tree = ast.parse(textwrap.dedent(source))
    assert len(_root_scan_lines(tree)) == expected_hits


def test_infra_and_hooks_source_scanners_are_unconditional() -> None:
    tests_root = Path(__file__).resolve().parents[1]
    scanner_sets = (
        (tests_root / "infra", _INFRA_UNCONDITIONAL_FILES),
        (tests_root / "hooks", _HOOKS_UNCONDITIONAL_FILES),
    )
    scanner_names: dict[str, set[str]] = {}
    violations: list[str] = []
    for directory, unconditional_files in scanner_sets:
        hits: set[str] = set()
        for path in sorted(directory.glob("test_*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except SyntaxError as error:
                pytest.fail(f"Could not parse {path}: {error}")
            if _root_scan_lines(tree):
                hits.add(path.name)
        scanner_names[directory.name] = hits
        set_name = (
            "_INFRA_UNCONDITIONAL_FILES"
            if directory.name == "infra"
            else "_HOOKS_UNCONDITIONAL_FILES"
        )
        violations.extend(
            f"add `{name}` to `{set_name}` in tests/_test_filter.py"
            for name in sorted(hits - unconditional_files)
        )

    required_infra_scanners = {
        "test_schema_version_convention.py",
        "test_plugin_source_ratchets.py",
    }
    assert required_infra_scanners <= scanner_names["infra"]
    assert not violations, "\n".join(violations)
