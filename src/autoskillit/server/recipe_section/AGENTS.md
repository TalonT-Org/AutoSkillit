# recipe_section/

Supporting internals for deterministic, schema-driven recipe-section delivery.

## Files

| File | Purpose |
|---|---|
| `__init__.py` | Package marker with no public re-exports |
| `_contracts.py` | Shared planner/verifier errors, selections, descriptors, manifests, and page-plan types |
| `_lifecycle.py` | One-way kitchen-retirement callback registry for supporting-state cleanup |
| `_rendering.py` | Bounded wire rendering for registered recipe-section tool failures |
| `_verification.py` | Post-digest descriptor, reconstruction, ordering, and rendered-bound invariant proof |
