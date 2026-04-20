---
name: make-campaign
description: Interactively author a campaign recipe YAML through a 6-phase guided workflow. Use when user says "make campaign", "create campaign", "author campaign", "new campaign recipe", or wants to decompose a campaign goal into dispatches.
categories:
  - orchestration-family
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo '[SKILL: make-campaign] Authoring campaign recipe...'"
          once: true
---

# Campaign Recipe Authoring Skill

Guides users through a 6-phase interactive workflow to decompose a campaign goal into a validated campaign recipe YAML.

## When to Use

- User says "make campaign", "create campaign", "author campaign", "new campaign recipe"
- User wants to decompose a campaign goal into dispatches
- User wants to orchestrate multiple recipe runs as a campaign
- User says "decompose into dispatches"

## Critical Constraints

**NEVER:**
- Modify or write to any source code files
- Create files outside `.autoskillit/recipes/campaigns/` (final output) or `{{AUTOSKILLIT_TEMP}}/make-campaign/` (temp/validation drafts)
- Write the final campaign YAML without first passing `validate_recipe` in Phase 5
- Use recipe names not confirmed by `find_recipe_by_name` or `list_recipes`
- Accept ingredient keys that do not exist in the target recipe's declared ingredients

**ALWAYS:**
- Use `find_recipe_by_name` to confirm dispatch targets exist before accepting a recipe name
- Use `load_recipe` to inspect each target recipe's `ingredients` schema before populating dispatch ingredients
- Validate the campaign YAML with `validate_recipe` before writing the final manifest
- Emit the structured output token `campaign_path` as the **absolute path** to the written campaign YAML:
  ```
  campaign_path = /absolute/path/to/.autoskillit/recipes/campaigns/<name>.yaml
  ```
  Use `$(pwd)` to resolve the absolute path when writing the output file.
- Run cycle detection on `depends_on` before proceeding to Phase 5

## Phase 1 — Goal Clarification

Prompt the user for the following information interactively:

1. **Campaign name** (kebab-case, e.g. `my-feature-campaign`)
2. **Description** (1–2 sentences describing the campaign's purpose)
3. **Target outcome** (what the campaign achieves when all dispatches succeed)
4. **Rough dispatch count** (how many dispatch phases the user anticipates)
5. **`continue_on_failure`** — should the campaign proceed if a dispatch fails? (default: `false`)
6. **`categories`** — which recipe family this campaign targets (default: `orchestration-family`)
7. **`requires_recipe_packs`** — which recipe pack(s) the dispatches will draw from (e.g. `implementation-family`)

Produce a Goal Artifact with: `name`, `description`, `categories`, `requires_recipe_packs`, `continue_on_failure`.

## Phase 2 — Dispatch Decomposition

For each dispatch (repeat until the user signals done):

1. Ask the user to describe the dispatch's task in one sentence.
2. Call `list_recipes` to show available recipes matching the declared packs.
3. Ask the user which recipe this dispatch targets.
4. Verify the recipe exists by calling `find_recipe_by_name` (or confirming from `list_recipes` results).
   - If not found: show available options and re-prompt.
   - If found: accept the recipe name.
5. Capture:
   - `name` — kebab-case dispatch name (e.g. `phase-1-implement`)
   - `recipe` — confirmed recipe name
   - `task` — non-empty task description
6. Optionally allow the user to add entries to `allowed_recipes` for dispatches that use recipes outside the declared packs.

Do not proceed to Phase 3 until all dispatch names, recipes, and tasks are confirmed.

## Phase 3 — Ingredient Population

For each dispatch captured in Phase 2:

1. Call `load_recipe` on the target recipe to inspect its `ingredients` schema.
2. Present the ingredient table: name, description, required, default.
3. For each ingredient the user wants to set:
   - Ask for the value (string) or a campaign-level pass-through using `${{ inputs.<name> }}` syntax.
   - Validate that the key exists in the target recipe's schema — reject unknown keys immediately.
4. Collect all key-value pairs as `ingredients: {key: "value"}` under the dispatch.

Proceed to Phase 4 only after all dispatches have their ingredients populated (even if empty).

## Phase 4 — Dependency Ordering

1. Present all dispatches in capture order with their names.
2. For each dispatch, ask: "Which earlier dispatches does this one depend on?" (comma-separated names, or none).
3. Build the `depends_on` adjacency map.
4. Run cycle detection (DFS):
   - If a cycle is found: report the full cycle path (e.g. `a → b → c → a`) and re-prompt affected dispatches.
   - Loop until the dependency graph is acyclic.
5. Suggest the topological sort order; ask the user to confirm or adjust dispatch ordering.

## Phase 5 — Schema Validation

1. Assemble the complete campaign YAML from all captured data (Phases 1–4).
2. Write a draft file (relative to the current working directory) to `{{AUTOSKILLIT_TEMP}}/make-campaign/<name>_draft.yaml`.
3. Call `validate_recipe` with the draft path.
4. If validation returns errors:
   - Display each error with its rule name and message.
   - Guide the user through the correction.
   - Update the draft and re-call `validate_recipe`.
   - Repeat until validation passes (structural + all 14 campaign semantic rules clean).
5. Proceed to Phase 6 only when `validate_recipe` reports no errors.

## Phase 6 — Manifest Write

1. Ensure the `.autoskillit/recipes/campaigns/` directory exists (create with `mkdir -p` if absent).
2. Write the validated YAML to `.autoskillit/recipes/campaigns/<name>.yaml`.
3. Print the absolute path of the written file.
4. Print the next-step hint:

   ```
   Campaign recipe written. Use the dispatch_food_truck MCP tool (with kitchen open)
   to execute this campaign.
   ```

## Output

Emit the structured output token on its own line:

```
campaign_path = {absolute_path_to_campaign_yaml}
```

Example:
```
campaign_path = /home/user/project/.autoskillit/recipes/campaigns/my-campaign.yaml
```
