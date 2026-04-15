# Research pipeline example

Four end-to-end runs of the `research` recipe against
[`TalonT-Org/spectral-init`](https://github.com/TalonT-Org/spectral-init).
Each example pairs the research PR (where the experiment ran) with the
archive PR (where the artifacts were committed) and links the on-disk
artifact tree.

These four pairs are also encoded in
`tests/docs/test_doc_links.py` as the canonical allowlist; if you add a
fifth example, update the test fixture in the same commit.

## Example 1 — initial spectral baseline

- Research PR: <https://github.com/TalonT-Org/spectral-init/pull/233>
- Archive PR: <https://github.com/TalonT-Org/spectral-init/pull/234>
- Artifact tree:
  <https://github.com/TalonT-Org/spectral-init/tree/main/research/2026-03-baseline>

The first run exercised the `research` recipe end to end against an empty
target repository. The orchestrator collected ingredients, ran
`scope` → `plan-experiment` → `implement-experiment` → `run-experiment` →
`generate-report`, and committed both the experiment results and the auxiliary
artifacts in two separate PRs so reviewers could read the report without
diffing the data.

## Example 2 — comparator construction follow-up

- Research PR: <https://github.com/TalonT-Org/spectral-init/pull/238>
- Archive PR: <https://github.com/TalonT-Org/spectral-init/pull/239>
- Artifact tree:
  <https://github.com/TalonT-Org/spectral-init/tree/main/research/2026-03-comparator>

Followed up on Example 1 by running the `exp-lens-comparator-construction`
lens skill against the prior baseline. The pipeline split the research and
archive concerns the same way: the research PR carries the report and the
archive PR carries the regenerated lens diagram.

## Example 3 — variance stability rerun

- Research PR: <https://github.com/TalonT-Org/spectral-init/pull/256>
- Archive PR: <https://github.com/TalonT-Org/spectral-init/pull/257>
- Artifact tree:
  <https://github.com/TalonT-Org/spectral-init/tree/main/research/2026-03-variance>

Rerun of the variance-stability experiment after a tooling change. The
orchestrator cleanly resumed from the prior staleness-cache hit so only the
affected experiment artifacts were regenerated.

## Example 4 — sensitivity sweep

- Research PR: <https://github.com/TalonT-Org/spectral-init/pull/263>
- Archive PR: <https://github.com/TalonT-Org/spectral-init/pull/264>
- Artifact tree:
  <https://github.com/TalonT-Org/spectral-init/tree/main/research/2026-04-sensitivity>

Final example: a sensitivity sweep across three input parameters, fanned
out via wavefront scheduling and stitched together by `pipeline-summary`
into a single GitHub issue.
