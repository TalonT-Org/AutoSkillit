# Explorer Agents

AutoSkillit provides two specialized, terminal Codex exploration roles for an L1 exploration
parent:

- `semantic-code-navigator` investigates semantic and structural code relationships.
- `repository-impact-profiler` investigates registries, configuration, generated artifacts,
  tests, and downstream consumers.

The parent decides which role to call, adapts any cross-leaf handoff, and produces the final
synthesis. Explorer leaves do not call each other, spawn descendants, or make repository changes.

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
