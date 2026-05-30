# rules/graph/

Graph semantic rule modules (4 rule files).

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | Docstring-only — rules register via `@semantic_rule` decorator on import |
| `rules_graph.py` | Unbounded cycle detection (DFS) |
| `rules_graph_output.py` | Merge-base unpublished, tool output routing, skill result routing gap |
| `rules_graph_review.py` | Pass-through validity, review waypoint guards, context limit |
| `rules_graph_routes.py` | Route completeness, structural ordering, clone root validation |

## Architecture Notes

No cross-imports between rule modules. Each rule receives a `ValidationContext` argument.
