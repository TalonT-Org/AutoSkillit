---
name: render-recipe
description: Render a recipe YAML as a compact visual overview with ASCII flow diagram and input table. Use when presenting a loaded recipe to the user.
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

The output has exactly three sections. Follow this structure precisely.

### Section 1: Header

```
## {name}
{description}
```

### Section 2: Flow Diagram

Build an ASCII flow diagram showing the step graph. This is the core visual.

**Infrastructure steps to hide:**
Steps whose sole purpose is capturing a value via `run_cmd` (e.g., `capture_base_sha`, `set_merge_target`, shell one-liners that just `printf` or `git rev-parse`) should be **omitted** from the diagram. They are plumbing — the user does not need to see them. If in doubt: if a step uses `run_cmd` and its `note:` describes it as capturing/setting a value, hide it.

**Diagram layout:**

Use a vertical spine with `├──` branches. The structure reads top-to-bottom:

1. **Main flow** runs down the left spine. Show meaningful steps in order.
2. **Optional steps** are shown in brackets `[step-name]` with a right-side annotation: `← only if {condition}`.
3. **Iteration loops** are wrapped in a `FOR EACH` block using box-drawing characters (`┌────┤` / `└────┘`). Only use this when `note:` fields indicate iteration over plan_parts or groups.
4. **Failure branches** are shown inline where they diverge: `fix ───┘ (on failure)`.
5. **Multi-way routing** (`on_result`) is shown with labeled paths on separate lines.
6. **Retry blocks** are noted parenthetically on the step: `(retry ×N)`. Use `×∞` for `max_attempts: 0`.
7. **Back-edges** use `↑` suffix: `→ verify↑`, `→ plan↑`.
8. **Terminal steps** go at the bottom after a separator line.
9. Do **not** show model annotations (e.g., `[sonnet]`) in the diagram.

**Reference example for implementation-pipeline:**

```
  clone
       │
       ├── [create_branch]  ← only if open_pr=true
       │
       ├── [make-groups]    ← only if make_groups=true
       │
  ┌────┤ FOR EACH GROUP:
  │    │
  │    plan ─── [review] ─── verify ─── implement ─── test ─── merge ─── push
  │         │                                           │
  │         │                                    fix ───┘ (on failure)
  │         │
  │         └── next_or_done: more parts? → verify↑
  │                           more groups? → plan↑
  │                           all done? ↓
  └────┘
       │
       ├── [audit-impl]    ← only if audit=true
       │     GO → open_pr / done
       │     NO GO → remediate → plan↑
       │
       ├── [open-pr]       ← only if open_pr=true
       │
       cleanup ─── done

  ─────────────────────────────────
  done       "Pipeline complete."
  escalate   "Failed — human intervention needed."
```

**Adapting to simpler recipes:**
- Recipes without iteration do not use `FOR EACH` blocks. Just show the linear flow with branches.
- Recipes with few optional steps can show them inline on the main flow: `clone ─── audit ─── investigate ─── plan ─── implement ─── test ─── merge ─── push`.
- Very simple recipes (under 8 meaningful steps) can use a single horizontal chain.
- Always adapt the shape to the recipe — do not force a complex layout on a simple recipe.

### Section 3: Inputs Table

Render all ingredients as a single Markdown table.

```
### Inputs

| Name | Description | Default |
|------|-------------|---------|
| task | What to implement | — |
| source_dir | Repository path | auto-detect |
| base_branch | Merge target | main |
| run_name | Run name prefix | impl |
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
- If an ingredient is conditionally required (e.g., `task` required when `make_groups=false`), note it parenthetically: "What to implement (when no groups)".
- Omit agent-managed state (ingredients with no default that are populated by step captures). If any exist, add a brief line below the table: `Agent-managed: work_dir, worktree_path, plan_path, ...`

---

## Workflow

### Step 1: Parse the Recipe

Read the recipe YAML from the prompt context or from disk if a path is given. Identify:
- All ingredients and their properties
- All steps in declaration order
- Infrastructure steps to hide (run_cmd steps that just capture values)
- The happy path (follow on_success chain from first step)
- Branch points (on_failure to non-terminal, on_result routing)
- Back-edges (on_success/on_failure targets that appear earlier in declaration order)
- Optional steps (optional: true)
- Retry blocks
- Terminal steps (action: stop)

### Step 2: Build the Flow Diagram

Construct the ASCII diagram following the rules in Section 2. Start with the happy path, omit infrastructure steps, then layer in branches and optional annotations.

### Step 3: Build the Inputs Table

Construct the table following the rules in Section 3.

### Step 4: Assemble and Write

Combine all three sections. Write to `temp/render-recipe/{recipe-name}.md`. Print the full content to terminal.

---

## Output Rules

| Content | Destination |
|---------|-------------|
| Rendered recipe overview | `temp/render-recipe/{recipe-name}.md` AND terminal |
| Validation warnings (if any) | Terminal only, as a brief note after the render |
