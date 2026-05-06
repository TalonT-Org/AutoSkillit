# rules/

Semantic validation rule modules for recipe analysis (27 rule files).

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | Docstring-only — rules register via `@semantic_rule` decorator on import |
| `rules_actions.py` | Semantic rules for `stop`/`route`/`confirm` action-type steps |
| `rules_blocks.py` | Block-level budget rules; loads `block_budgets.yaml` at import |
| `rules_bypass.py` | Rules for `skip_when_false` bypass routing contracts |
| `rules_campaign.py` | Campaign recipe validation: dispatch names, ingredient refs |
| `rules_ci.py` | CI polling patterns: PR state handling, CI step ordering |
| `rules_clone.py` | Clone/push dataflow rules: missing remote URL, local-strategy capture |
| `rules_cmd.py` | `run_cmd` echo-capture alignment; git remote command detection |
| `rules_contracts.py` | Skill contract completeness rules |
| `rules_dataflow.py` | Dataflow analysis: pipeline-forbidden tool usage, output chaining |
| `rules_features.py` | Feature-gated tool/skill reference validation |
| `rules_food_truck.py` | Food-truck recipe validation: sentinel stop step requirement |
| `rules_fixing.py` | Conditional-write skill must gate on declared verdict output |
| `rules_graph.py` | Graph/routing analysis rules |
| `rules_inline_script.py` | Detects inline shell scripts in `run_cmd` cmd fields |
| `rules_inputs.py` | Input/ingredient validation; version compatibility checks |
| `rules_isolation.py` | Workspace isolation rules (prevents operating on source repo) |
| `rules_merge.py` | `merge_worktree` routing completeness |
| `rules_merge_queue.py` | Merge queue push routing: `queued_branch` error route enforcement |
| `rules_packs.py` | Pack validation (names must exist in `PACK_REGISTRY`) |
| `rules_reachability.py` | Symbolic BFS reachability; capture-inversion detection |
| `rules_recipe.py` | Sub-recipe reference validity and `with_args` hygiene |
| `rules_skill_content.py` | Undefined bash placeholder detection in SKILL.md |
| `rules_skills.py` | `skill_command` resolvability rules |
| `rules_temp_path.py` | Rejects bare `{{AUTOSKILLIT_TEMP}}/` without scope prefix |
| `rules_tools.py` | MCP tool name validity (must be in known tool sets) |
| `rules_verdict.py` | Skill verdict routing completeness and cross-step consistency |
| `rules_worktree.py` | Worktree and retry validation rules |

## Architecture Notes

Side-effect registration: callers import the package to trigger `@semantic_rule` decorator registration of all 27 rule modules. Each rule receives a `ValidationContext` argument. No cross-imports between rule modules.
