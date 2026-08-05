# Explorer Agents

AutoSkillit provides two specialized, terminal Codex exploration roles for an L1 exploration
parent:

- `semantic-code-navigator` investigates semantic and structural code relationships.
- `repository-impact-profiler` investigates registries, configuration, generated artifacts,
  tests, and downstream consumers.

The parent decides which role to call, adapts any cross-leaf handoff, and produces the final
synthesis. Explorer leaves do not call each other, spawn descendants, or make repository changes.

## Skill adoption

An adopting `SKILL.md` declares each reviewed exploration vector in the flat
`exploration_vectors` frontmatter schema and binds its canonical prose to an exact marker pair:

```markdown
<!-- autoskillit:exploration-vector id="affected-files" -->
Canonical task prose retained in source control.
<!-- /autoskillit:exploration-vector -->
```

Every vector records its disposition, rationale, applicability, role, repository profile,
relationship classes, task and frontier identities, dependencies, scope, result/report bounds,
evidence version, and native-dispatch decision. Migrated vectors name one registered role and are
replaced only after the session backend and exploration router plan are bound. Retained or excluded
vectors keep their reviewed prose and cannot request native dispatch.

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

Phase D extends reviewed adoption to `investigate`, `scope`, and the representative
`arch-lens-module-dependency`, `arch-lens-state-lifecycle`, and `arch-lens-development` skills.
Every migrated Phase D vector authors `profile: auto` and keeps one stable task/frontier identity.
The source marker body is canonical review prose; backend projection replaces it with either the
Claude `Agent` launch or the Codex `spawn_agent` launch only after the router plan is bound.

`investigate` uses the closed `investigate-standard` and `investigate-deep` applicability IDs.
Local implementation and error provenance, dependencies, consumers, tests, repeated code
patterns, architecture constraints, history, and blast-radius collection route to typed explorer
packets. Web research, design and recurrence interpretation, hypothesis challenge, solution
generation, recommendation synthesis, breakage judgment, and post-report validation stay with
the existing reasoning agents. Explorer leaves return evidence only and cannot diagnose the root
cause, rank candidates, or select a fix.

`scope` uses the closed `scope-software` and `scope-non-software` applicability IDs so its runtime
branch choice activates only the corresponding migrated vectors. Retained and excluded vectors
keep their reviewed marker prose and never receive a native dispatch replacement. The three lens
skills preserve parent-owned synthesis and diagram output while routing only their reviewed local
repository evidence leaves.

Phase E completes the same reviewed adoption across the exact thirteen architecture selectors in
`prepare-pr`: `c4-container`, `concurrency`, `data-lineage`, `deployment`, `development`,
`error-resilience`, `module-dependency`, `operational`, `process-flow`, `repository-access`,
`scenarios`, `security`, and `state-lifecycle`. Each lens keeps its post-exploration analysis,
diagram construction, output path, and parent-owned synthesis unchanged. Its ordered vector
inventory, review rationale and relationship classes, dependency graph, and native-dispatch
decision are test-frozen against the skill frontmatter.

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
