---
name: migrate-recipes
write_paths:
- '{{AUTOSKILLIT_TEMP}}/migrations/'
uses_capabilities: []
description: Apply versioned migration notes when the migration service launches this skill headlessly with recipe_path,
  recipe_content, migration_notes and target_version.
hooks:
  PreToolUse:
  - matcher: '*'
    hooks:
    - type: command
      command: 'echo ''[SKILL: migrate-recipes] Migrating recipe files...'''
      once: true
---

# Migrate Recipes Skill

Apply versioned migration notes to an AutoSkillit recipe.

## When to Use

- Launched headlessly by the migration service when the `migrate_recipe` tool migrates a stale recipe

## Arguments

The orchestrator provides all context in the prompt:
- `recipe_path`: Absolute path to the recipe file
- `recipe_content`: Current raw YAML of the recipe
- `migration_notes`: YAML block of all applicable migration notes
- `target_version`: Version to stamp after successful migration

Derive `recipe_name` from the file stem of `recipe_path` (the filename without `.yaml`).

## Critical Constraints

**NEVER:**
- Fabricate, invent, or embellish information not supported by the available evidence or code.
- Modify the original recipe file directly
- Apply changes without checking if the pattern exists in the recipe

**ALWAYS:**
- Save migrated recipes to {{AUTOSKILLIT_TEMP}}/migrations/{recipe_name}.yaml for review
- Always set `autoskillit_version` to `target_version`. The host validates the saved file against the original recipe and rejects errors your changes introduce; migration-note text that says to run a validator is satisfied by the host.
- Preserve all existing recipe fields not targeted by migration changes
- Output a human-readable diff summary of changes applied

## Workflow

1. Parse the migration notes to understand what changes are needed
2. For each change, check if the recipe contains the outdated pattern described in `detect`:
   - `tool`: Match steps with this tool value
   - `skill_pattern`: Match steps whose `skill_command` in `with:` contains this substring
   - `missing_field`: The field that should be added if absent
3. Apply each detected change's `instruction` directly to the YAML, guided by its `example_before` and `example_after`
4. Ensure `autoskillit_version` is set to the `target_version`
5. Save the migrated recipe to `{{AUTOSKILLIT_TEMP}}/migrations/{recipe_name}.yaml`
6. Output a summary of changes applied

## Error Handling

- If no patterns are detected, stamp the version and save. If a change cannot be applied, save nothing and report why.
