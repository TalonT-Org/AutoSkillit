# Review-PR Capability-Contract Immunity Verification

Date: 2026-07-23

This record separates deterministic projection behavior from stochastic agent behavior.
The retry was explicitly constrained to direct repository work: no `open_kitchen`,
`run_skill`, or orchestration. The live evaluation therefore used four direct,
non-xdist Claude Code invocations, one per isolated `review-pr` variant.

## Deterministic four-way matrix

The deterministic matrix is implemented by
`test_review_pr_four_way_metadata_transport_projection_matrix`.

| Variant | Machine `run_skill` declaration | Transport sentence | Projected machine keys | Projected transport sentence |
|---|---:|---:|---:|---:|
| Metadata plus transport | present | present | absent | present |
| Metadata removed | absent | present | absent | present |
| Transport removed | present | absent | absent | absent |
| Both removed | absent | absent | absent | absent |

The test requires byte identity between the first two projected documents and between
the last two projected documents. It also requires the transport-present and
transport-absent projections to differ. This verifies that machine metadata cannot leak
into the model-facing document and that removal of the independent transport sentence
remains observable.

## Live non-xdist attempts

Each variant was installed as an isolated project-local `review-pr` skill under the
feature worktree's `.autoskillit/temp/retry-worktree/live-review-pr-eval-second/`
directory and invoked directly with:

- Backend: Claude Code 2.1.197
- Requested model: `sonnet`
- Prompt: `/review-pr impl-rectify_run_skill_capability_contract_immunity_2026-07-22_201946-20260722-223229 develop mode=local`
- Mode: non-interactive JSON output, `dontAsk`, no session persistence
- Tools: disabled, preserving the retry's prohibition on orchestration and preventing
  GitHub or filesystem mutations
- Maximum requested budget: USD 0.25 per attempt

| Attempt | Variant | Session ID | Duration | Provider result | Inference/cost |
|---:|---|---|---:|---|---:|
| 1 | Metadata plus transport | `6ac1ed88-a6cc-4f0f-9c80-6193379b95e9` | 611 ms | HTTP 429; weekly limit response | 0 tokens / USD 0 |
| 2 | Metadata removed | `509c3d29-7502-4191-918c-8663c44ea352` | 509 ms | HTTP 429; weekly limit response | 0 tokens / USD 0 |
| 3 | Transport removed | `f952b6b3-79ff-4ec8-b62e-2a08292fc2cf` | 439 ms | HTTP 429; weekly limit response | 0 tokens / USD 0 |
| 4 | Both removed | `32146007-a7d6-4613-bb2b-d099e90314e5` | 352 ms | HTTP 429; weekly limit response | 0 tokens / USD 0 |
| 5 | Metadata plus transport, post-reset retry | `7a0ce09c-ed3c-4acf-aa54-aa5f39aa263f` | 497 ms | HTTP 429; weekly limit response | 0 tokens / USD 0 |

All four provider requests were attempted independently. The provider rejected every
request before inference, so there was no agent completion and no behavioral result to
compare. A fifth request retried the first variant after the provider's reported reset
window and received the same environmental failure. These observations satisfy the
attempt record but provide no stochastic evidence for or against a metadata or
transport-prose effect.

## Context and limits

- Source: bundled `review-pr` canonical skill contract.
- Projection: `project_agent_skill_document` with a bound immutable catalog.
- Provider/backend context: Claude Code 2.1.197 with requested model `sonnet`.
- Live agent output: none; all four requests failed with HTTP 429 before inference.
- Causal scope: the deterministic result establishes projection behavior only. It does
  not claim a causal effect on a stochastic reviewer or CI outcome.

## Committed verification evidence

The durable remediation baseline is the repository tree at
`9562bf47fa2987c7108fbb40ffb07817d185c985`. Unlike the original worktree-only
record, that commit contains the implementation and its tests. The deterministic
matrix is preserved in
`tests/workspace/test_session_skills_provider.py::test_review_pr_four_way_metadata_transport_projection_matrix`.

The verification is reproducible from the committed tree with `develop` as the base ref:

| Command | Durable evidence |
|---|---|
| `git show --stat 9562bf47fa2987c7108fbb40ffb07817d185c985` | Identifies the committed source tree that contains the remediation. |
| `AUTOSKILLIT_TEST_FILTER=conservative AUTOSKILLIT_TEST_BASE_REF=develop task test-filtered` | Executes the repository-owned deterministic matrix and its affected test closure. |
| `AUTOSKILLIT_TEST_FILTER=conservative AUTOSKILLIT_TEST_BASE_REF=develop task test-check` | Runs the automation-facing PASS/FAIL gate against the committed implementation. |
| `pre-commit run --all-files` | Runs the repository-owned formatting, typing, lock, documentation, architecture, conflict, and secret-scanning guards. |
