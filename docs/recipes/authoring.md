# Recipe authoring

How to write a new recipe, what `validate_recipe` checks, and how the
contract / migration / staleness pipeline keeps recipes from rotting.

## Family vs bundled

A **bundled recipe** lives under `src/autoskillit/recipes/*.yaml` and ships
with the plugin (6 today: `implementation`, `implementation-groups`,
`merge-prs`, `planner`, `remediation`, `research`). A **project-local recipe** lives
under `.autoskillit/recipes/*.yaml` in a downstream project and overrides
the bundled name.

A **family** recipe references one or more sub-recipes via the `requires_packs`
field rather than inlining the steps. Sub-recipes live under
`src/autoskillit/recipes/sub-recipes/`.

## Schema reference

Every recipe is a `Recipe` dataclass parsed from YAML by `recipe/io.py`. The
top-level keys (defined in `recipe/schema.py:Recipe`):

| Key | Type | Purpose |
|-----|------|---------|
| `name` | str | Recipe identifier; matches the YAML filename stem |
| `description` | str | Single-line summary used by `autoskillit recipes list` |
| `autoskillit_version` | str | Minimum AutoSkillit version this recipe targets |
| `ingredients` | list[Ingredient] | Inputs the orchestrator collects from the user |
| `requires_packs` | list[str] | Sub-recipes or named packs to import |
| `steps` | list[RecipeStep] | The sequenced step graph |

`RecipeStep` adds: `name`, `tool`, `with_args`, `capture`, `on_result`,
`skip_when_false`, `retries`.

## Authoring flow

1. Sketch the step graph on paper. Identify each tool call, capture, and
   route.
2. Copy a similar bundled recipe as a starting template
   (`src/autoskillit/recipes/implementation.yaml` is a good baseline).
3. Run `autoskillit validate <path>` after every edit.
4. Run `autoskillit recipes render <name>` to generate the flow diagram and
   eyeball the topology.
5. Add the recipe to your project's `.autoskillit/recipes/` and run a small
   `order` against a sandbox issue.

## The 24 semantic rule families

`validate_recipe` runs every rule registered with `recipe/registry.py:semantic_rule`.
The rule families live in `src/autoskillit/recipe/rules_*.py` (24 files):

| File | What it catches |
|------|-----------------|
| `rules_actions.py` | Action-type semantic rules: `stop-step-has-no-routing`, `recipe-has-terminal-step`, `route-step-requires-on-result` |
| `rules_blocks.py` | Block-level budget rules: per-block `run_cmd` and `run_skill` call-count budgets |
| `rules_bypass.py` | `skip_when_false` routes that have no fallthrough — a step that gets bypassed must also have a downstream consumer that handles the bypass |
| `rules_campaign.py` | Campaign recipe structural rules (step count, required ingredient presence for campaign mode) |
| `rules_ci.py` | CI polling steps written as inline shell commands instead of `wait_for_ci` |
| `rules_clone.py` | Clone/push workflow integrity — every `clone_repo` must be paired with a `register_clone_status` and a `remove_clone` route |
| `rules_cmd.py` | `run_cmd` echo-capture alignment and `find` rediscovery anti-patterns |
| `rules_contracts.py` | Skills referenced in steps that are missing required output patterns in their contract card |
| `rules_dataflow.py` | Capture/output dataflow — a captured value must be consumed by a downstream step or removed |
| `rules_features.py` | Feature-flag gating rules: steps that reference features not declared in `requires_packs` |
| `rules_fixing.py` | Fixing/Rectify workflow rules: rectify loops missing a terminal success route |
| `rules_graph.py` | Step graph reachability and cycle detection |
| `rules_inputs.py` | Ingredient/version validation — required ingredients are present, types are correct |
| `rules_isolation.py` | Workspace isolation breaches: `source-isolation-violation` and `git-mutation-on-source` |
| `rules_merge.py` | `merge_worktree` routing completeness — every `MergeFailedStep` value must have a route |
| `rules_packs.py` | Unknown pack names referenced in `requires_packs` |
| `rules_reachability.py` | Symbolic reachability: `capture-inversion-detection`, `event-scope-requires-upstream-capture` |
| `rules_recipe.py` | Unknown sub-recipe references inside a `requires_packs` entry |
| `rules_skill_content.py` | SKILL.md bash-block placeholder validation (`undefined-bash-placeholder`) |
| `rules_temp_path.py` | Temp-path hygiene: steps that write to paths outside `.autoskillit/temp/` |
| `rules_skills.py` | `skill_command` resolvability against the bundled skill registry (`unknown-skill-command`) |
| `rules_tools.py` | MCP tool name and parameter validity (`unknown-tool`, `dead-with-param`) |
| `rules_verdict.py` | Skill verdict routing completeness — every emitted verdict value must be routed |
| `rules_worktree.py` | Worktree retry lifecycle — every step that creates a worktree must have a downstream merge or cleanup |

The 24-family count is enforced by `tests/docs/test_doc_counts.py`.

## Contract cards

Every skill referenced by a recipe carries a **contract card** in
`src/autoskillit/recipes/contracts/<recipe>/<skill>.md`. The card lists the
inputs the recipe will pass, the structured output tokens the recipe expects
to read, and the verdicts the recipe routes on. The contract is the source of
truth for `rules_contracts.py`.

## Staleness detection and Haiku triage

`recipe/staleness_cache.py` writes a per-contract hash and timestamp to disk.
When a recipe loads, the cache is consulted and any contract hash that has
drifted is enqueued for an LLM-assisted re-check via `_llm_triage.py`, which
spawns a Haiku subprocess to compare the deployed SKILL.md against the
contract card. The Haiku verdict is one of `FRESH`, `STALE`, or `UNCERTAIN`;
`UNCERTAIN` is escalated to a human via `cli/_stale_check.py`.

## Migration engine

When a recipe declares an `autoskillit_version` older than the running
package, the migration engine in `migration/engine.py` walks the migration
notes under `src/autoskillit/migrations/` and applies them in order. Each
note is a 7-step sequence:

1. Read current YAML.
2. Validate it parses.
3. Apply the note's transformer.
4. Validate the transformed YAML.
5. Diff old vs new.
6. Write the new YAML atomically.
7. Record the new `autoskillit_version`.

Failures are recorded in `migration/store.py:FailureStore` (atomic JSON
writes) so the next `autoskillit migrate` run can resume.
