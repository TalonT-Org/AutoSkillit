"""Food truck prompt builder for L3 dispatch sessions.

Moved from autoskillit.cli._prompts — this module
depends only on autoskillit.core and stdlib, making it importable from both
the server and CLI layers without introducing cross-L3 coupling.
"""

from __future__ import annotations

import json
import re

from autoskillit.core import SOUS_CHEF_L3_SECTIONS, get_logger, pkg_root
from autoskillit.hooks import QUOTA_GUARD_DENY_TRIGGER, QUOTA_POST_WARNING_TRIGGER

logger = get_logger(__name__)


def _build_l3_sous_chef_block() -> str:
    """Extract the L3-relevant subset of sous-chef SKILL.md.

    Uses regex to split on ``## `` section headers and retains only sections
    whose title starts with one of the SOUS_CHEF_L3_SECTIONS prefixes.
    Returns empty string if SKILL.md is absent (graceful degradation).
    """
    path = pkg_root() / "skills" / "sous-chef" / "SKILL.md"
    if not path.exists():
        return ""
    try:
        content = path.read_text()
    except OSError:
        return ""

    # Split into sections on ## boundaries (keeping the delimiter via lookahead)
    sections = re.split(r"(?=^## )", content, flags=re.MULTILINE)

    retained: list[str] = []
    for section in sections:
        for title in SOUS_CHEF_L3_SECTIONS:
            if section.startswith(f"## {title}"):
                retained.append(section.rstrip())
                break

    return "\n\n".join(retained)


