"""Open-kitchen and fleet-dispatch prompt builders."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from autoskillit.cli.prompts._prompts import (
    _MCP_RETRY_INSTRUCTION,
    _backend_supplement,
    _read_full_sous_chef,
)
from autoskillit.core import PIPELINE_FORBIDDEN_TOOLS, ROUTING_AUTHORITY_CLAUSE
from autoskillit.execution import codex_recipe_delivery_calling_contract

if TYPE_CHECKING:
    from autoskillit.workspace import EffectiveSkillCatalog

__all__ = [
    "_build_open_kitchen_prompt",
    "_build_fleet_dispatch_prompt",
]


def _build_open_kitchen_prompt(
    mcp_prefix: str,
    has_unguarded_filesystem_access: bool = False,
    skill_catalog: EffectiveSkillCatalog | None = None,
    project_dir: Path | None = None,
    backend: object | None = None,
) -> str:
    """Build the --append-system-prompt content for an open-kitchen cook session (no recipe)."""
    raw = _read_full_sous_chef(
        skill_catalog,
        project_dir=project_dir,
        backend=backend,
    )
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
        'If the user provides extra guidance for the food truck (e.g., "use opus model", '
        "\"skip review\"), pass it via caller_instructions='<guidance>'.\n"
        "Do NOT call open_kitchen without ingredients_only=True when dispatching "
        "— the full recipe content is unnecessary for dispatch and wastes context.\n"
        "\n\n"
        "POST-DISPATCH DIAGNOSTICS:\n"
        "After dispatch_food_truck returns, check the result envelope for a "
        '"health_report" field:\n'
        "- If health_report is absent or null: no diagnostic report was "
        "generated. Skip this step.\n"
        "- If health_report.findings is empty or all findings have severity "
        '"informational": report to the user "Pipeline diagnostics clean '
        '— no anomalies detected."\n'
        '- If findings include "confirmed_bug", "regression", or '
        '"anomaly": display each finding\'s severity, step_group, summary, '
        "and evidence to the user so they can act on them.\n\n"
        "If the user wants to run a recipe interactively (pipeline execution), "
        f"call {mcp_prefix}open_kitchen(name='<recipe>') without ingredients_only. "
        "The response is a bounded envelope (step-flow skeleton, orchestration rules, "
        "and a pull reference). To read a step's full definition before executing it, "
        f"call {mcp_prefix}get_recipe_section(section='<step_name>', "
        "recipe_name=recipe_pull.recipe_name, "
        "producer_tool=recipe_pull.producer_tool, "
        "descriptor_version=recipe_pull.descriptor_version, "
        "schema_version=recipe_pull.schema_version, "
        "payload_sha256=recipe_pull.payload_sha256, "
        "artifact_blob_sha256=recipe_pull.artifact_blob_sha256, "
        "artifact_blob_size_bytes=recipe_pull.artifact_blob_size_bytes, "
        "body_sha256=recipe_pull.body_sha256, "
        "body_size_bytes=recipe_pull.body_size_bytes). "
        "Require a known pagination_version and stable section_registry_sha256, "
        "section_sha256, and page_plan_sha256 across pages. Reconstruct raw-text by "
        "concatenating byte ranges; json-array-page by JSON-decoding and extending; "
        "json-scalar-page by JSON-decoding and concatenating strings; and "
        "json-element-fragment by JSON-decoding fragments, concatenating and verifying "
        "the canonical element, then parsing it once. Reject unknown pagination_version "
        "or unknown content_format; do not guess. Follow has_more/next_part, and require "
        "a terminal page to omit next_part. "
        "Do not read recipe YAML files directly.\n\n"
        "OPTIONAL STEP SEMANTICS:\n"
        "- optional: true means the step is SKIPPED when its skip_when_false ingredient\n"
        "  resolves to false. skip_when_false ingredient references are resolved\n"
        '  server-side; you may see literal "false" (skip) or no field (mandatory).\n'
        "- A running optional step that returns success: false MUST follow on_failure.\n\n"
        "STEP EXECUTION IS NOT DISCRETIONARY:\n"
        "- You MUST execute every step the pipeline routes you to.\n"
        "- NEVER skip a step because the PR is small, the diff is trivial, the change\n"
        "  looks simple, or you judge the step unnecessary.\n"
        "- skip_when_false ingredient references are resolved server-side; you may see\n"
        '  literal "false" (skip) or no skip_when_false field (mandatory). Resolved\n'
        "  steps also omit the configuration-only on_skip continuation.\n"
        "- Consequence: skipping PR review steps results in unreviewed code, missing diff\n"
        "  annotations, and no architectural lens analysis — code reaches main without\n"
        "  quality gates.\n\n"
        f"## ROUTING AUTHORITY\n\n{ROUTING_AUTHORITY_CLAUSE}\n" + sous_chef_content
    )

    scripts_dir = Path.cwd() / ".autoskillit" / "scripts"
    recipes_dir = Path.cwd() / ".autoskillit" / "recipes"
    if scripts_dir.exists() and not recipes_dir.exists():
        text += (
            "\n\n⚠️ UPGRADE NEEDED: This project has not been migrated to the new recipe format.\n"
            "`.autoskillit/scripts/` still exists. Run `autoskillit upgrade` in this directory\n"
            "to migrate automatically, or ask me to do it for you."
        )

    return (
        text
        + _backend_supplement(has_unguarded_filesystem_access)
        + "\n\n"
        + codex_recipe_delivery_calling_contract(mcp_prefix=mcp_prefix)
    )


def _build_fleet_dispatch_prompt(
    mcp_prefix: str,
    recipe_table: str | None = None,
    max_total_issues: int = 12,
    max_concurrent_dispatches: int = 3,
    has_unguarded_filesystem_access: bool = False,
) -> str:
    """Build the --append-system-prompt content for an ad-hoc fleet dispatcher session."""
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

TOOL SURFACE — these 11 tools are available in this session:
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
- {mcp_prefix}reset_dispatch          — reset a failed dispatch and clean stale artifacts
{_food_truck_section}
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
- `caller_instructions`: when the user provides extra guidance for the food truck session
  (e.g., 'use model opus for implement', 'skip review if diff is under 20 lines'),
  pass it as caller_instructions. This free-text is injected into the L2 system prompt
  as authoritative guidance from the dispatcher.

## DISPATCH RESUME PROTOCOL

When a dispatch_food_truck call returns with dispatch_status="resumable":

- The dispatch was interrupted mid-run with partial progress.
- You MUST re-dispatch using the resume fields from the prior result:
  resume_session_id = <dispatched_session_id from prior result>
  prior_dispatch_id = <dispatch_id from prior result>
  resume_checkpoint = <resume_checkpoint from prior result, if present>
  Add allow_reentry=true to ingredients.
- NEVER start a fresh dispatch for the same issue without resume parameters.
  A PreToolUse guard will block fresh dispatches on already-claimed issues.
- If the guard blocks your call, find the prior dispatch result and extract
  the resume fields listed above.
- If the prior session is unrecoverable and stale artifacts remain (in-progress label,
  open PR), call {mcp_prefix}reset_dispatch(dispatch_id=<prior_dispatch_id>) to clean up,
  then re-dispatch fresh with a new dispatch_name. If reset_dispatch fails, report to the
  human operator.

## INFRASTRUCTURE FAILURE RECOVERY

When a dispatch_food_truck call returns with dispatch_status="failure" and
the reason is an infrastructure code — any of:
  fleet_l3_no_result_block, fleet_l3_timeout, fleet_l3_startup_or_crash,
  fleet_l3_parse_failed, fleet_acquire_timeout, fleet_process_stale,
  fleet_hard_refusal_headless, fleet_cleanup_failed,
  fleet_resume_session_missing

These are infrastructure failures, not proof that the dispatched work had no
effects. Read effect_provenance.retry_disposition before choosing recovery:

- [fresh-only-on-proof] fresh_dispatch_allowed: every retry-relevant effect is
  proven not started or authoritatively compensated. Retry fresh with a new
  dispatch_name and the same ingredients.
- [resume-confirmed-effect] resume_by_identity: an effect or commit is
  confirmed. Preserve dispatch_id and dispatched_session_id and resume that
  identity; never create a fresh dispatch.
- [reconcile-ambiguity] reconcile_required: an effect started without
  authoritative confirmation. Reconcile the recorded operation/downstream
  identities. Do not redispatch the ambiguous effect and do not create a fresh
  dispatch.
- [missing-provenance-fails-closed] Missing effect_provenance never authorizes a
  fresh dispatch.

[cleanup-is-orthogonal] Local process cleanup, label cleanup, or compensation
claims are orthogonal evidence. [remote-effects-survive-cleanup] They authorize
a fresh dispatch only when retry_disposition explicitly equals
fresh_dispatch_allowed; an empty local survivor set does not prove remote
effects were reversed.

Action:
- Follow the explicit retry_disposition. For identity-based recovery, pass the
  prior dispatched_session_id as resume_session_id and dispatch_id as
  prior_dispatch_id.
- Call {mcp_prefix}reset_dispatch(dispatch_id=<prior_dispatch_id>) only as an
  explicit reconciliation step. After reset, retry fresh only when the returned
  provenance explicitly authorizes fresh_dispatch_allowed.
- If the retry also fails with an infrastructure code: report to the human
  operator. Do not retry more than once.

For quota_exhausted or fleet_quota_exhausted failures, these have a separate
retry mechanism — do not apply this section.

## MULTI-ISSUE DISPATCH — BEM PRE-STEP GATE — MANDATORY

**CRITICAL**: BEM (build-execution-map) is what determines whether issues are independent \
or share overlapping files and semantic dependencies. You cannot assess this on your own — \
only BEM can. Any time the user's request contains 2 or more issues — regardless of how \
many individual dispatch_food_truck calls you plan to make — you MUST follow this section. \
There are no exceptions.

**NEVER:**
- NEVER dispatch 2 or more issue-bearing food trucks without first completing the
  bem-wrapper pre-step.
- NEVER assume issues are independent without running BEM — BEM is what determines independence.
- NEVER call dispatch_food_truck for individual issues in parallel before the execution map \
has been produced and read.

1. Count total issues across all targets. If the total exceeds
   max_total_issues ({max_total_issues}), STOP and inform the user the
   request exceeds the session cap.

2. You MUST dispatch bem-wrapper first as the conflict analysis pre-step:
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
   array: [{{"group": N, "parallel": bool, "issues": "url1,url2"}},
   {{"group": M, "parallel": bool, "issues": "urlN", "gated": true, "gated_by": [887]}}, ...].
   If dispatch_plan is empty or bem-wrapper failed, fall back to sequential dispatch
   (one issue at a time, no parallelism).

**Gated groups:** If a dispatch_plan entry has `"gated": true`, do NOT dispatch it.
Hold gated groups — they are blocked by in-progress issues listed in `gated_by`.
After all non-gated groups complete, report held gated groups in the session result
with their blocker issue numbers. Do not poll or wait for blockers to clear — the
sous-chef path handles deferred issue lifecycle.

4. For each group in array order:
   - parallel: true → issue ALL dispatch_food_truck calls for this group in a single
     response (parallel tool calls). The fleet semaphore allows up to
     max_concurrent_dispatches ({max_concurrent_dispatches}) concurrent
     dispatches. If a dispatch returns FLEET_PARALLEL_REFUSED,
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
""" + _backend_supplement(has_unguarded_filesystem_access)
