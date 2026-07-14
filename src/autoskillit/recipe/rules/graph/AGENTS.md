# rules/graph/

Graph semantic rule modules (5 rule files).

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | Docstring-only — rules register via `@semantic_rule` decorator on import |
| `rules_graph.py` | Unbounded cycle detection (DFS) |
| `rules_graph_output.py` | Merge-base unpublished, tool output routing, skill result routing gap |
| `rules_graph_review.py` | Pass-through validity, review waypoint guards, context limit |
| `rules_graph_routes.py` | Route completeness, structural ordering, clone root validation |
| `rules_graph_summary.py` | Summary-vs-graph divergence: phase waypoint disclosure, optional-marker agreement, success-path ordering |

## Architecture Notes

No cross-imports between rule modules. Each rule receives a `ValidationContext` argument.
