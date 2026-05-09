"""L3 campaign dispatcher prompt builder."""

from __future__ import annotations

from typing import TYPE_CHECKING

from autoskillit.cli._prompts import _ingredient_table_display_instruction, _read_full_sous_chef
from autoskillit.core import RetryReason

if TYPE_CHECKING:
    from autoskillit.recipe.schema import Recipe

__all__ = [
    "_build_fleet_campaign_prompt",
    "_has_dynamic_dispatch",
    "_build_dynamic_dispatch_section",
    "_resume_reason_guidance",
]


def _has_dynamic_dispatch(campaign_recipe: Recipe) -> bool:
    return any("dispatch_plan" in d.capture for d in campaign_recipe.dispatches)


def _build_dynamic_dispatch_section(mcp_prefix: str) -> str:
    return f"""\
## DYNAMIC DISPATCH — IMPLEMENT-FINDINGS

After the dispatch that captures `dispatch_plan` completes, read the plan and launch
implement-findings food trucks dynamically. These dispatches are NOT in the manifest —
you create them yourself based on the captured plan.

**Step 1 — Read the plan:**

The captured value `${{{{ campaign.dispatch_plan }}}}` is a JSON array of groups:
```json
[
  {{"group": 1, "parallel": true, "issues": "1155,1156,1157"}},
  {{"group": 2, "parallel": false, "issues": "1158,1159"}}
]
```

If the array is empty (`[]`) there are no issues to implement — skip to INTERRUPT/CLEANUP.

**Step 2 — For each group (in array order):**

1. Parse the group's `issues` string into individual issue URLs.
2. If the group has more issues than `max_issues_per_food_truck` (default: 5), split into
   batches of that size. Name batches: `implement-findings-g{{N}}-a`, `-b`, `-c` …
   If the group fits in one batch, use the name `implement-findings-g{{N}}-a`.
3. If `parallel` is `true`: issue ALL `{mcp_prefix}dispatch_food_truck` calls for this
   group **in a single response (parallel tool calls)** — do not wait for one to
   complete before issuing the next. The fleet semaphore gates actual concurrency;
   if `at_capacity()` returns FLEET_PARALLEL_REFUSED, wait for a running dispatch to
   complete and retry — the semaphore is a fast-fail, not a queue. **This overrides the
   general sequential discipline — parallel groups are an explicit exception.**
4. If `parallel` is `false`: dispatch each batch and wait for it to complete before
   dispatching the next batch in this group.
5. Wait for ALL food trucks in this group to complete before advancing to the next group.

**Step 3 — Dispatch call format:**

```python
{mcp_prefix}dispatch_food_truck(
    recipe="implement-findings",
    task="Implement audit findings — group {{N}}, batch {{M}}",
    ingredients={{
        "issue_urls": "<comma-separated URLs for this batch>",
        "execution_map": "${{{{ campaign.execution_map }}}}",
        "base_branch": "${{{{ campaign.base_branch }}}}",
    }},
    dispatch_name="implement-findings-g{{N}}-{{letter}}",
    capture={{}},
)
```

**Step 4 — Failure handling:**

Apply the same failure rules as static dispatches. On any food truck failure:
- If `continue_on_failure` is true: mark failed, continue remaining groups.
- If `continue_on_failure` is false: halt immediately (INTERRUPT/CLEANUP).
"""


def _resume_reason_guidance(kill_reason: str) -> str:
    """Return reason-specific resume guidance for the L3 campaign dispatcher."""
    if kill_reason == RetryReason.IDLE_STALL:
        return (
            "Kill reason: idle timeout (session was waiting for an external event). "
            "Resume is safe — the session likely has partial progress on disk."
        )
    if kill_reason == RetryReason.RESUME:
        return (
            "Kill reason: transient infrastructure failure (API error or process kill). "
            "Resume is safe — retry immediately."
        )
    return "Kill reason: unknown. Resume with standard recovery."


