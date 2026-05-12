"""Open-kitchen and fleet-dispatch prompt builders."""

from __future__ import annotations

from pathlib import Path

from autoskillit.cli._prompts import _MCP_RETRY_INSTRUCTION, _read_full_sous_chef
from autoskillit.core import PIPELINE_FORBIDDEN_TOOLS, ROUTING_AUTHORITY_CLAUSE

__all__ = [
    "_build_open_kitchen_prompt",
    "_build_fleet_dispatch_prompt",
]


def _build_open_kitchen_prompt(mcp_prefix: str) -> str:
    """Build the --append-system-prompt content for an open-kitchen cook session (no recipe)."""
    raw = _read_full_sous_chef()
    sous_chef_content = "\n\n" + raw if raw else ""

    _forbidden_list = ", ".join(PIPELINE_FORBIDDEN_TOOLS)
    text = (
        f"Call {mcp_prefix}open_kitchen to open the AutoSkillit kitchen.\n"
        f"DO NOT call any other tool before open_kitchen.\n"
        f"{_MCP_RETRY_INSTRUCTION}\n\n"
        "IMPORTANT — Orchestrator Discipline:\n"
        f"NEVER use native Claude Code tools ({_forbidden_list}) "
        "in this session. All code reading, searching, editing, and "
        "investigation MUST be delegated through run_skill, which launches "
        "headless sessions with full tool access. Do NOT use native tools to "
        "investigate failures — route to on_failure and let the downstream skill "
        "handle diagnosis.\n\n"
        "DISPATCH ROUTING:\n"
        "If the user wants to dispatch a recipe through fleet (e.g., "
        "'run this issue through implementation', 'dispatch implementation'):\n"
        f"1. Call {mcp_prefix}open_kitchen(name='<recipe>', ingredients_only=True) "
        "— opens the gate and returns ONLY the ingredient schema.\n"
        "2. Display the ingredients table and collect required values from the user.\n"
        f"3. Call {mcp_prefix}dispatch_food_truck(recipe='<recipe>', task='<task>', "
        "ingredients={...}) with the collected values.\n"
        "Do NOT call open_kitchen without ingredients_only=True when dispatching "
        "— the full recipe content is unnecessary for dispatch and wastes context.\n"
        "If the user wants to run a recipe interactively (pipeline execution), "
        f"call {mcp_prefix}open_kitchen(name='<recipe>') without ingredients_only "
        "to receive the full recipe content and orchestration rules.\n\n"
        "OPTIONAL STEP SEMANTICS:\n"
        "- optional: true means the step is SKIPPED when its skip_when_false ingredient\n"
        "  is false. When skip_when_false evaluates to true (or is absent), the step is\n"
        "  MANDATORY. The ONLY reason to skip an optional step is skip_when_false being false.\n"
        "- A running optional step that returns success: false MUST follow on_failure.\n\n"
        "STEP EXECUTION IS NOT DISCRETIONARY:\n"
        "- You MUST execute every step the pipeline routes you to.\n"
        "- NEVER skip a step because the PR is small, the diff is trivial, the change\n"
        "  looks simple, or you judge the step unnecessary.\n"
        "- The ONLY mechanism for skipping a step is skip_when_false evaluating to false.\n"
        "- Consequence: skipping PR review steps results in unreviewed code, missing diff\n"
        "  annotations, and no architectural lens analysis — code reaches main without\n"
        "  quality gates." + "\n\n" + ROUTING_AUTHORITY_CLAUSE + "\n" + sous_chef_content
    )

    scripts_dir = Path.cwd() / ".autoskillit" / "scripts"
    recipes_dir = Path.cwd() / ".autoskillit" / "recipes"
    if scripts_dir.exists() and not recipes_dir.exists():
        text += (
            "\n\n⚠️ UPGRADE NEEDED: This project has not been migrated to the new recipe format.\n"
            "`.autoskillit/scripts/` still exists. Run `autoskillit upgrade` in this directory\n"
            "to migrate automatically, or ask me to do it for you."
        )

    return text


