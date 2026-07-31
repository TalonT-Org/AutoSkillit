# planner/

IL-1 progressive resolution planner — phases, work packages, manifest generation, DAG validation.

## Architecture Notes
The planner layer must not import from `server/` or `recipe/`. `validation.py` performs a DAG cycle check before any
compilation proceeds. `consolidation.py` runs as a post-pass after all elaboration phases
complete.
