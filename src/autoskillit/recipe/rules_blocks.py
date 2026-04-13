"""Block-level semantic validation rules for recipe pipelines.

Each rule is registered via @block_rule and dispatched by run_semantic_rules
for every RecipeBlock in a recipe.  Block rules operate on one block at a time
via BlockContext — they must not access ctx.recipe.steps directly to express
cross-block constraints; use bctx.parent for read-only access when needed.

Budget values are loaded from block_budgets.yaml at import time (lru_cache).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Mapping

from autoskillit.core import Severity
from autoskillit.core.io import load_yaml
from autoskillit.core.paths import pkg_root
from autoskillit.recipe.registry import BlockContext, RuleFinding, block_rule


@lru_cache(maxsize=1)
def _block_budgets() -> Mapping[str, Mapping[str, Any]]:
    """Load block_budgets.yaml, cached for the lifetime of the process."""
    path = pkg_root() / "recipe" / "block_budgets.yaml"
    data = load_yaml(path)
    if not isinstance(data, dict):
        return {}
    return data  # type: ignore[return-value]


def _budget_for(block_name: str) -> dict[str, Any]:
    """Return the budget dict for a named block, falling back to DEFAULT."""
    budgets = _block_budgets()
    return dict(budgets.get(block_name, budgets.get("DEFAULT", {})))


@block_rule(
    name="block-run-cmd-budget",
    description="Block members contain at most the declared number of run_cmd steps.",
    severity=Severity.ERROR,
)
def _check_block_run_cmd_budget(bctx: BlockContext) -> list[RuleFinding]:
    budget = _budget_for(bctx.block.name).get("run_cmd", 1)
    actual = bctx.block.tool_counts.get("run_cmd", 0)
    if actual <= budget:
        return []
    return [
        RuleFinding(
            rule="block-run-cmd-budget",
            severity=Severity.ERROR,
            step_name=bctx.block.entry,
            message=(
                f"Block {bctx.block.name!r} contains {actual} run_cmd step"
                f"{'s' if actual != 1 else ''} (budget: {budget}). "
                f"Consolidate into an MCP tool."
            ),
        )
    ]


@block_rule(
    name="block-mcp-tool-budget",
    description="Block members contain at most the declared number of non-run_cmd MCP tool calls.",
    severity=Severity.ERROR,
)
def _check_block_mcp_tool_budget(bctx: BlockContext) -> list[RuleFinding]:
    budget_entry = _budget_for(bctx.block.name)
    if "mcp_tools" not in budget_entry:
        return []  # No mcp_tools budget declared for this block — skip check
    budget = int(budget_entry["mcp_tools"])
    # MCP tool calls: any tool step that is not run_cmd
    actual = sum(
        count for tool, count in bctx.block.tool_counts.items() if tool != "run_cmd"
    )
    if actual <= budget:
        return []
    return [
        RuleFinding(
            rule="block-mcp-tool-budget",
            severity=Severity.ERROR,
            step_name=bctx.block.entry,
            message=(
                f"Block {bctx.block.name!r} contains {actual} MCP tool call"
                f"{'s' if actual != 1 else ''} (budget: {budget}). "
                f"Reduce to a single consolidated tool call."
            ),
        )
    ]


@block_rule(
    name="block-gh-api-forbidden",
    description=(
        "Blocks with forbid_gh_api: true must not contain 'gh api' shell patterns "
        "in any run_cmd step."
    ),
    severity=Severity.ERROR,
)
def _check_block_gh_api_forbidden(bctx: BlockContext) -> list[RuleFinding]:
    if not _budget_for(bctx.block.name).get("forbid_gh_api", False):
        return []  # This block does not prohibit gh api shell patterns
    if bctx.block.gh_api_occurrences == 0:
        return []
    return [
        RuleFinding(
            rule="block-gh-api-forbidden",
            severity=Severity.ERROR,
            step_name=bctx.block.entry,
            message=(
                f"Block {bctx.block.name!r} has {bctx.block.gh_api_occurrences} "
                f"'gh api' shell invocation"
                f"{'s' if bctx.block.gh_api_occurrences != 1 else ''}. "
                f"MCP tools must own all GitHub API calls in declared blocks; "
                f"consolidate into a single MCP tool call."
            ),
        )
    ]


@block_rule(
    name="block-single-producer",
    description="Each capture key produced within a block must originate from exactly one step.",
    severity=Severity.WARNING,
)
def _check_block_single_producer(bctx: BlockContext) -> list[RuleFinding]:
    # Build a map: capture_key → list of step names that produce it
    producers: dict[str, list[str]] = {}
    for step in bctx.block.members:
        for cap_key in (step.capture or {}):
            producers.setdefault(cap_key, []).append(step.name)
    findings = []
    for cap_key, producer_names in producers.items():
        if len(producer_names) > 1:
            findings.append(
                RuleFinding(
                    rule="block-single-producer",
                    severity=Severity.WARNING,
                    step_name=bctx.block.entry,
                    message=(
                        f"Block {bctx.block.name!r}: capture key {cap_key!r} is produced by "
                        f"{len(producer_names)} steps ({', '.join(producer_names)}); "
                        f"expected exactly one producer."
                    ),
                )
            )
    return findings


@block_rule(
    name="block-entry-exit-reachable",
    description=(
        "Block members must form a single connected region with exactly one "
        "entry step and one exit step."
    ),
    severity=Severity.WARNING,
)
def _check_block_entry_exit_reachable(bctx: BlockContext) -> list[RuleFinding]:
    step_graph = bctx.parent.step_graph
    # Build predecessor map over the block's step_graph slice
    predecessors: dict[str, set[str]] = {}
    for src, successors in step_graph.items():
        for dst in successors:
            predecessors.setdefault(dst, set()).add(src)

    member_names = {s.name for s in bctx.block.members}
    entry_candidates = [
        s for s in bctx.block.members
        if not (predecessors.get(s.name, set()) & member_names)
    ]
    exit_candidates = [
        s for s in bctx.block.members
        if not (step_graph.get(s.name, set()) & member_names)
    ]
    findings = []
    if len(entry_candidates) != 1:
        findings.append(
            RuleFinding(
                rule="block-entry-exit-reachable",
                severity=Severity.WARNING,
                step_name=bctx.block.entry,
                message=(
                    f"Block {bctx.block.name!r} has {len(entry_candidates)} entry candidates "
                    f"(expected 1): "
                    f"{[s.name for s in entry_candidates]}. "
                    f"Block members must form a single connected region."
                ),
            )
        )
    if len(exit_candidates) != 1:
        findings.append(
            RuleFinding(
                rule="block-entry-exit-reachable",
                severity=Severity.WARNING,
                step_name=bctx.block.exit,
                message=(
                    f"Block {bctx.block.name!r} has {len(exit_candidates)} exit candidates "
                    f"(expected 1): "
                    f"{[s.name for s in exit_candidates]}. "
                    f"Block members must form a single connected region."
                ),
            )
        )
    return findings
