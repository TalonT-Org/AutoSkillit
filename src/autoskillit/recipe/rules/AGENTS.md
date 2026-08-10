# rules/

Semantic validation rule modules for recipe analysis, split between flat rules and four
thematic subdirectories. Package initializers are docstring-only; importing rule modules
registers them through the `@semantic_rule` decorator.

## Subdirectories

| Subdirectory | Files | Purpose |
|---|---|---|
| `campaign/` | 5 rules | Campaign capture, deps, dispatch, flow, ingredients |
| `ci/` | 4 rules | CI config hygiene, conflict, guards, merge queue |
| `dataflow/` | 4 rules | Dataflow capture, callable, handoff, multipart, callable verdict routing completeness |
| `graph/` | 5 rules | Graph cycles, output, review, routes, summary-vs-graph divergence |

## Architecture Notes

Side-effect registration: callers import the package to trigger `@semantic_rule` decorator registration of all rule modules. Each rule receives a `ValidationContext` argument. No cross-imports between rule modules.

Rule modules are organized into subdirectories by theme (campaign/, ci/, dataflow/, graph/) to reduce flat-file sprawl.
