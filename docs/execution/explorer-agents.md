# Explorer Agents

AutoSkillit provides two specialized, terminal Codex exploration roles for an L1 exploration
parent:

- `semantic-code-navigator` investigates semantic and structural code relationships.
- `repository-impact-profiler` investigates registries, configuration, generated artifacts,
  tests, and downstream consumers.

The parent decides which role to call, adapts any cross-leaf handoff, and produces the final
synthesis. Explorer leaves do not call each other, spawn descendants, or make repository changes.

## Skill adoption

An adopting skill declares each reviewed exploration vector in a per-skill `exploration.yaml`
sidecar (slim schema) and binds its canonical prose to exact HTML marker pairs in `SKILL.md`:

```markdown
<!-- autoskillit:exploration-vector id="affected-files" -->
Canonical task prose retained in source control.
<!-- /autoskillit:exploration-vector -->
```

The sidecar schema is slim: migrated entries carry `{id, role, relationship_classes, rationale}`
with an optional `applicability` (defaults to `always`); retained entries carry `{id, rationale}`.
Task/frontier identities, profile, dependencies, scope, and evidence version are derived constants
in the parser. Migrated vectors name one registered role and are replaced only after the session
backend and exploration router plan are bound. Retained vectors keep their reviewed prose and are
never dispatched.

Planner vectors author `profile: auto`. The server resolves that selector only from the
factory-owned trusted repository root: exact AutoSkillit repositories receive the AutoSkillit
overlay, other Python repositories receive the generic Python profile, and unrelated repositories
remain language-neutral. The resolved profile and active applicability set are persisted for
resume; canonical skill prose cannot select an overlay.

The initial planner adoption covers `planner-analyze`, `planner-extract-domain`, and
`planner-elaborate-phase`. Their parent sessions still own input interpretation, dynamic mode
selection, waiting, evidence merge, synthesis, and output writes. The three deep-mode-only
`planner-extract-domain` vectors use the closed `planner-extract-domain-deep` applicability, which
preserves their existing `module_count > 20` or layered/hexagonal architecture condition while
still giving applicable sessions native dispatch.

`investigate` and `scope` vectors are all `always`-active. Investigate's standard-mode and
deep-mode vectors both render dispatch packets; mode selection is handled by the SKILL.md section
structure (a deep-mode session routes through the deep-mode section). Scope's vectors are all
retained (its software/non-software split is an in-session judgment, not a projection-time
condition).

The full adoption covers all thirteen architecture selectors, eighteen experiment lenses, twelve
visualization lenses, investigate, scope, and the planner skills. Each lens keeps its
post-exploration analysis, diagram construction, output path, and parent-owned synthesis unchanged.
Its ordered vector inventory, review rationale, and relationship classes are test-frozen against the
exploration sidecar.

Architecture orchestration is pinned to Codex only at the three reviewed recipe-step authorities:
`implementation.run_arch_lenses`, `implementation-groups.run_arch_lenses`, and
`remediation.run_arch_lenses`. The pins use the `recipe_step` tier, so unrelated steps and recipes
continue to inherit their configured backend while existing planner, investigate, and scope pins
remain intact.

Phase F applies the reviewed contract to the eighteen experiment lenses registered by
`make-experiment-diag`. Each lens has one shared `missing-context-fields` Step-0 vector and five
lens-specific Step-1 vectors. Step 0 may investigate only fields that remain absent after parent
argument parsing: complete supplied fields are never rediscovered or overwritten, and absent or
unrelated evidence is reported explicitly without widening scope, inferring scientific meaning,
or executing the target. Across the family, all 108 vectors use `profile: auto`, while the ninety
authored Step-1 vectors retain their exact ordered roles and evidence relationships.

Experiment-lens orchestration is pinned to Codex only for
`research.run_experiment_lenses` and `research-review.run_experiment_lenses`, again at the
`recipe_step` tier. Lens analysis, scientific judgment, optional visualization, temp directories,
diagram paths, and final synthesis remain parent-owned.

Phase G reviews all twelve packaged visualization lenses. The shared selector currently reaches
exactly seven: `always-on`, `temporal`, `multi-compare`, `chart-select`, `uncertainty`,
`figure-table`, and `methodology-norms`. The other five remain packaged with their reviewed
semantics but are not silently added to either recipe selector. Supplied caller context and
external or scientific judgments remain parent-owned; only bounded repository evidence vectors
receive native dispatch. Figure-spec blocks, the always-on `yaml:spec-index`, temp directories,
and diagram paths remain unchanged.

Visualization application is pinned to Codex only for `research.vis_apply` and
`research-design.vis_apply` at the `recipe_step` tier. Both recipes retain dynamic `{slug}`
materialization, so the pin selects the backend without changing the chosen lens or its arguments.

## Policy

Both roles use the validated native Codex policy: `gpt-5.6-luna`, maximum reasoning effort, and
read-only access. These settings are part of the role contract, not user configuration. The
effective parent sandbox must also be read-only: a Codex child role cannot narrow a more permissive
parent sandbox.

## Boundaries

Explorer output is advisory evidence. Deterministic runtime code remains authoritative for
repository identity, snapshot capture, collector precedence, graph semantics, merging,
pagination, and completeness. Callers should use an explorer for bounded investigation and retain
the evidence needed to verify its conclusions.

The capability conformance gate validates the native role projection and effective runtime policy
before production use. See [the conformance decision](../design/explorer-capability-conformance.md)
for the release-gate evidence and limitations.
