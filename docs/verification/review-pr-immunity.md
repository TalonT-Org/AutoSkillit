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

## Worktree verification evidence

Verification ran on 2026-07-23 (America/Los_Angeles) in the feature worktree at
`HEAD=8c4a4ff925077139516cc22faa37bd1a583a3323`, with the remediation represented by
the uncommitted working-tree diff and `develop` used as the configured base ref.

| Command | Outcome | Saved evidence |
|---|---|---|
| `pre-commit run --all-files` | PASS; Ruff format/check, mypy, lock validation, documentation and architecture guards, merge-conflict check, and gitleaks passed | `.autoskillit/temp/retry-worktree/third-pre-commit.txt` |
| `AUTOSKILLIT_TEST_FILTER=conservative AUTOSKILLIT_TEST_BASE_REF=develop task test-filtered` | PASS; 28,610 passed, 2,508 skipped, 28 xfailed | `.autoskillit/temp/retry-worktree/second-test-filtered-rerun.txt` |
| `AUTOSKILLIT_TEST_FILTER=conservative AUTOSKILLIT_TEST_BASE_REF=develop task test-check` | PASS; 28,610 passed, 2,508 skipped, 28 xfailed | `.autoskillit/temp/retry-worktree/second-test-check.txt` |
| `AUTOSKILLIT_TEST_FILTER=conservative AUTOSKILLIT_TEST_BASE_REF=develop task test-all` | PASS; 11 import contracts kept, 0 broken; 28,610 passed, 2,508 skipped, 28 xfailed | `.autoskillit/temp/retry-worktree/second-test-all.txt` |
