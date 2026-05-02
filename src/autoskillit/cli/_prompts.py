"""Orchestrator system prompt builder for the cook command."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from autoskillit.core import PIPELINE_FORBIDDEN_TOOLS, get_logger, pkg_root
from autoskillit.hooks import QUOTA_GUARD_DENY_TRIGGER, QUOTA_POST_WARNING_TRIGGER

logger = get_logger(__name__)

if TYPE_CHECKING:
    from autoskillit.recipe.loader import RecipeInfo
    from autoskillit.recipe.schema import Recipe


# Sentinel returned by _resolve_recipe_input when the user selects option 0.
_OPEN_KITCHEN_CHOICE: str = "__open_kitchen__"

# Shared retry instruction for both orchestrator and open-kitchen prompts.
_MCP_RETRY_INSTRUCTION: str = (
    "If calling open_kitchen produces ANY error — including"
    ' "No such tool available", a tool_use_error, or any is_error response —'
    " retry the call once immediately. Do not explain the error or suggest"
    " troubleshooting steps to the user. Just retry silently.\n"
    'If the retry also fails, output "AutoSkillit MCP server did not start — ending session."'
    " and end."
)


def _read_full_sous_chef() -> str:
    """Read the full sous-chef SKILL.md for injection into L1/L3 orchestration sessions."""
    path = pkg_root() / "skills" / "sous-chef" / "SKILL.md"
    try:
        return path.read_text()
    except OSError:
        return ""


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
   calls queue when the semaphore is saturated. **This overrides the general
   sequential discipline — parallel groups are an explicit exception.**
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


def _build_fleet_campaign_prompt(
    campaign_recipe: Recipe,
    manifest_yaml: str,
    completed_dispatches: str,
    mcp_prefix: str,
    campaign_id: str,
    max_quota_wait_sec: int = 3600,
    resumable_dispatch_name: str = "",
) -> str:
    """Build the system prompt for an L3 campaign dispatcher headless session.

    Assembles a 10-section prompt that instructs a headless Claude session to
    sequentially dispatch food trucks (L2 sessions), handle failures, respect
    quota, resume from prior state, and emit structured campaign-summary and
    progress markers.
    """
    dispatch_count = len(campaign_recipe.dispatches)
    sous_chef_content = _read_full_sous_chef()
    sous_chef_section = (
        f"\n## SOUS-CHEF DISCIPLINE\n\n{sous_chef_content}\n" if sous_chef_content else ""
    )

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
- Set `l2_session_id` to `""` (no L2 session was spawned).
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
        resumable_section = f"""\

## RESUMABLE DISPATCH: {resumable_dispatch_name}

This dispatch was interrupted mid-run with partial sidecar progress.
Re-dispatch it using compute_remaining_issues(dispatch_id, original_urls, project_dir)
to retrieve only the remaining issue URLs, then call dispatch_food_truck with
issue_urls=<remaining> and allow_reentry=true as ingredient overrides.
Do NOT re-dispatch from the full original issue list.
"""

    return f"""\
You are a fleet campaign dispatcher. Execute campaign '{campaign_recipe.name}' autonomously.
Campaign ID: {campaign_id}. Dispatches: {dispatch_count}.
{sous_chef_section}
## CAMPAIGN OVERVIEW

- Name: {campaign_recipe.name}
- Campaign ID: {campaign_id}
- Description: {campaign_recipe.description}
- Dispatch count: {dispatch_count} dispatches
- Continue on failure: {campaign_recipe.continue_on_failure}

## DISPATCH MANIFEST

The following manifest defines all dispatches for this campaign:

```yaml
{manifest_yaml}
```

## CAMPAIGN DISCIPLINE

Execute static manifest dispatches SEQUENTIALLY via {mcp_prefix}dispatch_food_truck.
The fleet_lock semaphore uses max_concurrent=1 for static dispatches — do NOT issue
static manifest calls in parallel. For dynamic dispatches (see the dynamic dispatch
instructions section when present), `parallel: true` groups override this rule.

Each dispatch is an independent L2 session with its own kitchen context. There is NO
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
is started — the L2 agent always receives concrete values.
{gate_section}{dynamic_dispatch_section}
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

