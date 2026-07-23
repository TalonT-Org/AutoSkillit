# Review-PR Capability-Contract Immunity Verification

Date: 2026-07-23

This record separates deterministic projection behavior from stochastic agent behavior.
The retry was explicitly constrained to direct repository work: no `open_kitchen`,
`run_skill`, or orchestration. Consequently, live agent dispatch attempts were zero and
no provider or backend was invoked. The associated retry deviation records that boundary.

## Four-way matrix

The deterministic matrix is implemented by
`test_review_pr_four_way_metadata_transport_projection_matrix`.

| Variant | Machine `run_skill` declaration | Transport sentence | Projected machine keys | Projected transport sentence | Dispatch attempts |
|---|---:|---:|---:|---:|---:|
| Metadata plus transport | present | present | absent | present | 0 |
| Metadata removed | absent | present | absent | present | 0 |
| Transport removed | present | absent | absent | absent | 0 |
| Both removed | absent | absent | absent | absent | 0 |

The test requires byte identity between the first two projected documents and between
the last two projected documents. It also requires the transport-present and
transport-absent projections to differ. This verifies that machine metadata cannot leak
into the model-facing document and that removal of the independent transport sentence
remains observable.

## Context and limits

- Source: bundled `review-pr` canonical skill contract.
- Projection: `project_agent_skill_document` with a bound immutable catalog.
- Provider/backend context: not applicable; no live session was launched.
- Live agent output or failures: none collected because dispatch was prohibited.
- Causal scope: the deterministic result establishes projection behavior only. It does
  not claim a causal effect on a stochastic reviewer or CI outcome.
