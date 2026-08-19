# contracts/

Protocol satisfaction, package gateway, and skill contract compliance tests.

`_anti_fab_helpers.py` mirrors the production anti-fabrication guard.
`_projection_helpers.py` supplies shared session catalogs and stale snapshots for
plugin-projection contract tests.

## Architecture Notes

`conftest.py` provides `REFUSAL_SIGNALS` constants shared across many contract tests. `_anti_confirm_helpers.py` mirrors the production anti-confirmation regex for structural contract verification.

## Config-Field-Has-Consumer Discipline (#4684)

`test_config_field_has_consumer.py` generalizes the `inert-tracked:#NNNN`
discipline documented at `tests/AGENTS.md` § run_skill Parameter-Role Ledgers
(precedent: `test_recipe_step_field_ledger.py`, applied there to `RecipeStep`
fields) to every `@dataclass` directly defined in `config/_config_dataclasses.py`.
An advertised-but-unread config field is the most dangerous config shape — it
makes reviewers believe a gate exists when none does. `#4684`'s original bug
was exactly this: `AgentBackendConfig.force_claude_agent_teams_inactive` was
declared and documented but never read anywhere in `src/`, while the policy it
was supposed to gate ran unconditionally.

A field is "live" iff either:
- some production module outside `config/_config_dataclasses.py` reads
  `.<field_name>` directly, **or**
- a method defined on the same dataclass (excluding `__post_init__`, which is
  auto-invoked at construction regardless of whether the field's value ever
  reaches real behavior) reads `self.<field_name>` and that method itself has
  an external call site — e.g. `GitHubConfig.allowed_labels` has no direct
  external `.allowed_labels` access, only external calls to
  `check_label_allowed(...)`/`check_labels_allowed(...)`, which read
  `self.allowed_labels` internally, **or**
- the field's doc-comment carries an `inert-tracked:#NNNN` annotation citing
  an open issue.

Unlike the frozen-ledger pattern above, this contract does not require a
same-commit ledger edit — it re-derives liveness by AST/grep scan on every
run, so drift is caught automatically rather than by comparing against a
hand-maintained table. The `inert-tracked:#NNNN` escape hatch exists because
this scan (like Vulture's dead-code check) has known false negatives for
fields consumed through indirection this scan cannot see (e.g. `getattr`
reflection, serialization round-trips); a field wired through such
indirection but genuinely orphaned in practice should still get a tracking
issue rather than silently passing. Widening enforcement to a newly-added
dataclass is safe by construction — a currently-orphaned field either gets a
real consumer or an `inert-tracked:#NNNN` annotation before this test can
pass — but widening also finds pre-existing, unrelated orphans (see
`RunSkillConfig.natural_exit_grace_seconds`, `ProviderProfileDef.context_window`
in `_config_dataclasses.py`, both `inert-tracked:#4693`); resolve those
separately rather than folding an unrelated fix into whatever change
triggered the discovery.

Distinct from `test_config_field_coverage.py`, which checks a different thing
(every dataclass field is populated by `_build_subconfig`) — a field can be
populated and still have zero readers.