Trigger: a dispatch returns reason=quota_exhausted with a wait_seconds field.

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
      "l2_session_id": "<session_id>"
    }}
  ],
  "error_records": [
    {{
      "dispatch_name": "<name>",
      "code": "<fleet_error_code>",
      "message": "<human_readable_error>",
      "l2_session_id": "<session_id>"
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


def _resolve_recipe_input(raw: str, available: list[RecipeInfo]) -> RecipeInfo | str | None:
    """Resolve picker raw text to a selection.

    Returns:
        _OPEN_KITCHEN_CHOICE  if raw is "0" (open kitchen, always valid)
        RecipeInfo            if raw is a valid 1-based index or an exact name match
        None                  for empty input, out-of-range numbers, or unknown names
    """
    if not raw:
        return None
    if raw.isdigit():
        n = int(raw)
        if n == 0:
            return _OPEN_KITCHEN_CHOICE
        if 1 <= n <= len(available):
            return available[n - 1]
        return None
    return next((r for r in available if r.name == raw), None)


def _get_ingredients_table(
    recipe_name: str, recipe_info: RecipeInfo | None, cwd: Path
) -> str | None:
    """Pre-render the ingredients table for system prompt injection.

    Uses load_and_validate (not load_recipe) so sub-recipe composition is included.
    Returns None on any error so the orchestrator prompt is built without the
    ingredients table rather than crashing.
    """
    from autoskillit.config import resolve_ingredient_defaults
    from autoskillit.recipe import load_and_validate

    try:
        return load_and_validate(
            recipe_name,
            project_dir=cwd,
            recipe_info=recipe_info,
            resolved_defaults=resolve_ingredient_defaults(cwd),
        ).get("ingredients_table")
    except Exception:
        logger.warning(
            "Failed to pre-render ingredients table for %r — proceeding without it",
            recipe_name,
        )
        return None


_COOK_GREETINGS: list[str] = [
    (
        "Welcome to Good Burger, home of the Good Burger, "
        "can I take your order? Today's special: {recipe_name}."
    ),
    "Order up! Today's special: {recipe_name}. What ingredients are we working with?",
    "Table for one! Today's special: {recipe_name}. Ready when you are.",
    "Fresh off the menu — today's special: {recipe_name}. What can I get started for you?",
]

_OPEN_KITCHEN_GREETINGS: list[str] = [
    "Welcome to Good Burger, home of the Good Burger, can I take your order?",
    "Kitchen's open! What are we cooking today?",
    "Order up! The kitchen is ready. What can I get you?",
]


def _build_orchestrator_prompt(
    recipe_name: str,
    mcp_prefix: str,
    ingredients_table: str | None = None,
) -> str:
    """Build the --append-system-prompt content for a cook session.

    The prompt contains behavioral instructions (routing rules, failure
    predicates, orchestrator discipline) and a greeting pool. Recipe content
    is discovered by the session via ``load_recipe``.
    """
    # Inject sous-chef global orchestration rules (graceful degradation if absent)
    raw = _read_full_sous_chef()
    sous_chef_content = "\n\n" + raw if raw else ""

    _ing_section = ""
    if ingredients_table:
        _ing_section = (
            "RECIPE INGREDIENTS — USE THESE EXACT NAMES:\n"
            f"{ingredients_table}\n\n"
            "The ingredient names above are authoritative. Use them verbatim when:\n"
            "- Collecting values from the user\n"
            "- Evaluating skip_when_false conditions\n"
            "- Passing ingredients to pipeline steps via `with:` arguments\n\n"
        )

    return f"""\
You are a pipeline orchestrator. Execute the recipe '{recipe_name}' step-by-step.

{_ing_section}FIRST ACTION — before prompting for any inputs:
1. Call {mcp_prefix}open_kitchen(name='{recipe_name}') to activate pipeline tools and open
   the kitchen gate. open_kitchen is REQUIRED to enable all gated AutoSkillit tools —
   the ingredients table above (when present) is provided for reference only.
   DO NOT call AskUserQuestion or any other tool before open_kitchen.
   {_MCP_RETRY_INSTRUCTION.replace(chr(10), chr(10) + "   ")}
2. The response contains a pre-formatted ingredients table
   between --- INGREDIENTS TABLE --- and --- END TABLE --- markers.
   Display it verbatim in your response — do not reformat or re-render it.
   Then ask for the required fields (marked with *). If the recipe has both
   a task and an issue_url ingredient, mention that a GitHub issue URL can
   be provided as the task. Keep it to one or two short sentences.
3. Collect ingredient values conversationally from the user's response.
4. Execute the pipeline steps.

During pipeline execution, only use AutoSkillit MCP tools:
- Read, Grep, Glob (code investigation) — not used here because investigation
  happens inside headless sessions launched by run_skill, which has full tool access.
- Edit, Write (code modification) — not used here because all code changes
  are delegated through run_skill.
- Bash (shell commands) — not used here; use run_cmd if shell access is needed.
- Agent subagents, WebFetch, WebSearch — not used here; delegate via
  run_skill for any research or multi-step work.

Allowed during pipeline execution:
- AutoSkillit MCP tools (call directly, not via subagents)
- AskUserQuestion (user interaction)
- Steps with `capture:` fields extract values from tool results into a
  pipeline context dict. Use captured values in subsequent steps via
  ${{{{ context.var_name }}}} in `with:` arguments.
- Thread outputs from each step into the next (e.g. worktree_path from
  implement into test_check).

ROUTING RULES — MANDATORY:
- When a tool returns a failure result, you MUST follow the step's on_failure route.
- When a step fails, route to on_failure — the downstream skill has diagnostic
  access that the orchestrator does not.
- Your ONLY job is to route to the correct next step and pass the
  required arguments. The downstream skill does the actual work.

FAILURE PREDICATES — when to follow on_failure:
- test_check: "passed: False" in output
- merge_worktree: "error:" line present in output
- run_cmd: "success: False" in output
- run_skill: "success: False" in output
- classify_fix: "error:" line present in output

FAILURE PREDICATE — open_kitchen:
  If the open_kitchen response contains `"success": false` OR does not
  contain the substring `--- INGREDIENTS TABLE ---`:
    1. Extract and print the value of "user_visible_message" from the
       JSON response verbatim (fall back to the raw response text if
       parsing fails).
    2. DO NOT call AskUserQuestion.
    3. End the session with a final text response.

CONTEXT LIMIT ROUTING — run_skill only (check BEFORE on_failure):
- When run_skill returns "success: False" AND "needs_retry: true" AND "retry_reason: resume":
  - Check "subtype" to discriminate the termination cause:
    - If subtype=stale: a transient hung process was killed by the watchdog. Retry
      the step (decrement the retries counter). Do NOT follow on_context_limit.
      If retries are exhausted, follow on_exhausted.
    - If subtype≠stale (e.g. context_exhaustion, error_max_turns): follow on_context_limit
      if defined, fall through to on_failure otherwise. This is the default resume path.
  - NEVER route retry_reason=resume with subtype=stale to on_context_limit.
- When run_skill returns "needs_retry: true" AND "retry_reason: drain_race":
  - The infrastructure confirmed session completion (Channel A or B) but stdout was not
    fully flushed before the process was killed. Partial progress was confirmed by the
    channel signal. Route identically to "resume": follow on_context_limit if defined,
    fall through to on_failure otherwise.
  - NEVER route retry_reason=drain_race to on_failure when on_context_limit exists.
- When run_skill returns "needs_retry: true" AND "retry_reason: empty_output":
  - The session exited cleanly but produced no output (transient API issue or infrastructure
    failure). No partial progress exists on disk. Do NOT route to on_context_limit.
  - Fall through to on_failure regardless of whether on_context_limit is defined.
- When run_skill returns "needs_retry: true" AND "retry_reason: path_contamination":
  - The session wrote files outside its working directory. This is a CWD boundary violation,
    not a context limit. No partial worktree progress should be resumed.
  - Fall through to on_failure regardless of whether on_context_limit is defined.
- When run_skill returns "needs_retry: true" AND "retry_reason: early_stop" or "zero_writes":
  - These are not context limit conditions. Fall through to on_failure.

HOOK DENIAL COMPLIANCE — ALL HOOKS:
- When a PreToolUse hook DENIES a tool call (permissionDecision: "deny"), the denial
  is a MANDATORY directive, not a suggestion. You MUST comply immediately.
- Read the permissionDecisionReason carefully — it contains the required corrective action.
- NEVER retry the denied tool call without first completing the corrective action.
- NEVER ignore, work around, or reason past a hook denial.
- Hook denials are structural enforcement of recipe/pipeline contracts. Treating them
  as optional undermines the pipeline's safety guarantees.
- After completing the corrective action specified in the deny reason, you may retry
  the original tool call.

SPECIFIC HOOK DENIAL PATTERNS:
- "QUOTA WAIT REQUIRED": Temporary — sleep and retry (see QUOTA DENIAL ROUTING below).
- "REVIEW LOOP REQUIRED": Call check_review_loop before retrying wait_for_ci/enqueue_pr.
- All other denials: Follow the corrective instruction in the deny reason text.

QUOTA DENIAL ROUTING — run_skill only (check BEFORE on_failure):
- When a PreToolUse hook DENIES run_skill with "{QUOTA_GUARD_DENY_TRIGGER}":
  - This is a TEMPORARY block. The API quota resets on a rolling window.
  - The deny message contains a run_cmd sleep command. Execute it immediately.
  - After the sleep completes, retry the EXACT same run_skill call (same arguments).
  - NEVER treat a quota denial as a permanent failure or pipeline-stopping error.
  - NEVER report quota denials to the user — handle them silently and automatically.
  - Before executing the sleep, state aloud: "Quota denied. Sleeping N seconds, then retrying."
- When run_skill output contains "{QUOTA_POST_WARNING_TRIGGER}":
  - A post-execution quota check detected high utilization.
  - The warning contains a run_cmd sleep command. Execute it BEFORE the next run_skill call.
  - After sleeping, proceed normally with the next pipeline step.

TWO FAILURE TIERS FOR PREDICATE-FORMAT STEPS:
- Tool-level failure (run_skill returns "success: False"): Follow on_failure. This fires
  BEFORE any result object exists. on_result conditions are NOT evaluated.
- Skill-level error ("error:" line present in result): Follow the matching on_result
  condition. This fires only when run_skill completes and returns a result with an error line.
- When a step has no on_failure declared and the tool returns "success: False", this is a
  recipe authoring error. Stop the pipeline and report the missing route.

OPTIONAL STEP SEMANTICS:
- optional: true means the step is SKIPPED (treated as bypassed) when its
  skip_when_false ingredient is false. It does NOT mean failures are tolerated.
- When skip_when_false evaluates to true (or is absent), the step is MANDATORY
  and MUST execute. The ONLY reason to skip an optional step is skip_when_false
  being false — no other reason is valid.
- A running optional step that returns success: false MUST follow on_failure.
  Never route a running optional step's failure to done.

STEP EXECUTION IS NOT DISCRETIONARY:
- You MUST execute every step the pipeline routes you to.
- NEVER skip a step because the PR is small, the diff is trivial, the change
  looks simple, or you judge the step unnecessary.
- The ONLY mechanism for skipping a step is skip_when_false evaluating to false.
  When skip_when_false evaluates to true (or is absent), the step is MANDATORY.
- Consequence: skipping PR review steps results in unreviewed code, missing diff
  annotations, and no architectural lens analysis — code reaches main without
  quality gates.

ACTION: CONFIRM STEP SEMANTICS:
- When you reach a step with action: "confirm", call AskUserQuestion with the
  step's message. Do NOT call any MCP tools for this step type — user interaction
  via AskUserQuestion IS the step.
- If the user confirms (answers yes, ok, proceed, delete, or similar affirmative),
  route to the step's on_success target.
- If the user declines (answers no, skip, keep, cancel, or similar negative),
  route to the step's on_failure target.

ACTION: STOP STEP SEMANTICS:
- When you reach a step with action: "stop", the pipeline is TERMINATED.
- Display the step's message to the user. Do NOT call any MCP tools.
- Do NOT attempt recovery, error reporting, or off-recipe actions after a stop step.
- Do NOT reason about what went wrong or try alternative approaches.
- A stop step is an INTENTIONAL terminus, not an error. Treat it as the recipe's
  final word — the recipe author designed this as the endpoint.

ACTION: ROUTE STEP SEMANTICS:
- When you reach a step with action: "route", evaluate the step's on_result
  conditions against captured context variables. Route to the matching target.
- Do NOT call any MCP tools for this step type — routing evaluation IS the step.
- If no on_result condition matches and on_failure is defined, follow on_failure.

SKILL_COMMAND FORMATTING — MANDATORY:
- The `skill_command` value in each step's `with:` block is a LITERAL template.
  Substitute ${{{{ context.var_name }}}} and ${{{{ inputs.var_name }}}} placeholders with
  their resolved values and pass the resulting string VERBATIM to run_skill.
- Do NOT add markdown headers, labels, notes, or any prose to skill_command.
  Do NOT restructure it as a labeled document or section list.
- skill_command arguments are POSITIONAL SPACE-SEPARATED TOKENS. A path argument
  is always a single path token — never a labeled section.
- If a step note says to pass an extra argument, append it as one space-separated
  token: `/autoskillit:skill /path/arg1 arg2`, NOT `/autoskillit:skill\n## Path\n/path`.

NULL/NONE CONTEXT VARIABLES — MANDATORY:
- When a ${{{{ context.var_name }}}} or ${{{{ inputs.var_name }}}} value is None, null,
  or has not been captured yet, you MUST either:
  (a) OMIT the parameter entirely from the tool call, OR
  (b) Pass null/None as the value.
- NEVER substitute a guessed, inferred, or plausible value for an uncaptured
  context variable. If ci_event is None, pass event=null — do not guess "push"
  or any other event name.
- The string "None" is NOT the same as null. If the captured value is the Python
  None object, do not pass the literal string "None".
{sous_chef_content}
"""


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
        "  quality gates." + sous_chef_content
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


def _build_fleet_dispatch_prompt(mcp_prefix: str) -> str:
    """Build the --append-system-prompt content for an ad-hoc fleet dispatcher session."""
    from autoskillit.fleet import _build_l2_sous_chef_block  # noqa: PLC0415

    sous_chef_block = _build_l2_sous_chef_block()
    sous_chef_section = (
        f"\n## SOUS-CHEF DISCIPLINE (DISPATCH SUBSET)\n\n{sous_chef_block}\n"
        if sous_chef_block
        else ""
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
{sous_chef_section}
## RECIPE DISCOVERY FLOW

1. Call {mcp_prefix}list_recipes to see available recipes.
2. Call {mcp_prefix}load_recipe with a recipe name to inspect its ingredients schema.
3. Call {mcp_prefix}fetch_github_issue (or {mcp_prefix}get_issue_title) to retrieve \
issue context when the task involves a GitHub issue.
4. Populate all required ingredient fields before dispatching.

## DISPATCH GUIDANCE

- `task` parameter: provide a clear, actionable one-line description of the work for each dispatch.
- `ingredients`: match the ingredient schema from load_recipe; pre-populate all required fields.
- Serial execution: dispatch one food truck at a time. fleet_lock enforces this — \
do NOT attempt parallel dispatches.

## DISPATCHER DISCIPLINE

You are a fleet dispatcher — NOT an executor. ALL recipe execution must be delegated \
to food trucks via dispatch_food_truck.
NEVER use run_skill or any non-fleet tool.

## CLEANUP / EXIT PROTOCOL

After all dispatches complete, call {mcp_prefix}batch_cleanup_clones() to clean up \
clone artifacts before ending the session.
"""


def show_cook_preview(
    recipe_name: str, parsed_recipe: object, recipes_dir: Path, project_dir: Path
) -> None:
    """Display the terminal preview: flow diagram + ingredients table.

    Owns the entire pre-launch display so ``cook()`` makes one call.
    Gateway imports only (no cross-package submodule imports).
    """
    from autoskillit.cli._ansi import diagram_to_terminal, ingredients_to_terminal
    from autoskillit.config import resolve_ingredient_defaults
    from autoskillit.recipe import build_ingredient_rows, load_recipe_diagram

    diagram = load_recipe_diagram(recipe_name, recipes_dir)
    if diagram:
        print(diagram_to_terminal(diagram))
        print()  # blank line between diagram and table

    resolved = resolve_ingredient_defaults(project_dir)
    rows = build_ingredient_rows(parsed_recipe, resolved_defaults=resolved)
    if rows:
        print(ingredients_to_terminal(rows))
