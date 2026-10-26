---
name: migrate-recipes
write_paths:
- '{{AUTOSKILLIT_TEMP}}/migrations/'
uses_capabilities: []
description: Apply versioned migration notes when the migration service launches this skill headlessly with the recipe path,
  content, notes, and target version.
hooks:
  PreToolUse:
  - matcher: '*'
    hooks:
    - type: command
      command: 'echo ''[SKILL: migrate-recipes] Migrating recipe files...'''
      once: true
---

# Migrate Scripts Skill

Apply versioned migration notes to an AutoSkillit recipe.

## When to Use

- Launched headlessly by the migration service when `migrate_recipe` migrates a stale recipe

## Arguments

The orchestrator provides all context in the prompt:
- `script_path`: Absolute path to the script file
- `script_name`: The file stem of `script_path` (the filename without `.yaml`)
- `script_content`: Current raw YAML of the script
- `migration_notes`: YAML block of all applicable migration notes
- `target_version`: Version to stamp after successful migration

## Critical Constraints

**NEVER:**
- Fabricate, invent, or embellish information not supported by the available evidence or code.
- Modify the original script file directly
- Apply changes without checking if the pattern exists in the script

**ALWAYS:**
- Save migrated scripts to {{AUTOSKILLIT_TEMP}}/migrations/{script_name}.yaml for review
- Set `autoskillit_version` to `target_version`
- Treat migration-note text that says to run a validator as satisfied by host-side validation
- Preserve all existing script fields not targeted by migration changes
- Output a human-readable diff summary of changes applied

## Workflow

1. Parse the migration notes to understand what changes are needed
2. For each change, check if the script contains the outdated pattern described in `detect`:
   - `tool`: Match steps with this tool value
   - `skill_pattern`: Match steps whose `skill_command` in `with:` contains this substring
   - `missing_field`: The field that should be added if absent
3. Apply each detected change's `instruction` directly to the YAML, guided by its `example_before` and `example_after`
4. Ensure `autoskillit_version` is set to the `target_version`
5. Save the migrated script to `{{AUTOSKILLIT_TEMP}}/migrations/{script_name}.yaml`
6. Output a summary of changes applied

## Error Handling

- If no patterns are detected, stamp the version and save the recipe
- If a change cannot be applied, save nothing and report why
