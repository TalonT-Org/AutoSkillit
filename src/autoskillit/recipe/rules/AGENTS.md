# rules/

Semantic validation rule modules for recipe analysis, split between flat rules and thematic
subdirectories. Package initializers are docstring-only; importing rule modules
registers them through the `@semantic_rule` decorator.

## Subdirectories

| Subdirectory | Purpose |
|---|---|
| `campaign/` | Campaign capture, deps, dispatch, flow, ingredients |
| `ci/` | CI config hygiene, conflict, guards, merge queue |
| `dataflow/` | Dataflow capture, callable, handoff, multipart, callable verdict routing completeness |
| `graph/` | Graph cycles, output, review, routes, summary-vs-graph divergence |

## Architecture Notes

Side-effect registration: callers import the package to trigger `@semantic_rule` decorator registration of all rule modules. Each rule receives a `ValidationContext` argument. No cross-imports between rule modules.

Rule modules are organized into subdirectories by theme (campaign/, ci/, dataflow/, graph/) to reduce flat-file sprawl.

## Multi-concern files

Files with no registration support their folder; files with several registrations group one related concern.

- `__init__.py` — Support module: Recipe semantic rule modules — imported for side-effect registration.
- `rules_actions.py` — Registers semantic validation rules for action-type steps (stop, route, confirm).
- `rules_backend_compat.py` — Support module: Backend compatibility is adapted from typed skill semantics at execution time.
- `rules_blocks.py` — Registers block-level semantic validation rules for recipe pipelines.
- `rules_bypass.py` — Registers semantic validation rules for skip_when_false bypass routing contracts.
- `rules_cmd.py` — Registers semantic rules for run_cmd echo-capture alignment in recipe steps.
- `rules_contracts.py` — Registers semantic rules for skill contract completeness.
- `rules_features.py` — Registers semantic rules for feature-gated tool and skill references.
- `rules_food_truck.py` — Registers semantic validation rules for food-truck recipes.
- `rules_inline_script.py` — Registers semantic rules that detect inline shell scripts in run_cmd cmd fields.
- `rules_inputs.py` — Registers input and ingredient validation rules for recipe pipelines.
- `rules_isolation.py` — Registers semantic rules for workspace isolation — prevent recipes from operating on the source repo.
- `rules_loop_counter.py` — Registers semantic validation rules for loop-counter scope isolation.
- `rules_merge.py` — Support module: Semantic rules for merge_worktree — thin facade for sibling rule modules.
- `rules_merge_context.py` — Registers semantic rules for merge-gate test-output context forwarding enforcement.
- `rules_merge_enrollment.py` — Registers semantic rules for gh pr merge silent-success and enrollment auto-consistency (R5, R8).
- `rules_merge_guards.py` — Registers semantic rules for merge_worktree commit_guard enforcement (R4, R6).
- `rules_merge_routing.py` — Registers semantic rules for merge_worktree route completeness, cross-site consistency, failure-domain matching, and recovery-class classification (R1-R3).
- `rules_optional_capture.py` — Registers semantic rules for optional-capture guard enforcement.
- `rules_packs.py` — Registers semantic rules for pack validation in recipe pipelines.
- `rules_phoropter_adjacency.py` — Registers semantic validation rules enforcing phoropter step adjacency (dial→apply→synthesize).
- `rules_plan_set_gate.py` — Registers issue-wide plan-set authority threading and sealed-path reachability.
- `rules_reachability.py` — Registers symbolic reachability semantic rules for recipe pipelines.
- `rules_recipe.py` — Registers semantic rules for sub-recipe reference validity and with_args hygiene.
- `rules_skill_content.py` — Support module: Compatibility facade for the decomposed `rules_skill_content` family.
- `rules_skill_content_github_api_safety.py` — Registers SKILL.md GitHub-API-safety semantic rules.
- `rules_skill_content_shell_safety.py` — Registers SKILL.md shell-safety semantic rules.
- `rules_skill_content_skill_contract.py` — Registers SKILL.md skill-contract semantic rules.
- `rules_skills.py` — Registers semantic rules for skill_command resolvability.
- `rules_tools.py` — Registers semantic rules for MCP tool name validity.
- `rules_verdict.py` — Registers semantic rules for skill verdict routing completeness and consistency.
- `rules_worktree.py` — Registers worktree and retry validation rules for recipe pipelines.