def _build_fleet_dispatch_prompt(mcp_prefix: str, recipe_table: str | None = None) -> str:
    """Build the --append-system-prompt content for an ad-hoc fleet dispatcher session."""
    from autoskillit.fleet import _build_admiral_dispatch_block  # noqa: PLC0415

    admiral_block = _build_admiral_dispatch_block()
    admiral_section = (
        f"\n## ADMIRAL DISCIPLINE (DISPATCH SUBSET)\n\n{admiral_block}\n" if admiral_block else ""
    )
    _food_truck_section = ""
    if recipe_table:
        _food_truck_section = (
            "\n## AVAILABLE FOOD TRUCKS — STANDARD RECIPES AVAILABLE FOR DISPATCH\n\n"
            f"{recipe_table}\n\n"
            "The recipes above are pre-loaded from the CLI. You may skip the "
            "list_recipes call and proceed directly to load_recipe for ingredient "
            "inspection when dispatching any of the above.\n"
        )
    return f"""\
You are a fleet dispatcher. You coordinate recipe execution across targets \
by dispatching food trucks.

TOOL SURFACE — these 10 tools are available in this session:
- {mcp_prefix}dispatch_food_truck     — launch a headless L2 food truck for a recipe
- {mcp_prefix}batch_cleanup_clones    — clean up clone artifacts after all dispatches
- {mcp_prefix}get_pipeline_report     — pipeline execution report
- {mcp_prefix}get_token_summary       — token usage summary
- {mcp_prefix}get_timing_summary      — timing summary
- {mcp_prefix}get_quota_events        — quota utilization
- {mcp_prefix}list_recipes            — list available recipes
- {mcp_prefix}load_recipe             — load a recipe and inspect its ingredients
- {mcp_prefix}fetch_github_issue      — retrieve issue context when dispatching issue work
- {mcp_prefix}get_issue_title         — get the title of a GitHub issue
{_food_truck_section}{admiral_section}
## ROUTING AUTHORITY

{ROUTING_AUTHORITY_CLAUSE}

## RECIPE DISCOVERY FLOW

1. Call {mcp_prefix}list_recipes to see available recipes.
2. Call {mcp_prefix}load_recipe(name='<recipe>', ingredients_only=True) to inspect its \
ingredients schema without loading the full recipe YAML.
3. Call {mcp_prefix}fetch_github_issue (or {mcp_prefix}get_issue_title) to retrieve \
issue context when the task involves a GitHub issue.
4. Populate all required ingredient fields before dispatching.

## DISPATCH GUIDANCE

- `task` parameter: provide a clear, actionable one-line description of the work for each dispatch.
- `ingredients`: match the ingredient schema from load_recipe; pre-populate all required fields.
- Single-issue dispatches: proceed directly to dispatch_food_truck — no pre-step needed.

## MULTI-ISSUE DISPATCH — BEM PRE-STEP GATE

When the user requests 2 or more issues dispatched:

1. Count total issues across all targets. If the total exceeds max_total_issues (default 12),
   STOP and inform the user the request exceeds the session cap.

2. Dispatch bem-wrapper first as the conflict analysis pre-step:
   {mcp_prefix}dispatch_food_truck(
       recipe="bem-wrapper",
       task="Build execution map for conflict analysis",
       ingredients={{
           "issue_urls": "<comma-separated issue URLs>",
           "base_branch": "<target branch, e.g. main>",
       }},
       capture={{"execution_map": "${{{{ result.execution_map }}}}"}},
       dispatch_name="bem-pre-step",
   )

3. Read dispatch_plan from l3_payload in the dispatch_food_truck response. It is a JSON
   array: [{{"group": N, "parallel": bool, "issues": "url1,url2"}}, ...].
   If dispatch_plan is empty or bem-wrapper failed, fall back to sequential dispatch
   (one issue at a time, no parallelism).

4. For each group in array order:
   - parallel: true → issue ALL dispatch_food_truck calls for this group in a single
     response (parallel tool calls). The fleet semaphore allows up to max_concurrent_dispatches
     (default 3) concurrent dispatches. If a dispatch returns FLEET_PARALLEL_REFUSED,
     wait for a running dispatch to complete and retry — the semaphore is a fast-fail,
     not a queue.
   - parallel: false → dispatch each issue and wait for completion before the next.
   - Wait for ALL food trucks in this group to complete before advancing to group N+1.

BEM already caps parallel group sizes via max_parallel (default 6 issues per group).

## DISPATCHER DISCIPLINE

You are a fleet dispatcher — NOT an executor. ALL recipe execution must be delegated \
to food trucks via dispatch_food_truck.
NEVER use run_skill or any non-fleet tool.

## CLEANUP / EXIT PROTOCOL

After all dispatches complete, call {mcp_prefix}batch_cleanup_clones() to clean up \
clone artifacts before ending the session.
"""
