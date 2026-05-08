"""Block extraction: group steps by block annotation with entry/exit tracking."""

from __future__ import annotations

from autoskillit.core import get_logger
from autoskillit.recipe.schema import Recipe, RecipeBlock, RecipeStep

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Block extraction helpers
# ---------------------------------------------------------------------------


def _count_by_tool(members: list[RecipeStep]) -> dict[str, int]:
    """Count tool occurrences across a list of steps."""
    counts: dict[str, int] = {}
    for step in members:
        key = step.tool or ""
        if key:
            counts[key] = counts.get(key, 0) + 1
    return counts


def _count_gh_api(step: RecipeStep) -> int:
    """Count occurrences of 'gh api' in a run_cmd step's cmd argument."""
    if step.tool != "run_cmd":
        return 0
    cmd = (step.with_args or {}).get("cmd", "") or ""
    return cmd.count("gh api")


def extract_blocks(
    recipe: Recipe,
    step_graph: dict[str, set[str]],
    *,
    predecessors: dict[str, set[str]] | None = None,
) -> tuple[RecipeBlock, ...]:
    """Extract named block regions from a recipe's routing graph.

    Groups steps that share the same ``step.block`` value, then computes the
    entry step (no in-block predecessor) and exit step (no in-block successor)
    for each group.  The step_graph is the forward adjacency dict produced by
    ``_build_step_graph``; a local inverse is built here to find predecessors.

    Steps without a ``block`` annotation are ignored.  Recipes with no block
    annotations return an empty tuple.
    """
    if predecessors is None:
        # Build predecessor map by inverting the forward step_graph edges.
        predecessors = {}
        for src, successors in step_graph.items():
            for dst in successors:
                predecessors.setdefault(dst, set()).add(src)

    by_name: dict[str, list[RecipeStep]] = {}
    for step in recipe.steps.values():
        if step.block is not None:
            by_name.setdefault(step.block, []).append(step)

    blocks: list[RecipeBlock] = []
    for name, members in by_name.items():
        member_names = {s.name for s in members}
        # Entry: no in-block predecessor (predecessor set ∩ member_names is empty)
        entry_candidates = [
            s for s in members if not (predecessors.get(s.name, set()) & member_names)
        ]
        # Exit: no in-block successor (successor set ∩ member_names is empty)
        exit_candidates = [
            s for s in members if not (step_graph.get(s.name, set()) & member_names)
        ]
        entry_name: str
        if entry_candidates:
            entry_name = entry_candidates[0].name
        else:
            logger.warning(
                "block %r has no graph-reachable entry step; falling back to first member",
                name,
            )
            entry_name = members[0].name

        exit_name: str
        if exit_candidates:
            exit_name = exit_candidates[0].name
        else:
            logger.warning(
                "block %r has no graph-reachable exit step; falling back to last member",
                name,
            )
            exit_name = members[-1].name

        blocks.append(
            RecipeBlock(
                name=name,
                entry=entry_name,
                exit=exit_name,
                members=tuple(members),
                tool_counts=_count_by_tool(members),
                gh_api_occurrences=sum(_count_gh_api(s) for s in members),
            )
        )
    return tuple(blocks)
