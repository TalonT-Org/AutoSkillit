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
  prefix_entry:
    sub_recipe: my-prefix   # name of sub-recipe (no extension)
    gate: enable_prefix     # ingredient controlling activation
    on_success: done        # where to route after sub-recipe completes
    on_failure: escalate_stop
    note: >
      When gate=true, my-prefix steps are merged here.
      When gate=false (default), this step is dropped at load time.
```

The referenced ingredient must be declared as hidden so it is not shown to agents:

```yaml
ingredients:
  enable_prefix:
    description: Enable the prefix sub-recipe
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

Pass `overrides` when loading the recipe via the `open_kitchen` or `load_recipe` MCP
tool to activate a gated sub-recipe:

```
open_kitchen(name="my-recipe", overrides={"enable_prefix": "true"})
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
