# recipe/

IL-2 recipe layer — YAML schema, validation, semantic rules, dataflow analysis.
Sub-package: rules/ (see rules/AGENTS.md).

## Architecture Notes

`registry.py` uses the `@semantic_rule` decorator pattern (same side-effect registration
as `rules/`). The `_analysis_*.py` modules form an internal BFS-based dataflow analysis
pipeline; callers use `make_validation_context` as the sole entry point.
