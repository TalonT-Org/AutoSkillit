# rules/ci/

CI semantic rule modules (4 rule files).

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | Docstring-only — rules register via `@semantic_rule` decorator on import |
| `rules_ci.py` | CI config hygiene: inline shell, event scope, workflow, timeout |
| `rules_ci_conflict.py` | CI conflict gate routing, mergeability, auto_trigger checks |
| `rules_ci_guards.py` | CI applicability guards, self-loop, enqueue gate, cwd/branch mismatch |
| `rules_ci_merge_queue.py` | Merge queue PR state routing completeness and conformance |

## Architecture Notes

No cross-imports between rule modules. Each rule receives a `ValidationContext` argument.
