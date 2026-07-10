"""Tool registry parity: canonical ``ToolDef`` registry matches decorated handler surface.

The recipe layer (IL-2) cannot import server handlers (IL-3) without violating
the import-layer contract, so this test enforces registry parity at the AST and
import level. Any drift between the registry and the live handlers breaks the
single namespace guarantee that the ``dead-with-param`` WARNING rule,
``unsupported-run-skill-param`` ERROR rule, and ``DeliveryEvidence``
``unsupported_keys`` set all rely on.

Three layers of enforcement:

1. **AST discovery** — discover every ``@mcp.tool()`` decorated function under
   ``src/autoskillit/server/tools/``, extract its signature, and filter out
   framework-injected context params (those annotated ``Context`` / ``ToolContext``
   or with ``CurrentContext()`` defaults).
2. **Reverse coverage** — every recipe-callable handler must have a matching
   ``ToolDef`` entry; every ``ToolDef`` entry must correspond to a live handler.
3. **Framework-only exclusions** — every handler that lacks a registry entry
   must appear in ``FRAMEWORK_ONLY_EXCLUSIONS`` with explicit justification
   documented in ``tool_registry.py``.

This test runs at ``layer("server")`` because it inspects ``server/tools/``.
The registry under test lives in IL-2 (``recipe/tool_registry.py``); the
reverse direction is enforced here.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

import pytest

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


_SERVER_TOOLS_DIR: Final[Path] = (
    Path(__file__).resolve().parent.parent.parent / "src" / "autoskillit" / "server" / "tools"
)


def _tools_files() -> list[Path]:
    """Return all tool implementation modules (excluding helper submodules)."""
    return sorted(p for p in (_SERVER_TOOLS_DIR).glob("tools_*.py"))


def _is_injected_framework_param(arg: ast.arg, default: ast.expr | None) -> bool:
    """A parameter is framework-injected when its annotation is Context/ToolContext
    or its default value is a CurrentContext() call.

    AST-level filtering — we do not have a runtime type here, so we look at the
    annotation identifier and the default call name. This avoids classifying
    arbitrary public coroutines as tools by relying on the decorator + signature
    pair.
    """
    ann = arg.annotation
    if isinstance(ann, ast.Name) and ann.id in {"Context", "ToolContext"}:
        return True
    if isinstance(ann, ast.Attribute) and ann.attr in {"Context", "ToolContext"}:
        return True
    if isinstance(default, ast.Call) and isinstance(default.func, ast.Name):
        if default.func.id == "CurrentContext":
            return True
    return False


def _collect_decorated_handlers(path: Path) -> dict[str, tuple[tuple[str, ...], Path, int]]:
    """Return {func_name: (param_names, file_path, lineno)} for every ``@mcp.tool()``.

    ``param_names`` excludes framework-injected context parameters so the
    result mirrors what the MCP wire protocol exposes to recipe authors.
    """
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    out: dict[str, tuple[tuple[str, ...], Path, int]] = {}

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        is_tool = False
        for dec in node.decorator_list:
            if (
                isinstance(dec, ast.Call)
                and isinstance(dec.func, ast.Attribute)
                and dec.func.attr == "tool"
                and isinstance(dec.func.value, ast.Name)
                and dec.func.value.id == "mcp"
            ):
                is_tool = True
                break
        if not is_tool:
            continue

        args = node.args.args
        defaults = node.args.defaults
        defaults_aligned: list[ast.expr | None] = [None] * (len(args) - len(defaults))
        defaults_aligned.extend(defaults)

        params: list[str] = []
        for arg, default in zip(args, defaults_aligned, strict=True):
            if _is_injected_framework_param(arg, default):
                continue
            params.append(arg.arg)
        out[node.name] = (tuple(params), path, node.lineno)

    return out


def _all_decorated_handlers() -> dict[str, tuple[tuple[str, ...], Path, int]]:
    """Aggregate every ``@mcp.tool()`` decorated function across all tool modules."""
    out: dict[str, tuple[tuple[str, ...], Path, int]] = {}
    for path in _tools_files():
        out.update(_collect_decorated_handlers(path))
    return out


class TestToolRegistryParity:
    """Canonical ToolDef registry ↔ live @mcp.tool handler parity."""

    def test_registry_covers_all_handlers(self) -> None:
        """Every ``@mcp.tool()`` handler must either appear in the registry
        or be declared in ``FRAMEWORK_ONLY_EXCLUSIONS``.
        """
        from autoskillit.recipe.tool_registry import (
            FRAMEWORK_ONLY_EXCLUSIONS,
            all_recipe_tools,
            is_framework_only,
        )

        handlers = _all_decorated_handlers()
        registry_names = all_recipe_tools()
        exclusions = FRAMEWORK_ONLY_EXCLUSIONS

        assert handlers, "No @mcp.tool handlers discovered — glob is wrong"

        missing: list[str] = []
        for func_name, (params, path, lineno) in handlers.items():
            _ = params  # used in registry_signatures_match test
            if func_name in registry_names:
                continue
            if is_framework_only(func_name):
                if func_name not in exclusions:
                    missing.append(
                        f"{path.name}:{lineno}: {func_name!r} is framework-only "
                        f"but missing from FRAMEWORK_ONLY_EXCLUSIONS"
                    )
                continue
            missing.append(
                f"{path.name}:{lineno}: {func_name!r} is a @mcp.tool() handler "
                f"but is missing from ToolDef registry (add to "
                f"src/autoskillit/recipe/tool_registry.py or "
                f"FRAMEWORK_ONLY_EXCLUSIONS with documented reason)"
            )

        assert not missing, (
            "Tool registry does not cover every @mcp.tool() handler:\n\n"
            + "\n".join(f"  {m}" for m in missing)
        )

    def test_registry_signatures_match_handler_signatures(self) -> None:
        """Every ``ToolDef`` entry must have the same parameter set as its
        decorated handler signature (after framework-injected context exclusion).
        """
        from autoskillit.recipe.tool_registry import for_tool

        handlers = _all_decorated_handlers()
        mismatches: list[str] = []
        for func_name, (params, path, lineno) in handlers.items():
            _ = path  # path used in next test for stale entries
            td = for_tool(func_name)
            if td is None:
                continue  # framework-only — covered by other test
            expected = frozenset(params)
            actual = td.param_set
            if expected != actual:
                only_handler = sorted(expected - actual)
                only_registry = sorted(actual - expected)
                detail_parts: list[str] = []
                if only_handler:
                    detail_parts.append(f"only in handler signature: {only_handler}")
                if only_registry:
                    detail_parts.append(f"only in ToolDef: {only_registry}")
                mismatches.append(f"{func_name!r} ({lineno}): " + "; ".join(detail_parts))

        assert not mismatches, (
            "ToolDef registry drift — handler signatures changed but "
            "tool_registry.py was not updated:\n\n" + "\n".join(f"  {m}" for m in mismatches)
        )

    def test_registry_has_no_stale_entries(self) -> None:
        """Every ``ToolDef`` entry must correspond to a live ``@mcp.tool()`` handler.

        Catches orphaned registry definitions whose handler was removed or
        renamed — those would silently pass through ``unsupported_params`` as
        zero mismatches for any ``with:`` block.
        """
        from autoskillit.recipe.tool_registry import all_recipe_tools

        handlers = _all_decorated_handlers()
        registry_names = all_recipe_tools()
        handler_names = set(handlers)

        stale = sorted(registry_names - handler_names)
        assert not stale, (
            f"ToolDef registry contains stale entries with no @mcp.tool() "
            f"handler: {stale}. Remove from "
            f"src/autoskillit/recipe/tool_registry.py."
        )

    def test_framework_only_exclusions_have_no_registry_entry(self) -> None:
        """Tools listed in FRAMEWORK_ONLY_EXCLUSIONS must NOT also appear in
        the recipe-callable ToolDef registry — the two sets are disjoint.
        """
        from autoskillit.recipe.tool_registry import (
            FRAMEWORK_ONLY_EXCLUSIONS,
            all_recipe_tools,
        )

        overlap = FRAMEWORK_ONLY_EXCLUSIONS & all_recipe_tools()
        assert not overlap, (
            f"Tools listed in both FRAMEWORK_ONLY_EXCLUSIONS and the recipe "
            f"registry: {sorted(overlap)}. Pick one — framework-only or "
            f"recipe-callable, never both."
        )

    def test_no_duplicate_registry_entries(self) -> None:
        """ToolDef names must be unique. The dataclass-based registry catches
        this at registration time, but the test guards against future
        refactors that might relax uniqueness.
        """
        from autoskillit.recipe.tool_registry import all_recipe_tools

        names = list(all_recipe_tools())
        assert len(names) == len(set(names)), (
            f"ToolDef registry contains duplicate names: "
            f"{[n for n in names if names.count(n) > 1]}"
        )

    def test_unsupported_params_helper_rejects_unknown_tool(self) -> None:
        """``unsupported_params`` is fail-closed for unknown tool names —
        the entire key set is returned so unknown tools cannot silently pass.
        """
        from autoskillit.recipe.tool_registry import unsupported_params

        result = unsupported_params("definitely_not_a_real_tool", frozenset({"foo", "bar"}))
        assert result == frozenset({"foo", "bar"}), (
            "unsupported_params must return the full key set for unknown tools (fail-closed)"
        )

    def test_unsupported_params_helper_returns_empty_for_known_tool(self) -> None:
        """All declared params for a known tool are supported — return empty."""
        from autoskillit.recipe.tool_registry import unsupported_params

        result = unsupported_params("run_skill", frozenset({"skill_command", "cwd"}))
        assert result == frozenset(), (
            f"unsupported_params must return empty set for declared params, got {sorted(result)}"
        )

    def test_unsupported_params_helper_flags_extras(self) -> None:
        """Unknown params are returned as the unsupported subset."""
        from autoskillit.recipe.tool_registry import unsupported_params

        result = unsupported_params("run_skill", frozenset({"skill_command", "unknown_thing"}))
        assert result == frozenset({"unknown_thing"}), (
            f"unsupported_params must return only the unsupported subset, got {sorted(result)}"
        )

    def test_unsupported_params_helper_framework_only_rejects_all(self) -> None:
        """Framework-only tools always return the full key set as unsupported."""
        from autoskillit.recipe.tool_registry import FRAMEWORK_ONLY_EXCLUSIONS, unsupported_params

        for tool_name in sorted(FRAMEWORK_ONLY_EXCLUSIONS):
            result = unsupported_params(tool_name, frozenset({"foo"}))
            assert result == frozenset({"foo"}), (
                f"unsupported_params must return the full key set for "
                f"framework-only tool {tool_name!r}, got {sorted(result)}"
            )
