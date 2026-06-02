# rules/dataflow/

Dataflow semantic rule modules (4 rule files).

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | Docstring-only — rules register via `@semantic_rule` decorator on import |
| `rules_dataflow.py` | Capture key validation, dead output, weak constraint |
| `rules_dataflow_callable.py` | Callable contract validation, signature mismatch, context gap, work_dir arg misplacement |
| `rules_dataflow_handoff.py` | Implicit handoff, uncaptured consumer, merge cleanup, stale ref |
| `rules_dataflow_multipart.py` | Multi-part recipe iteration notes validation |

## Architecture Notes

No cross-imports between rule modules. Each rule receives a `ValidationContext` argument.
