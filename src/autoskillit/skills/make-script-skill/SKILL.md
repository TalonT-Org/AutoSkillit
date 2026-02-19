---
name: make-script-skill
description: Generate clean, script-style SKILL.md files from workflow descriptions. Use when user says "make script skill", "generate script", "script a workflow", or when loaded by other skills for script formatting.
---

# Make Script Skill

Format a workflow into a concise, scannable skill script following the strict script format.

## When to Use

- **Standalone**: User wants to create a new script-style skill from scratch
- **Loaded by another skill**: Another skill (e.g., setup-project) loads this via the Skill tool to format a workflow it has already discovered

## Arguments (standalone mode)

```
/make-script-skill
```

No positional arguments. The skill prompts interactively for workflow details.

## The Script Format

Every generated script MUST follow this exact structure:

```markdown
---
name: {skill-name}
description: {One line}. Use when user says "{triggers}".
---

# {Title}

{One sentence description.}

SETUP:
  - var1 = {value or description}
  - var2 = {value or description}

{PIPELINE: | LOOP:}
0. Verify AutoSkillit tools are enabled. If not → tell user to run /mcp__autoskillit__enable_tools
0.1. Prompt user for SETUP variables (use AskUserQuestion)
1. tool_call(args) → routing
2. tool_call(args) → routing

{FIX: (only if LOOP)}
N. tool_call(args) → routing

ESCALATE: Stop and report what failed. Human intervention needed.

{Notes: (optional, max 3 bullets)}
```

## Format Rules

- **Frontmatter**: only `name` and `description`. No hooks.
- **Body**: one `#` title, one sentence description, then pure script blocks
- **Variables** in SETUP use `${var}` syntax for substitution in tool calls
- **Tool calls** are written as function calls: `tool_name(arg1, arg2)`
- **Routing** uses `→` arrows: `→ if error, go to FIX` or `→ PASS: next step`
- **Conditional branches** use indented bullets under the step
- **Step 0** is always the AutoSkillit tools check + SETUP variable prompting (auto-included)
- **No markdown headers** inside the script body (no `##`, `###`)
- **No prose paragraphs** explaining what each step does — the tool call IS the explanation
- **Notes section** (if present) is max 3 bullet points for non-obvious things only

## Anti-patterns — Do NOT Include in Generated Scripts

- "When to Use" sections
- "Critical Constraints" blocks
- "Output" sections describing what gets created
- Verbose step-by-step prose descriptions
- Headed subsections within steps (`### Step N: Title`)
- "Important Notes" or "Error Handling" blocks
- Paragraphs of explanation between tool calls
- Install instructions or "Getting Started" guidance

The tool call line is self-documenting. `run_skill("/make-plan ${task}", cwd=${work_dir})` needs no paragraph explaining that it creates a plan.

## Example: Standard Implementation Pipeline

This is the reference format. All generated scripts should match this style:

```markdown
---
name: implement-pipeline
description: Plan, verify, implement, test, and merge a task. Use when user says "run pipeline", "implement task", or "auto implement".
---

# Implementation Pipeline

Automated plan-to-merge pipeline for a single task.

SETUP:
  - project_dir = /path/to/project
  - work_dir = /path/to/workspace (or same as project_dir)
  - base_branch = main
  - task = "description of what to implement"

PIPELINE:
0. Verify AutoSkillit tools are enabled. If not → tell user to run /mcp__autoskillit__enable_tools
0.1. Prompt user for SETUP variables (use AskUserQuestion)
1. run_skill("/make-plan ${task}", cwd=${work_dir}, add_dir=${project_dir}) → save ${plan_path}
2. run_skill("/dry-walkthrough ${plan_path}", cwd=${work_dir})
3. run_skill_retry("/implement-worktree-no-merge ${plan_path}", cwd=${work_dir})
   - If context exhausted: run_skill_retry("/retry-worktree ${plan_path} ${worktree_path}", cwd=${work_dir})
     Repeat up to 3x, then → ESCALATE
4. test_check(${worktree_path})
   - PASS → merge_worktree(${worktree_path}, ${base_branch}). Done.
   - FAIL → run_skill("/assess-and-merge ${worktree_path} ${plan_path} ${base_branch}", cwd=${work_dir})
     Still failing after 3 attempts → ESCALATE

ESCALATE: Stop and report what failed. Human intervention needed.

Notes:
- If work_dir equals project_dir, omit add_dir from run_skill calls
- Monitor test_check output for flaky tests vs real failures
```

## Standalone Invocation Flow

When called directly as `/make-script-skill`:

1. Ask the user what workflow they want to script (name, what it does)
2. Ask whether it's a linear PIPELINE or a LOOP with a FIX step
3. Ask for the tool calls and routing (which MCP tools, what order, what conditions)
4. Ask for SETUP variables (what's configurable)
5. Generate the script in the format above
6. Ask where to save: suggest `.claude/skills/{name}/SKILL.md`
7. Write to disk after confirmation

## Loaded by Another Skill

When loaded via the Skill tool by another skill (e.g., setup-project), the calling agent already has all the workflow context in its conversation. Use that context directly:

- Workflow name and description are already known
- Tool calls and routing are already determined
- SETUP variables are already identified

Apply the format rules above to produce the script. Do not re-ask for information the calling agent has already gathered.