def _build_fleet_campaign_prompt(
    campaign_recipe: Recipe,
    manifest_yaml: str,
    completed_dispatches: str,
    mcp_prefix: str,
    campaign_id: str,
    max_quota_wait_sec: int = 3600,
    resumable_dispatch_name: str = "",
    resume_session_id: str = "",
    resume_kill_reason: str = "",
    ingredients_table: str | None = None,
    prior_dispatch_id: str = "",
) -> str:
    """Build the system prompt for an L3 campaign dispatcher headless session.

    Assembles a 10-section prompt that instructs a headless Claude session to
    sequentially dispatch food trucks (L2 sessions), handle failures, respect
    quota, resume from prior state, and emit structured campaign-summary and
    progress markers.
    """
    dispatch_count = len(campaign_recipe.dispatches)
    admiral_content = _read_full_sous_chef()
    admiral_section = f"\n## ADMIRAL DISCIPLINE\n\n{admiral_content}\n" if admiral_content else ""

    has_gate_dispatches = any(d.gate for d in campaign_recipe.dispatches)

    gate_tool_line = (
        (
            f"\n- {mcp_prefix}record_gate_dispatch"
            " — persist gate dispatch outcome to campaign state"
            "\n- AskUserQuestion"
        )
        if has_gate_dispatches
        else ""
    )

    gate_section = ""
    if has_gate_dispatches:
        gate_section = f"""\
## GATE DISPATCH HANDLING

When you reach a dispatch with `gate: confirm` in the manifest:

1. Do NOT call `{mcp_prefix}dispatch_food_truck`. Gate dispatches spawn no L2 session.
2. Call `AskUserQuestion` with the dispatch's `message` field as the question text.
3. Evaluate the response:
   - Affirmative (yes / proceed / approve / confirm): call `{mcp_prefix}record_gate_dispatch`
     with `dispatch_name` and `approved=true`. Emit the %%FLEET_PROGRESS%% marker with
     state=success. Advance to the next dispatch.
   - Negative (no / reject / abort / cancel): call `{mcp_prefix}record_gate_dispatch`
     with `dispatch_name` and `approved=false`. Halt the campaign immediately
     (proceed to INTERRUPT/CLEANUP as if a dispatch had failed with
     continue_on_failure=false).

In the campaign summary, for gate dispatch entries:
- Set `status` to `success` or `failure` based on user response.
- Set `dispatched_session_id` to `""` (no food truck session was spawned).
- Set `elapsed_seconds` to the wall-clock time for the question/response exchange.
- Set all `token_usage` fields to 0.
"""

    resume_section = ""
    if completed_dispatches:
        resume_section = f"""\
## COMPLETED DISPATCHES — DO NOT RE-DISPATCH

{completed_dispatches}

Skip these dispatch names in the dispatch loop. Begin from the first
dispatch name NOT listed above.
"""

    dynamic_dispatch_section = (
        _build_dynamic_dispatch_section(mcp_prefix)
        if _has_dynamic_dispatch(campaign_recipe)
        else ""
    )

    resumable_section = ""
    if resumable_dispatch_name:
        _resume_session_line = (
            f'and pass resume_session_id="{resume_session_id}" to dispatch_food_truck'
            " so the L2 food truck session resumes from its prior context"
            if resume_session_id
            else ""
        )
        _prior_dispatch_id_line = (
            f'and pass prior_dispatch_id="{prior_dispatch_id}" to dispatch_food_truck'
            if prior_dispatch_id
            else ""
        )
        _resume_session_clause = f" {_resume_session_line}" if _resume_session_line else ""
        _prior_dispatch_id_clause = (
            f" {_prior_dispatch_id_line}" if _prior_dispatch_id_line else ""
        )
        _reason_guidance = _resume_reason_guidance(resume_kill_reason)
        _reenter_clause = (
            f" issue_urls=<remaining> and allow_reentry=true as ingredient overrides"
            f"{_resume_session_clause}{_prior_dispatch_id_clause}"
        )
        resumable_section = f"""\
## RESUMABLE DISPATCH: {resumable_dispatch_name}

This dispatch was interrupted mid-run with partial sidecar progress.
{_reason_guidance}
Re-dispatch it using compute_remaining_issues(dispatch_id, original_urls, project_dir)
to retrieve only the remaining issue URLs, then call dispatch_food_truck with{_reenter_clause}.
Do NOT re-dispatch from the full original issue list.
"""

    _ing_section = ""
    if ingredients_table:
        _ing_section = (
            "\n## RECIPE INGREDIENTS — USE THESE EXACT NAMES\n\n"
            f"{ingredients_table}\n\n"
            "Before dispatching, collect values for all required ingredients from the user "
            "via AskUserQuestion. Do not dispatch until all required values are confirmed.\n"
        )

    _first_action_section = ""
    if ingredients_table:
        _display = _ingredient_table_display_instruction(
            "the ## RECIPE INGREDIENTS section in this system prompt"
        )
        _first_action_section = (
            "\nFIRST ACTION — before asking for any inputs:\n\n"
            f"1. {_display}\n\n"
            "2. Collect ingredient values via AskUserQuestion.\n\n"
            "3. Proceed only after all required ingredient values are confirmed.\n"
        )

    return f"""\
You are a fleet campaign dispatcher. Execute campaign '{campaign_recipe.name}' autonomously.
Campaign ID: {campaign_id}. Dispatches: {dispatch_count}.
{admiral_section}
## CAMPAIGN OVERVIEW

- Name: {campaign_recipe.name}
- Campaign ID: {campaign_id}
- Description: {campaign_recipe.description}
- Dispatch count: {dispatch_count} dispatches
- Continue on failure: {campaign_recipe.continue_on_failure}
{_ing_section}
{_first_action_section}
## DISPATCH MANIFEST

The following manifest defines all dispatches for this campaign:

```yaml
{manifest_yaml}
```

## CAMPAIGN DISCIPLINE

Execute static manifest dispatches SEQUENTIALLY via {mcp_prefix}dispatch_food_truck.
Static manifest dispatches use the fleet_lock semaphore and are SEQUENTIAL — do NOT issue
static manifest calls in parallel, regardless of the fleet semaphore's
max_concurrent_dispatches setting.

Each dispatch is an independent L2 food truck session with its own kitchen context. There is NO
cross-dispatch state sharing managed by you — the runtime handles it
via capture:. There is NO cross-dispatch token aggregation.

After startup, only these tools should be used for all campaign operations:
- {mcp_prefix}dispatch_food_truck
- {mcp_prefix}batch_cleanup_clones
- {mcp_prefix}get_pipeline_report
- {mcp_prefix}get_token_summary
- {mcp_prefix}get_timing_summary
- {mcp_prefix}get_quota_events{gate_tool_line}

Explicitly FORBIDDEN: open_kitchen, close_kitchen, run_skill, and all GitHub/CI tools.
Use ONLY {mcp_prefix}dispatch_food_truck to dispatch — never run_skill.

## CAPTURE & DATA FLOW

Some dispatches declare a `capture:` block and some use `${{{{ campaign.* }}}}` references
in their `ingredients:`. The runtime handles all value extraction and interpolation
automatically — you do not need to parse, store, or forward captured values yourself.

Your only responsibility: pass the `capture` dict from the manifest YAML directly to
`{mcp_prefix}dispatch_food_truck` on every call:

```python
dispatch_food_truck(
    recipe="...",
    task="...",
    ingredients={{...}},       # may contain ${{{{ campaign.* }}}} — resolved by runtime
    capture={{...}},            # copied verbatim from the dispatch manifest
)
```

If a dispatch has no `capture:` field, pass `capture={{}}` or omit the parameter.
The `${{{{ campaign.* }}}}` references in ingredients are resolved before the L2 session
is started — the L2 food truck agent always receives concrete values.
{gate_section}{dynamic_dispatch_section}
## RESUME DISCIPLINE

When a dispatch is killed (no result block received), the infrastructure classifies it:

| Kill Reason | Policy | Rationale |
|-------------|--------|-----------|
| idle_stall | RESUME | Session was waiting for an external event — partial progress exists |
| api_error / process_killed | RESUME | Transient failure — session state is intact |
| context_exhausted | ABANDON | Session exhausted its context window — resuming hits same limit |
| thinking_stall | ABANDON | Session stuck in a loop — resuming repeats the stall |
| stale | ABANDON | Session state is stale — no meaningful progress to continue |

If the infrastructure marks a dispatch RESUMABLE, follow the RESUMABLE DISPATCH section below.
If the infrastructure marks a dispatch FAILURE, follow the FAILURE RECOVERY rules above.
Do NOT override the infrastructure's resume/abandon decision.

## FAILURE RECOVERY

When a dispatch call returns, evaluate the envelope and payload:

- Condition 1: envelope success=false → dispatch FAILED
- Condition 2: payload is null → dispatch FAILED (session crashed)
- Condition 3: payload .success=false → dispatch FAILED

On FAILURE:
- If continue_on_failure={campaign_recipe.continue_on_failure} is true: mark dispatch failed,
  emit the %%FLEET_PROGRESS%% marker with state=failure, proceed to next dispatch.
- If continue_on_failure={campaign_recipe.continue_on_failure} is false: halt campaign
  immediately (proceed to INTERRUPT/CLEANUP).

NEVER retry the same dispatch_name on non-quota failures in v1.

## QUOTA RETRY

Trigger: a dispatch returns reason=quota_exhausted OR
  reason=fleet_quota_exhausted with a wait_seconds field.

Action:
1. Sleep min(wait_seconds, {max_quota_wait_sec}) seconds.
2. Retry that exact dispatch ONCE.
3. If the retry still fails: halt campaign (proceed to INTERRUPT/CLEANUP).

This is the ONLY condition where re-dispatching the same dispatch_name is permitted.
{resume_section}{resumable_section}
## INTERRUPT/CLEANUP SEQUENCE

On campaign completion (all dispatches done) OR halt (failure or quota exhaustion):

1. Call {mcp_prefix}batch_cleanup_clones() to clean up all clone artifacts.
2. Emit the campaign summary block (see CAMPAIGN SUMMARY CONTRACT below).
3. End the session — no additional tool calls after the summary.

## CAMPAIGN SUMMARY CONTRACT v1

Emit this EXACT block as your final output. No other text after the block.

---campaign-summary::{campaign_id}---
{{
  "schema_version": 1,
  "campaign_id": "{campaign_id}",
  "campaign_name": "{campaign_recipe.name}",
  "dispatch_count": <total dispatches>,
  "completed_count": <successful dispatches>,
  "failure_count": <failed dispatches>,
  "skipped_count": <skipped dispatches>,
  "per_dispatch": [
    {{
      "name": "<dispatch_name>",
      "status": "<success|failure|skipped>",
      "elapsed_seconds": <float>,
      "token_usage": {{
        "input": <int>,
        "output": <int>,
        "cache_read": <int>,
        "cache_creation": <int>
      }},
      "dispatched_session_id": "<session_id>"
    }}
  ],
  "error_records": [
    {{
      "dispatch_name": "<name>",
      "code": "<fleet_error_code>",
      "message": "<human_readable_error>",
      "dispatched_session_id": "<session_id>"
    }}
  ]
}}
---end-campaign-summary::{campaign_id}---

Fields:
- schema_version: always 1
- dispatch_count / completed_count / failure_count / skipped_count: integer tallies
- per_dispatch: one entry per dispatch, in execution order;
  status is one of success, failure, skipped
- error_records: one entry per failed dispatch; empty list if no failures
- NO aggregate token fields (no total_input_tokens, no total_output_tokens, no total_duration)

## PROGRESS MARKERS

Emit at each dispatch state transition:

%%FLEET_PROGRESS::{campaign_id}::dispatch_<i>_of_<n>::<dispatch_id>::<state>%%

- <i>: 1-indexed dispatch position
- <n>: total dispatch count ({dispatch_count})
- <dispatch_id>: per-dispatch UUID assigned before calling dispatch_food_truck
- <state>: one of queued, running, success, failure, skipped
"""
