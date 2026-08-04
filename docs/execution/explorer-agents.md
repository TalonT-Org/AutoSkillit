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
