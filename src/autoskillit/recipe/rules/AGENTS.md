# rules/

Semantic validation rule modules for recipe analysis, split between flat rules and four
thematic subdirectories. Package initializers are docstring-only; importing rule modules
registers them through the `@semantic_rule` decorator.

## Subdirectories

| Subdirectory | Files | Purpose |
|---|---|---|
| `campaign/` | 5 rules | Campaign capture, deps, dispatch, flow, ingredients |
| `ci/` | 4 rules | CI config hygiene, conflict, guards, merge queue |
| `dataflow/` | 4 rules | Dataflow capture, callable, handoff, multipart, callable verdict routing completeness |
| `graph/` | 5 rules | Graph cycles, output, review, routes, summary-vs-graph divergence |

## Architecture Notes

Side-effect registration: callers import the package to trigger `@semantic_rule` decorator registration of all rule modules. Each rule receives a `ValidationContext` argument. No cross-imports between rule modules.

Rule modules are organized into subdirectories by theme (campaign/, ci/, dataflow/, graph/) to reduce flat-file sprawl.

## Multi-concern files

Files with no registration support their folder; files with several registrations group one related concern.

- `__init__.py` — Support module: Recipe semantic rule modules — imported for side-effect registration.
- `rules_actions.py` — Groups 5 registrations: Semantic validation rules for action-type steps (stop, route, confirm).
- `rules_backend_compat.py` — Support module: Backend compatibility is adapted from typed skill semantics at execution time.
- `rules_blocks.py` — Groups 5 registrations: Block-level semantic validation rules for recipe pipelines.
- `rules_bypass.py` — Groups 9 registrations: Semantic validation rules for skip_when_false bypass routing contracts.
- `rules_clone.py` — Groups 5 registrations: Semantic validation rules for clone, push, and multipart-plan capture.
- `rules_cmd.py` — Groups 8 registrations: Semantic rules for run_cmd echo-capture alignment in recipe steps.
- `rules_contracts.py` — Groups 11 registrations: Semantic rules for skill contract completeness.
- `rules_features.py` — Groups 3 registrations: Semantic rules for feature-gated tool and skill references.
- `rules_food_truck.py` — Groups 2 registrations: Semantic validation rules for food-truck recipes.
- `rules_inline_script.py` — Groups 2 registrations: Semantic rules: detect inline shell scripts in run_cmd cmd fields.
- `rules_inputs.py` — Groups 13 registrations: Input and ingredient validation rules for recipe pipelines.
- `rules_isolation.py` — Groups 2 registrations: Semantic rules for workspace isolation — prevent recipes from operating on the source repo.
- `rules_loop_counter.py` — Groups 4 registrations: Semantic validation rules — loop counter scope isolation.
- `rules_merge.py` — Support module: Semantic rules for merge_worktree — thin facade for sibling rule modules.
- `rules_merge_context.py` — Groups 2 registrations: Semantic rules for merge gate test output context forwarding enforcement.
- `rules_merge_enrollment.py` — Groups 2 registrations: Semantic rules for gh pr merge silent-success and enrollment auto-consistency (R5, R8).
- `rules_merge_guards.py` — Groups 2 registrations: Semantic rules for merge_worktree commit_guard enforcement (R4, R6).
- `rules_merge_routing.py` — Groups 3 registrations: Semantic rules for merge_worktree route completeness, cross-site consistency, failure-domain matching, and recovery-class classification (R1-R3).
- `rules_optional_capture.py` — Groups 2 registrations: Semantic rules for optional capture guard enforcement.
- `rules_packs.py` — Groups 2 registrations: Semantic rules for pack validation in recipe pipelines.
- `rules_phoropter_adjacency.py` — Groups 2 registrations: Semantic validation rules enforcing phoropter step adjacency (dial→apply→synthesize).
- `rules_reachability.py` — Groups 2 registrations: Symbolic reachability semantic rules for recipe pipelines.
- `rules_recipe.py` — Groups 3 registrations: Semantic rules for sub-recipe reference validity and with_args hygiene.
- `rules_skill_content.py` — Support module: Compatibility facade for the decomposed `rules_skill_content` family.
- `rules_skill_content_github_api_safety.py` — Groups 3 registrations: SKILL.md GitHub-API-safety semantic rules.
- `rules_skill_content_shell_safety.py` — Groups 6 registrations: SKILL.md shell-safety semantic rules.
- `rules_skill_content_skill_contract.py` — Groups 5 registrations: SKILL.md skill-contract semantic rules.
- `rules_skills.py` — Groups 3 registrations: Semantic rules for skill_command resolvability.
- `rules_tools.py` — Groups 11 registrations: Semantic rules for MCP tool name validity.
- `rules_verdict.py` — Groups 4 registrations: Semantic rules for skill verdict routing completeness and consistency.
- `rules_worktree.py` — Groups 9 registrations: Worktree and retry validation rules for recipe pipelines.
