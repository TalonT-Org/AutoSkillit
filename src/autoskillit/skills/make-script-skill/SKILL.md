---
name: make-script-skill
description: Generate YAML pipeline scripts for .autoskillit/scripts/. Use when user says "make script skill", "generate script", "script a workflow", or when loaded by other skills for script formatting.
---

# Make Script Skill

Format a workflow into a YAML pipeline script following the workflow schema.

## When to Use

- **Standalone**: User wants to create a new pipeline script from scratch
- **Loaded by another skill**: Another skill (e.g., setup-project) loads this via the Skill tool to format a workflow it has already discovered

## Arguments (standalone mode)

```
/autoskillit:make-script-skill
```

No positional arguments. The skill prompts interactively for workflow details.

## The Script Format

Every generated script MUST follow the workflow YAML schema:

```yaml
name: {script-name}
description: {One line description.}
summary: {Concise pipeline chain, e.g. "plan > verify > implement > test > merge"}

inputs:
  var_name:
    description: {What this input is for}
    required: true          # or false
    default: {value}        # optional

steps:
  step_name:
    tool: {mcp_tool_name}
    with:
      arg1: "${{ inputs.var_name }}"
      arg2: "literal value"
    on_success: next_step
    on_failure: escalate
    retry:                  # optional
      max_attempts: 3
      on: needs_retry
      on_exhausted: escalate
  done:
    action: stop
    message: "Pipeline complete."
  escalate:
    action: stop
    message: "Failed — human intervention needed."
```

## Format Rules

- **Top-level fields**: `name`, `description`, `summary` (required), `inputs`, `steps`
- **Inputs**: each with `description`, optional `required` (default false) and `default`
- **Steps**: each has either `tool` (MCP tool call) or `action` (terminal: `stop`)
- **Tool steps**: use `with:` for arguments, `on_success`/`on_failure` for routing
- **Terminal steps**: have `action: stop` and a `message:`
- **Routing targets**: must reference other step names defined in the same file
- **Variable substitution**: use `${{ inputs.var_name }}` in `with:` values
- **Retry blocks**: optional, specify `max_attempts`, `on` (condition field), `on_exhausted` (step name)
- **Summary**: one line, use `>` to chain steps (e.g., "plan > verify > implement > test > merge")

## Example: Standard Implementation Pipeline

This is the reference format. All generated scripts should match this style:

```yaml
name: implementation
description: Plan, verify, implement, test, and merge a task.
summary: make-plan > dry-walk > implement > test > merge

inputs:
  task:
    description: What to implement
    required: true
  project_dir:
    description: Path to the project
    required: true
  work_dir:
    description: Working directory (can be same as project_dir)
    default: "."
  base_branch:
    description: Branch to merge into
    default: main

steps:
  plan:
    tool: run_skill
    with:
      skill_command: "/autoskillit:make-plan ${{ inputs.task }}"
      cwd: "${{ inputs.work_dir }}"
      add_dir: "${{ inputs.project_dir }}"
    on_success: verify
    on_failure: escalate
  verify:
    tool: run_skill
    with:
      skill_command: "/autoskillit:dry-walkthrough ${{ inputs.plan_path }}"
      cwd: "${{ inputs.work_dir }}"
    on_success: implement
    on_failure: escalate
  implement:
    tool: run_skill_retry
    with:
      skill_command: "/autoskillit:implement-worktree-no-merge ${{ inputs.plan_path }}"
      cwd: "${{ inputs.work_dir }}"
    on_success: test
    on_failure: escalate
    retry:
      max_attempts: 3
      on: needs_retry
      on_exhausted: escalate
  test:
    tool: test_check
    with:
      worktree_path: "${{ inputs.worktree_path }}"
    on_success: merge
    on_failure: fix
  merge:
    tool: merge_worktree
    with:
      worktree_path: "${{ inputs.worktree_path }}"
      base_branch: "${{ inputs.base_branch }}"
    on_success: done
    on_failure: escalate
  fix:
    tool: run_skill
    with:
      skill_command: "/autoskillit:assess-and-merge ${{ inputs.worktree_path }} ${{ inputs.plan_path }} ${{ inputs.base_branch }}"
      cwd: "${{ inputs.work_dir }}"
    on_success: done
    on_failure: escalate
    retry:
      max_attempts: 3
      on: needs_retry
      on_exhausted: escalate
  done:
    action: stop
    message: "Implementation complete."
  escalate:
    action: stop
    message: "Failed — human intervention needed."
```

## Standalone Invocation Flow

When called directly as `/autoskillit:make-script-skill`:

1. Ask the user what workflow they want to script (name, what it does)
2. Ask whether it's a linear pipeline or a loop with a fix step
3. Ask for the tool calls and routing (which MCP tools, what order, what conditions)
4. Ask for inputs (what's configurable)
5. Generate the script in the YAML format above
6. Save to `.autoskillit/scripts/{name}.yaml` (create the directory if needed)
7. Tell the user: "Saved to `.autoskillit/scripts/{name}.yaml`. Load it with `load_skill_script("{name}")` via the MCP tool."

## CRITICAL: Scripts Are NOT Skills

Pipeline scripts are YAML workflow files in `.autoskillit/scripts/`. They are:
- **Loaded** via the `load_skill_script` MCP tool
- **Executed** by the agent interpreting the YAML steps

They are NOT:
- Slash commands (cannot be invoked as `/autoskillit:<name>`)
- Stored in `.autoskillit/skills/` or any other directory
- Markdown files (they are `.yaml` files)

Never tell the user to run a script with `/autoskillit:<name>`. The correct
invocation is always via `load_skill_script("<name>")`.

## Loaded by Another Skill

When loaded via the Skill tool by another skill (e.g., setup-project), the calling agent already has all the workflow context in its conversation. Use that context directly:

- Workflow name and description are already known
- Tool calls and routing are already determined
- Inputs are already identified

Apply the format rules above to produce the YAML script. Do not re-ask for information the calling agent has already gathered.