def _build_food_truck_prompt(
    recipe: str,
    task: str,
    ingredients: dict[str, str],
    mcp_prefix: str,
    dispatch_id: str,
    campaign_id: str,
    l3_timeout_sec: int,
) -> str:
    """Build the system prompt for an L3 food truck headless session.

    The prompt is self-contained — the L3 session needs no runtime reference
    material beyond what is embedded here. It assembles 8 sections:
    filtered sous-chef discipline, headless directives, routing/predicates,
    budget guidance, quota awareness, campaign task, ingredient values,
    and a sentinel-anchored result contract.
    """
    dispatch_id_short = dispatch_id[:8]
    ingredients_json = json.dumps(ingredients)
    ingredients_pretty_json = json.dumps(ingredients, indent=2)

    sous_chef_block = _build_l3_sous_chef_block()

    return f"""\
You are an L3 food truck orchestrator. Execute the recipe '{recipe}' autonomously.
Timeout: {l3_timeout_sec}s. Campaign: {campaign_id}. Dispatch: {dispatch_id}.

--- SECTION 1: SOUS-CHEF DISCIPLINE (L3 SUBSET) ---

{sous_chef_block}

--- SECTION 2: HEADLESS OPERATING MODE ---

H1 — FIRST ACTION (open_kitchen with overrides):
  Call {mcp_prefix}open_kitchen(name='{recipe}', overrides={ingredients_json})
  to activate pipeline tools. Ingredient values are pre-applied via overrides.
  DO NOT call Bash, ToolSearch, or any other tool before open_kitchen.

H2 — NO INGREDIENT PROMPTING:
  All ingredient values have been applied via open_kitchen overrides.
  DO NOT prompt for ingredient values. DO NOT call AskUserQuestion to collect inputs.
  Proceed directly to pipeline execution after open_kitchen succeeds.

H3 — AUTO-ACCEPT CONFIRM STEPS:
  When you reach a step with action: "confirm", treat it as automatically confirmed.
  Route to the step's on_success target without calling AskUserQuestion.
  Headless sessions have no human operator to prompt.

H3b — STOP STEP SEMANTICS:
  When you reach a step with action: "stop", the pipeline is TERMINATED.
  Display the step's message in the sentinel block. Do NOT call any MCP tools.
  Do NOT attempt recovery, error reporting, or off-recipe actions after a stop step.
  A stop step is an INTENTIONAL terminus — the recipe author designed this as the endpoint.

H3c — ROUTE STEP SEMANTICS:
  When you reach a step with action: "route", evaluate the step's on_result
  conditions against captured context variables. Route to the matching target.
  Do NOT call any MCP tools for this step type — routing evaluation IS the step.
  If no on_result condition matches and on_failure is defined, follow on_failure.

H4 — AskUserQuestion PERMITTED (AUTO-ACCEPTS):
  AskUserQuestion calls are permitted in this session. In the headless environment,
  they auto-accept with the first option. Use them only when a recipe step requires
  explicit user interaction routing — the auto-accept behavior handles it.

H5 — SHELL ACCESS VIA run_cmd ONLY:
  All shell commands must go through run_cmd. Do NOT use Bash directly.
  run_cmd provides audit logging, timeout enforcement, and quota integration.

H6 — SENTINEL-ONLY OUTPUT:
  Do NOT produce conversational prose, status summaries, or progress narration.
  Your ONLY output is the final sentinel block (see SECTION 8).
  Every non-final turn MUST invoke at least one tool.

--- SECTION 3: CORE ROUTING RULES + FAILURE PREDICATES ---

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
    1. Emit the sentinel block with success=false and reason="open_kitchen_failed".
    2. End the session.

TWO FAILURE TIERS FOR PREDICATE-FORMAT STEPS:
- Tool-level failure (run_skill returns "success: False"): Follow on_failure. This fires
  BEFORE any result object exists. on_result conditions are NOT evaluated.
- Skill-level error ("error:" line present in result): Follow the matching on_result
  condition. This fires only when run_skill completes and returns a result with an error line.
- When a step has no on_failure declared and the tool returns "success: False", this is a
  recipe authoring error. Emit the sentinel block with success=false
  and reason="missing_on_failure".

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

HOOK DENIAL COMPLIANCE — ALL HOOKS:
- When a PreToolUse hook DENIES a tool call (permissionDecision: "deny"), the denial
  is a MANDATORY directive, not a suggestion. You MUST comply immediately.
- Read the permissionDecisionReason carefully — it contains the required corrective action.
- NEVER retry the denied tool call without first completing the corrective action.
- NEVER ignore, work around, or reason past a hook denial.
- Hook denials are structural enforcement of recipe/pipeline contracts.
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
- When run_skill output contains "{QUOTA_POST_WARNING_TRIGGER}":
  - A post-execution quota check detected high utilization.
  - The warning contains a run_cmd sleep command. Execute it BEFORE the next run_skill call.
  - After sleeping, proceed normally with the next pipeline step.

SKILL_COMMAND FORMATTING — MANDATORY:
- The `skill_command` value in each step's `with:` block is a LITERAL template.
  Substitute ${{{{ context.var_name }}}} and ${{{{ inputs.var_name }}}} placeholders with
  their resolved values and pass the resulting string VERBATIM to run_skill.
- Do NOT add markdown headers, labels, notes, or any prose to skill_command.
- skill_command arguments are POSITIONAL SPACE-SEPARATED TOKENS.

--- SECTION 4: PER-SKILL BUDGET GUIDANCE ---

Skill invocations have time budgets based on complexity:
- Simple skills (single tool, no subagent): 120 seconds
- Single-subagent skills (one headless session): 900 seconds
- Multi-subagent skills (parallel or sequential sessions): 1800 seconds

When a skill approaches its budget, prefer completing partial work and
emitting a result over running to timeout. Budget overruns degrade
campaign throughput.

--- SECTION 5: QUOTA AWARENESS ---

When you detect that the API quota is exhausted (via quota guard denial
or quota post-warning), and you cannot make further progress:

Emit the sentinel block with:
  "success": false,
  "reason": "quota_exhausted",
  "wait_seconds": <seconds_until_reset>

The fleet dispatcher will schedule a retry after the wait period.
Do NOT loop indefinitely on quota denials — if 3 consecutive quota
denials occur with no successful run_skill between them, emit the
quota_exhausted sentinel and exit.

--- SECTION 6: CAMPAIGN TASK ---

Recipe: {recipe}
Task: {task}
Campaign ID: {campaign_id}
Dispatch ID: {dispatch_id}
Timeout: {l3_timeout_sec} seconds

Execute the recipe pipeline for the task above. Follow all routing
rules and failure predicates. Emit the sentinel block upon completion.

--- SECTION 7: INGREDIENT VALUES ---

The following ingredient values have been applied via open_kitchen overrides.
They are provided here for reference only — do NOT re-apply or re-prompt.

```json
{ingredients_pretty_json}
```

--- SECTION 8: FINAL OUTPUT CONTRACT ---

When the pipeline completes (success or failure), emit this EXACT sentinel block
as your final output. No other text after the sentinel.

```
---l3-result::{dispatch_id}---
{{"success": <true|false>, "reason": "<completion_reason>", "summary": "<one_line_summary>"}}
---end-l3-result::{dispatch_id}---
%%L3_DONE::{dispatch_id_short}%%
```

Fields:
- success: true if all mandatory steps completed without unresolved failures
- reason: "completed", "failed", "quota_exhausted", "timeout",
  "open_kitchen_failed", "missing_on_failure"
- summary: One-line description of what happened

The sentinel markers ---l3-result::{dispatch_id}--- and ---end-l3-result::{dispatch_id}---
are parsed by the fleet dispatcher. The %%L3_DONE::{dispatch_id_short}%% marker
signals session completion to the process monitor.
"""
