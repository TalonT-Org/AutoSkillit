---
name: render-recipe
description: Render a recipe YAML as a compact visual overview with ASCII flow diagram, input table, and step summary. Use when presenting a loaded recipe to the user.
---

# Render Recipe

Produce a compact, structured overview of an AutoSkillit recipe. Reads the recipe YAML (provided in the prompt or loaded via `load_recipe`), analyzes the step graph, and writes a formatted Markdown file to `temp/render-recipe/`.

## When to Use

- After loading a recipe via `load_recipe`
- User says "render recipe", "show recipe", "recipe overview"
- Presenting a recipe to a user before collecting inputs

## Critical Constraints

**NEVER:**
- Modify any source code or recipe files
- Invent steps, ingredients, or routing not in the YAML
- Add decorative flair, emoji, or unnecessary commentary
- Create files outside `temp/render-recipe/`

**ALWAYS:**
- Read the recipe YAML carefully and render exactly what exists
- Write output to `temp/render-recipe/{recipe-name}.md`
- Print the rendered content to terminal after writing the file

---

## Rendering Specification

The output has exactly four sections. Follow this structure precisely.

### Section 1: Header

```
## {name}
{description}
```

### Section 2: Flow Diagram

Build an ASCII flow diagram showing the step graph. This is the core visual.

**Rules:**

1. Identify the **happy path** — the longest chain following `on_success` from the first step to a terminal `stop` step.
2. Show the happy path as a horizontal or vertical chain using `───` connectors.
3. Show **optional steps** in brackets: `[step-name]`.
4. Show **branch points** where `on_failure` diverges to a non-terminal step (e.g., `fix`). Use a vertical tap off the main line.
5. Show **loops** (back-edges to earlier steps) with a return arrow and label.
6. Show **on_result routing** (multi-way branches) with labeled paths.
7. Show **retry blocks** as a note on the step: `(retry ×N)`.
8. Group repeated cycles with `FOR EACH` notation when `note:` fields indicate iteration over plan_parts or groups.
9. Terminal steps go at the bottom, separated by a line.

**Connectors:**
- `───` horizontal flow
- `│` vertical flow
- `├──` branch off main line
- `└──` last branch
- `↑` back-edge (loop to earlier step)

**Example for implementation-pipeline:**

```
clone ─── capture_sha ─── set_target ─── [create_branch]
                                              │
                                         [make-groups]
                                              │
┌─────────────────────────────────────────────┤
│  FOR EACH group / plan part:                │
│                                             │
│  plan ─── [review] ─── verify ─── implement (retry) ─── test ─── merge ─── push
│                                                           │
│                                                    fix ───┘ (on fail)
│                                                                       │
│  next_or_done:  more parts → verify↑ │ more groups → plan↑ │ done ↓  │
└───────────────────────────────────────────────────────────────────────┘
                              │
                        [audit-impl]
                         GO ↓  NO GO → plan↑
                              │
                         [open-pr]
                              │
                          cleanup
                              │
  ─────────────────────────────────
  done       "Pipeline complete."
  escalate   "Failed — human intervention needed."
```

This is an example — adapt the shape to the actual recipe. Simpler recipes get simpler diagrams. Do not force the `FOR EACH` notation if the recipe has no iteration.

### Section 3: Inputs Table

Render all ingredients as a single Markdown table. Separate user-supplied inputs from agent-managed state.

```
### Inputs

| Name | Description | Default |
|------|-------------|---------|
| task | What to implement | — |
| source_dir | Repository path | auto-detect |
| base_branch | Merge target | main |
| run_name | Run name prefix | "impl" |
| make_groups | Decompose into groups | off |
| review_approach | Research first | off |
| audit | Post-merge audit | on |
| open_pr | PR instead of direct merge | off |
```

**Rules:**
- Keep descriptions short — one phrase, not a sentence.
- Show `—` for no default (required inputs).
- Show `off`/`on` for boolean-like flags with `"false"`/`"true"` defaults.
- Show `auto-detect` for empty-string defaults that auto-resolve.
- If an ingredient is conditionally required (e.g., `task` required when `make_groups=false`), note it parenthetically in the description: "What to implement (when no groups)".
- Omit agent-managed state (ingredients with no default that are populated by step captures). If any exist, add a brief line below the table: `Agent-managed: work_dir, worktree_path, plan_path, ...`

### Section 4: Steps Summary

List non-terminal steps in declaration order. One line per step.

```
### Steps (N total, M optional)

| # | Step | Tool | On Fail | Notes |
|---|------|------|---------|-------|
| 1 | clone | clone_repo | stop | captures work_dir |
| 2 | capture_sha | run_cmd | stop | captures base_sha |
| 3 | set_target | run_cmd | stop | sets merge_target default |
| 4 | create_branch | run_cmd | cleanup | [optional] only if open_pr=true |
| 5 | group | run_skill | cleanup | [optional] only if make_groups=true |
| 6 | plan | run_skill | cleanup | make-plan; captures plan_path |
| 7 | review | run_skill | cleanup | [optional] only if review_approach=true |
| 8 | verify | run_skill | cleanup | dry-walkthrough; per plan part |
| 9 | implement | run_skill_retry | cleanup | retry ×∞ → retry_worktree |
| 10 | retry_worktree | run_skill_retry | cleanup | retry ×3 → cleanup |
| 11 | test | test_check | fix | pipeline gate |
| 12 | merge | merge_worktree | cleanup | merges into merge_target |
| 13 | push | push_to_remote | cleanup | pushes merge_target to remote |
| 14 | fix | run_skill [sonnet] | cleanup | resolve-failures → test↑ |
| 15 | next_or_done | route | — | 3-way: parts/groups/done |
| 16 | audit_impl | run_skill | stop | [optional] GO/NO GO routing |
| 17 | remediate | route | — | → plan↑ with remediation file |
| 18 | open_pr_step | run_skill | cleanup | [optional] only if open_pr=true |
```

**Rules:**
- `#` is the ordinal position (1-indexed), not the YAML key.
- `Tool` column: show the tool/action value. Append `[model]` if a `model:` field is set.
- `On Fail` column: show the on_failure target. Use `stop` for terminal targets, `—` for route steps with no on_failure.
- `Notes` column: keep to one short phrase. Prioritize: [optional] flag, retry info, what it captures, skill name if it's a run_skill step.
- Omit terminal steps (action: stop) and cleanup steps from the table. List terminals below it:
  ```
  Terminals: done ("Pipeline complete."), escalate_stop ("Failed — human intervention needed.")
  ```

---

## Workflow

### Step 1: Parse the Recipe

Read the recipe YAML from the prompt context or from disk if a path is given. Identify:
- All ingredients and their properties
- All steps in declaration order
- The happy path (follow on_success chain from first step)
- Branch points (on_failure to non-terminal, on_result routing)
- Back-edges (on_success/on_failure targets that appear earlier in declaration order)
- Optional steps (optional: true)
- Retry blocks
- Terminal steps (action: stop)

### Step 2: Build the Flow Diagram

Construct the ASCII diagram following the rules in Section 2. Start with the happy path, then layer in branches, loops, and optional annotations.

### Step 3: Build the Tables

Construct the Inputs table and Steps table following the rules in Sections 3 and 4.

### Step 4: Assemble and Write

Combine all four sections. Write to `temp/render-recipe/{recipe-name}.md`. Print the full content to terminal.

---

## Output Rules

| Content | Destination |
|---------|-------------|
| Rendered recipe overview | `temp/render-recipe/{recipe-name}.md` AND terminal |
| Validation warnings (if any) | Terminal only, as a brief note after the render |
