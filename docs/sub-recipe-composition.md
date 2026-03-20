# Sub-Recipe Composition

## Overview

Recipes can declare **sub-recipe references** — optional workflow prefixes (or suffixes)
that are lazy-loaded based on a hidden ingredient gate. When the gate is inactive, the
sub-recipe step is dropped entirely from the loaded recipe. When the gate is active, the
sub-recipe's steps are merged inline at the attachment point.

## Declaring a Sub-Recipe Reference

In a recipe YAML, add a step with `sub_recipe:` and `gate:` fields:

```yaml
steps:
  sprint_entry:
    sub_recipe: sprint-prefix   # name of sub-recipe (no extension)
    gate: sprint_mode           # ingredient controlling activation
    on_success: done            # where to route after sub-recipe completes
    on_failure: escalate_stop
    note: >
      When gate=true, sprint-prefix steps are merged here.
      When gate=false (default), this step is dropped at load time.
```

The referenced ingredient must be declared as hidden so it is not shown to agents:

```yaml
ingredients:
  sprint_mode:
    description: Enable sprint sub-recipe prefix
    default: "false"
    hidden: true
```

## Lazy Loading

When the gate ingredient is `"false"` (default), the sub-recipe step is **dropped
entirely** from the loaded recipe. The agent sees only the standard steps, and the
recipe behaves identically to how it did before sub-recipe composition was introduced.

When the gate ingredient is `"true"`, the sub-recipe's steps are **merged inline** at
the attachment point, with step names prefixed to avoid collisions. The sub-recipe's
kitchen rules are merged with the parent's kitchen rules.

## Activating Sprint Mode

Pass `overrides` when loading the recipe via the `open_kitchen` MCP tool:

```
open_kitchen(name="implementation", overrides={"sprint_mode": "true"})
```

Or via the `load_recipe` MCP tool:

```
load_recipe(name="implementation", overrides={"sprint_mode": "true"})
```

## Sub-Recipe File Location

Sub-recipes live in `recipes/sub-recipes/` within the package. They are **not
user-visible** via `list_recipes` — they are internal implementation details.

Project-local sub-recipes can be placed in `.autoskillit/recipes/sub-recipes/`
and take precedence over built-in sub-recipes with the same name.

Their diagrams live in `recipes/sub-recipes/diagrams/{name}.md`.

## Hidden Ingredients

Hidden ingredients (`hidden: true`) are suppressed from the ingredients table shown
to agents at recipe load time. Agents will not see them unless the ingredient is
explicitly passed via `overrides`. This keeps the default runtime experience clean
while allowing advanced activation patterns.

## Example: Sprint Mode in the Implementation Recipe

The `implementation` recipe includes a `sprint_entry` step gated on `sprint_mode`:

- **Default** (`sprint_mode="false"`): `sprint_entry` is dropped; the recipe starts
  at `clone` and runs the standard single-issue pipeline.
- **Sprint mode** (`sprint_mode="true"`): `sprint-prefix` steps are merged as a prefix,
  running the full sprint workflow (triage → sprint planning → user confirmation →
  per-issue dispatch → sprint report).

The same pattern applies to the `remediation` recipe.

### How Sprint Mode Works

Sprint mode transforms a single-issue recipe into a batch processor:

1. **Triage** — `triage-issues` analyzes open GitHub issues and produces a sequenced plan
2. **Sprint Planning** — `sprint-planner` selects a conflict-free subset of issues for the sprint
3. **User Confirmation** — the orchestrator presents the sprint plan and waits for approval
4. **Dispatch** — for each issue in the sprint, the standard pipeline runs (plan → implement → test → merge → PR)
5. **Sprint Report** — a summary issue is created linking all PRs from the sprint

This reuses the existing recipe steps — the sub-recipe only adds the triage/planning prefix and the per-issue dispatch loop.
