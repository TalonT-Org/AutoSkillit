"""Guard the execution graph's single recipe-routing authority.

The allowlist is an exact architectural ownership boundary: both a new raw
recipe/routing read and removal of a pinned, intentionally-owned read fail.
Changing it therefore requires an explicit baseline review rather than a
silent broadening or narrowing of the graph authority.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.arch._helpers import SRC_ROOT

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]

_ROUTING_ATTRIBUTES = frozenset(
    {
        "on_success",
        "on_failure",
        "on_context_limit",
        "on_rate_limit",
        "on_exhausted",
        "on_skip",
        "on_result",
    }
)

_SCANNED_FILES = tuple(
    path
    for path in sorted(SRC_ROOT.rglob("*.py"))
    if path.relative_to(SRC_ROOT).parts[0] != "recipe"
)

# (module path relative to SRC_ROOT, enclosing function, exact unparsed read).
# These CLI validation paths intentionally parse recipes before launching or
# reporting on them; serving code must consume the finalized projection instead.
_ALLOWED_RAW_RECIPE_READS: frozenset[tuple[str, str, str]] = frozenset(
    {
        (
            "cli/session/_session_order.py",
            "order",
            "load_recipe(_match.path)",
        ),  # Validate the selected recipe before launching its session.
        (
            "cli/doctor/_doctor_config.py",
            "_check_standing_backend_pins_feasibility",
            "load_recipe(recipe_info.path)",
        ),  # Inspect a configured recipe while reporting invalid backend pins.
        (
            "cli/doctor/_doctor_config.py",
            "_check_local_recipe_validity",
            "load_recipe(yaml_path)",
        ),  # Parse local files for the doctor validity report.
        (
            "cli/fleet/__init__.py",
            "fleet_campaign",
            "load_recipe(match.path)",
        ),  # Validate the selected campaign before dispatching its fleet.
    }
)


def _collect_raw_recipe_reads(path: Path) -> set[tuple[str, str, str]]:
    """Return raw recipe loads and RecipeStep routing reads outside recipe.

    Fingerprints use the relative path, enclosing function, and unparsed AST
    expression so line-only edits do not move the architectural baseline.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    relative_path = str(path.relative_to(SRC_ROOT)).replace("\\", "/")
    found: set[tuple[str, str, str]] = set()
    function_stack: list[str] = []

    class _Visitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            function_stack.append(node.name)
            self.generic_visit(node)
            function_stack.pop()

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            function_stack.append(node.name)
            self.generic_visit(node)
            function_stack.pop()

        def visit_Call(self, node: ast.Call) -> None:
            is_recipe_repository_load = (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "load"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "RecipeRepository"
            )
            is_load_recipe = isinstance(node.func, ast.Name) and node.func.id == "load_recipe"
            if is_recipe_repository_load or is_load_recipe:
                enclosing_function = function_stack[-1] if function_stack else "<module>"
                found.add((relative_path, enclosing_function, ast.unparse(node)))
            self.generic_visit(node)

        def visit_Attribute(self, node: ast.Attribute) -> None:
            if node.attr in _ROUTING_ATTRIBUTES:
                enclosing_function = function_stack[-1] if function_stack else "<module>"
                found.add((relative_path, enclosing_function, ast.unparse(node)))
            self.generic_visit(node)

    _Visitor().visit(tree)
    return found


def test_raw_recipe_reads_and_routing_accesses_match_exact_allowlist() -> None:
    """Keep raw recipe loads and routing reads at the reviewed ownership sites.

    The comparison is deliberately bidirectional: a newly introduced raw read
    bypasses the finalized execution graph, while a removed allowlisted site
    must be consciously reviewed before its architectural baseline changes.
    """
    found: set[tuple[str, str, str]] = set()
    for path in _SCANNED_FILES:
        found |= _collect_raw_recipe_reads(path)

    unexpected = found - _ALLOWED_RAW_RECIPE_READS
    missing = _ALLOWED_RAW_RECIPE_READS - found
    assert not unexpected, (
        "new raw RecipeRepository.load/load_recipe call or RecipeStep routing "
        "read outside autoskillit.recipe -- use the finalized projection instead: "
        f"{sorted(unexpected)}"
    )
    assert not missing, (
        "a pinned raw recipe read is missing -- update _ALLOWED_RAW_RECIPE_READS "
        f"only after intentional baseline review: {sorted(missing)}"
    )
