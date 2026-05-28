# rules/

Semantic validation rule modules for recipe analysis (46 rule files).

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | Docstring-only — rules register via `@semantic_rule` decorator on import |
| `rules_actions.py` | Semantic rules for `stop`/`route`/`confirm` action-type steps |
| `rules_blocks.py` | Block-level budget rules; loads `block_budgets.yaml` at import |
| `rules_bypass.py` | Rules for `skip_when_false` bypass routing contracts |
| `rules_callable_scope.py` | Enforces scoped directory args for file-discovering callables (e.g. `batch_create_issues` → `audit_run_dir`) |
| `rules_campaign_capture.py` | Campaign capture validation: identifier keys, result refs, sentinel cross-checks |
| `rules_campaign_deps.py` | Campaign dependency graph rules: valid refs, acyclic, sequential |
| `rules_campaign_dispatch.py` | Campaign dispatch structure: kind, names, recipe refs, packs, task |
| `rules_campaign_flow.py` | Campaign flow control: gates, paths, campaign refs, version, skip-when |
| `rules_campaign_ingredients.py` | Campaign ingredient validation: keys, dangling, required, string types |
| `rules_ci.py` | CI config hygiene: inline shell, event scope, workflow, timeout |
| `rules_ci_conflict.py` | CI conflict gate routing, mergeability, auto_trigger checks |
| `rules_ci_guards.py` | CI applicability guards, self-loop, enqueue gate, cwd/branch mismatch |
| `rules_ci_merge_queue.py` | Merge queue PR state routing completeness and conformance |
| `rules_clone.py` | Clone/push dataflow rules: missing remote URL, local-strategy capture |
| `rules_cmd.py` | `run_cmd` echo-capture alignment; git remote command detection |
| `rules_contracts.py` | Skill contract completeness rules |
| `rules_dataflow.py` | Capture key validation, dead output, weak constraint |
| `rules_dataflow_callable.py` | Callable contract validation, signature mismatch, context gap |
| `rules_dataflow_handoff.py` | Implicit handoff, uncaptured consumer, merge cleanup, stale ref |
| `rules_dataflow_multipart.py` | Multi-part recipe iteration notes validation |
| `rules_features.py` | Feature-gated tool/skill reference validation |
| `rules_food_truck.py` | Food-truck recipe validation: sentinel stop step requirement |
| `rules_fixing.py` | Conditional-write skill must gate on declared verdict output |
| `rules_graph.py` | Unbounded cycle detection (DFS) |
| `rules_graph_output.py` | Merge-base unpublished, tool output routing, skill result routing gap |
| `rules_graph_review.py` | Pass-through validity, review waypoint guards, context limit |
| `rules_graph_routes.py` | Route completeness, structural ordering, clone root validation |
| `rules_inline_script.py` | Detects inline shell scripts in `run_cmd` cmd fields |
| `rules_inputs.py` | Input/ingredient validation; version compatibility checks; condition-value-domain checks |
| `rules_isolation.py` | Workspace isolation rules (prevents operating on source repo) |
| `rules_merge.py` | `merge_worktree` routing completeness |
| `rules_merge_queue.py` | Merge queue push routing: `queued_branch` error route enforcement |
| `rules_optional_capture.py` | Optional capture guard enforcement: detect steps with optional output patterns routing to consumers without a truthiness guard |
| `rules_packs.py` | Pack validation (names must exist in `PACK_REGISTRY`) |
| `rules_reachability.py` | Symbolic BFS reachability; capture-inversion detection |
| `rules_remediation.py` | audit-impl remediation_path capture must have non-terminal non-GO route |
| `rules_recipe.py` | Sub-recipe reference validity and `with_args` hygiene |
| `rules_route_gate.py` | Route gate shared-stop detection; fallback and primary path convergence |
| `rules_loop_progress.py` | Loop progress tracking: run_skill steps in cycles must capture declared outputs |
| `rules_skill_content.py` | Undefined bash placeholder detection in SKILL.md |
| `rules_skills.py` | `skill_command` resolvability rules |
| `rules_skip_inviting_notes.py` | Flags note: fields with skip-inviting phrases ('never blocks', 'best-effort', 'optional: true') on optional steps |
| `rules_temp_path.py` | Rejects bare `{{AUTOSKILLIT_TEMP}}/` without scope prefix |
| `rules_tools.py` | MCP tool name validity (must be in known tool sets) |
| `rules_verdict.py` | Skill verdict routing completeness and cross-step consistency |
| `rules_worktree.py` | Worktree and retry validation rules; superseded-input-after-capture detection |

## Architecture Notes

Side-effect registration: callers import the package to trigger `@semantic_rule` decorator registration of all 46 rule modules. Each rule receives a `ValidationContext` argument. No cross-imports between rule modules.
