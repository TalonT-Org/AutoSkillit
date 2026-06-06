# rules/

Semantic validation rule modules for recipe analysis (46 flat rule files + 4 subdirectories).

## Subdirectories

| Subdirectory | Files | Purpose |
|---|---|---|
| `campaign/` | 5 rules | Campaign capture, deps, dispatch, flow, ingredients |
| `ci/` | 4 rules | CI config hygiene, conflict, guards, merge queue |
| `dataflow/` | 4 rules | Dataflow capture, callable, handoff, multipart |
| `graph/` | 4 rules | Graph cycles, output, review, routes |

See each subdirectory's CLAUDE.md for details.

## Flat Files

| File | Purpose |
|------|---------|
| `__init__.py` | Docstring-only — rules register via `@semantic_rule` decorator on import |
| `rules_actions.py` | Semantic rules for `stop`/`route`/`confirm` action-type steps |
| `rules_backend_compat.py` | Backend-skill compatibility: flags `run_skill` steps whose `backend_requirements` exclude the target backend |
| `rules_blocks.py` | Block-level budget rules; loads `block_budgets.yaml` at import |
| `rules_bypass.py` | Rules for `skip_when_false` bypass routing contracts and `hidden-input-ref-in-template` detection |
| `rules_callable_scope.py` | Enforces scoped directory args for file-discovering callables |
| `rules_clone.py` | Clone/push dataflow rules: missing remote URL, local-strategy capture |
| `rules_cmd.py` | `run_cmd` echo-capture alignment; git remote command detection; bare git rebase without conflict routing detection; path-typed capture non-empty file guard detection |
| `rules_contracts.py` | Skill contract completeness rules |
| `rules_failure_verdict_bypass.py` | Detects bypass routes from verdict-gated steps reaching success stop terminals |
| `rules_features.py` | Feature-gated tool/skill reference validation |
| `rules_flake_loop.py` | Flake-suspected unwinnable loop detection for merge gate cycles |
| `rules_food_truck.py` | Food-truck recipe validation: sentinel stop step requirement |
| `rules_fixing.py` | Conditional-write skill must gate on declared verdict output |
| `rules_ingredient_step_name.py` | 1:1 gating ingredient ↔ step name asymmetry detection |
| `rules_inline_script.py` | Detects inline shell scripts in `run_cmd` cmd fields |
| `rules_inputs.py` | Input/ingredient validation; version compatibility checks |
| `rules_isolation.py` | Workspace isolation rules (prevents operating on source repo) |
| `rules_merge.py` | `merge_worktree` routing completeness |
| `rules_merge_context.py` | Merge gate test output context forwarding enforcement |
| `rules_merge_queue.py` | Merge queue push routing: `queued_branch` error route enforcement |
| `rules_optional_capture.py` | Optional capture guard enforcement |
| `rules_packs.py` | Pack validation (names must exist in `PACK_REGISTRY`) |
| `rules_phoropter_adjacency.py` | Phoropter phase-order and step-interleaving adjacency rules; canonical phase predicate `_canonical_phase_for_step`; family-prefix loader backed by phoropter-registry.yaml |
| `rules_pseudocode_sync.py` | SKILL.md pseudocode constant-reference divergence from run_python callables |
| `rules_reachability.py` | Symbolic BFS reachability; capture-inversion detection |
| `rules_audit_impl_topology.py` | audit-impl-diff-topology-mismatch semantic rule |
| `rules_remediation.py` | audit-impl remediation_path capture routing rules |
| `rules_recipe.py` | Sub-recipe reference validity and `with_args` hygiene |
| `rules_route_gate.py` | Route gate shared-stop detection; fallback and primary path convergence |
| `rules_loop_artifact_scope.py` | Loop artifact isolation: run_skill steps in cycles must use iteration-scoped output_dir |
| `rules_loop_counter.py` | Loop counter scope isolation: cross-path sharing and guard-before-verify detection |
| `rules_loop_progress.py` | Loop progress tracking: run_skill steps in cycles must capture declared outputs |
| `rules_skill_content.py` | SKILL.md content validation: undefined bash placeholders, source-attribution directives, output formatting, issue comment prohibition, inline-content-in-subagent-prompt detection |
| `rules_skill_write_path_alignment.py` | Cross-layer validation: SKILL.md declared write scope must align with recipe step output_dir; fires ERROR when iteration-scoped output_dir is narrower than SKILL.md NEVER block path and the skill doesn't use a dynamic write variable |
| `rules_skills.py` | `skill_command` resolvability rules |
| `rules_stamp_ownership.py` | Exclusive stamp ownership enforcement across skills |
| `rules_stop_sentinel_direction.py` | Stop terminals must emit the correct success direction based on their graph position |
| `rules_step_naming.py` | Step-key vs invoked-skill collision detection |
| `rules_skip_inviting_notes.py` | Flags note: fields with skip-inviting phrases on optional steps |
| `rules_temp_path.py` | Rejects bare `{{AUTOSKILLIT_TEMP}}/` without scope prefix |
| `rules_terminal_convergence.py` | Success-stop reason uniqueness; detects convergent success paths with shared reasons |
| `rules_tools.py` | MCP tool name validity (must be in known tool sets) |
| `rules_verdict.py` | Skill verdict routing completeness and cross-step consistency |
| `rules_verdict_degradation.py` | verdict-ungated-degradation: errors when degradation path emits a verdict used by nominal success path |
| `rules_worktree.py` | Worktree and retry validation rules |

## Architecture Notes

Side-effect registration: callers import the package to trigger `@semantic_rule` decorator registration of all rule modules. Each rule receives a `ValidationContext` argument. No cross-imports between rule modules.

Rule modules are organized into subdirectories by theme (campaign/, ci/, dataflow/, graph/) to reduce flat-file sprawl.
