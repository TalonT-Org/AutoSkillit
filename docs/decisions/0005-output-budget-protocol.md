# ADR-0005: Output Budget Protocol

**Status:** Accepted
**Date:** 2026-07-15
**Issue:** [#4272](https://github.com/TalonT-Org/AutoSkillit/issues/4272)

## Context

An investigation of a Codex session exhausted the orchestrator's context after two
commands returned large, mostly irrelevant bodies. The model had no enforceable
per-response budget before the commands ran, at the producing tools, or at the common
MCP response boundary. This is the recurring failure class behind
[#4253](https://github.com/TalonT-Org/AutoSkillit/issues/4253),
[#2819](https://github.com/TalonT-Org/AutoSkillit/issues/2819),
[#2564](https://github.com/TalonT-Org/AutoSkillit/issues/2564), and
[#3938](https://github.com/TalonT-Org/AutoSkillit/issues/3938): a useful control-plane
signal is carried beside unbounded evidence, and a transport or context limit removes
the information needed to continue.

The system has two competing masters. `open_kitchen` must retain enough transport
headroom to deliver the complete recipe and terminal sentinel. Native shell and tool
responses must have a low enough containment ceiling that one response cannot consume
an excessive share of context. Treating either requirement as the only authority makes
the other path unsafe.

## Decision

AutoSkillit adopts a four-layer Output Budget Protocol. The first applicable layer owns
containment; downstream layers are independent backstops rather than substitutes.

1. **Lossless source shaping:** output-aware producers spill the complete output to an
   atomically published project artifact and return a bounded head/tail summary. This is
   producer-side, post-execution enforcement.
2. **Universal response backstop:** the common MCP response boundary measures the final
   handler representation and uses routing- and shape-preserving projection. This is
   producer-blind, post-execution enforcement. A response that cannot be persisted or
   projected safely fails closed without returning the original payload.
3. **Pre-spend command guard:** enumerated high-confidence unbounded shell shapes are
   denied before execution and receive a bounded-rewrite instruction. This is static,
   pre-execution enforcement for rules R1 through R3.
4. **Producer-aware discipline and derived transport ceiling:** one evidence-output
   policy is delivered on backend surfaces that support it, while Codex's stored
   tool/function output receives a derived damage ceiling. The policy is advisory;
   the ceiling is downstream containment. Neither is cumulative-context accounting.

The raw-text `open_kitchen` and `load_recipe` responses are measured exemptions from the
universal backstop. Adding an exemption requires its own measured-budget test.

## Numeric Limits and Rationale

| Limit | Decision and rationale |
|---|---|
| `OPEN_KITCHEN_OUTPUT_BUDGET_BYTES = 96_000` | This is a stable regression ceiling, not an estimate of the external gate. The 2026-07-15 maximum canonical rendering was 95,771 UTF-8 bytes for `remediation` with all truthy ingredients. The 229-byte project margin keeps growth explicit, while the payload remains roughly 4 KB below the last observed external persistence gate near 100 KB. Re-measure after CLI upgrades instead of silently raising the constant. |
| `CODEX_TOOL_OUTPUT_TOKEN_LIMIT = 32_000` | Derive it as `((96_000 + 3) // 4) + 8_000`: 24,000 tokens under the current client's four-byte heuristic, plus 8,000 tokens (32 KB under that heuristic) for serialized-payload headroom. It is a blast-radius damage bound, not the mechanism that makes evidence lossless. |
| `CODEX_AUTO_COMPACT_LIMIT = 999_999_999` | Retain the unreachable sentinel and the recovery obligation accepted in [ADR-0004](0004-recipe-redelivery.md). This protocol does not relax recipe-preservation policy. |
| `inline_max_chars = 5_000` | Preserve the previous truncation threshold while changing the representation from destructive clipping to an artifact-backed preview. The configured 2,500-character head and 2,500-character tail retain both diagnostic setup and terminal status; a spill marker is added outside those source slices. |
| `response_max_bytes = 90_000` | Bound the exact compact serialized handler payload before a coarser transport can clip it. Bytes are authoritative here; this is not a token or full JSON-RPC-envelope estimate. |
| `MAX_MCP_OUTPUT_TOKENS = 50_000` | Keep Claude's independently defined setting separate. It has no shared source of truth with `CODEX_TOOL_OUTPUT_TOKEN_LIMIT` and does not control Claude Code's observed disk-persistence gate. Claude's native Bash spill behavior covers shell output on that backend. |

The command guard also reads `small_file_max_bytes = 5_000` for its narrow literal-JSONL
exception and accepts proven byte sinks only through `shell_max_inline_bytes = 12_000`.
Those are classification bounds, not transport limits. Parser limits of 65,536 command
characters and nesting depth 8 bound classification work; exceeding them produces an
unknown disposition and cannot authorize a risky R1-R3 command.

## Ceiling and Backstop Reconciliation

Codex CLI 0.144.1 uses `APPROX_BYTES_PER_TOKEN = 4` and integer byte arithmetic in
[`codex-rs/utils/string/src/truncate.rs` at tag `rust-v0.144.1`, commit
`44918ea10c0f99151c6710411b4322c2f5c96bea`](https://github.com/openai/codex/blob/44918ea10c0f99151c6710411b4322c2f5c96bea/codex-rs/utils/string/src/truncate.rs).
This is a coarse one-token-per-four-UTF-8-bytes truncation heuristic. It performs no real
tokenization and provides neither a tokenizer guarantee nor a cumulative-context
estimate.

The project therefore requires the stricter relationship
`response_max_bytes // 3 < CODEX_TOOL_OUTPUT_TOKEN_LIMIT`. The three-byte divisor is
deliberate margin: the 90,000-byte response backstop must fire before Codex's 32,000-token
transport ceiling can clip a producer-blind response. A static test pins the relationship,
and the live large-output probe must pass before either side is retuned.

The measured raw-text exemptions, `open_kitchen` and `load_recipe`, must each remain below
the 128,000-byte budget implied by the current 32,000-token, four-byte heuristic. Their
measurements are independent release gates; the heuristic is not permission to omit
those tests.

## Corrections of Record

Commit `6b421e38e` introduced the `_codex_config.py` comment framing
`tool_output_token_limit` as a per-MCP-tool response budget sized for `open_kitchen`.
That framing was incorrect. The Codex setting governs tool/function output stored in
context, including native shell, `unified_exec`, and MCP output. It is a global damage
ceiling and does not make `open_kitchen` lossless.

[PR #4259](https://github.com/TalonT-Org/AutoSkillit/pull/4259) included
`Closes #4253`, but GitHub did not auto-close the issue because the PR merged into
`develop`, not the repository's default branch, `main`. Closure must therefore be
recorded explicitly in the issue body.

## Accepted Gaps

1. Non-JSONL single-file searches and shell verbs outside R1-R3, including `python -c`
   file dumps, `git log -p`, and `curl`, are bounded on Codex only by the derived ceiling.
   Codex may still truncate and discard their excess output.
2. The interactive discipline digest does not survive Codex `resume` and cannot
   guarantee post-compaction reinjection. Those paths retain the command guard, transport
   ceilings, and file-materialized skill content.
3. If tool context or artifact persistence is unavailable, the response backstop fails
   closed with a bounded explicit error. The original response is unavailable to the
   caller but never enters model context.
4. Prompt-side caps for GitHub issue/comment embedding, `resume_message`, and
   `attempt_history` are outside this tool-output protocol.
5. `merge_worktree` drops passing raw test output at the server boundary by design.
6. Routing- and shape-preserving projections retain control-plane keys and value types
   and place complete domain data in the artifact. A caller needing pruned collection
   members must retrieve bounded slices from that artifact.
7. Repeated individually bounded calls can still exhaust cumulative context. No pre-call
   component owns current context usage, so reserve instructions remain advisory.
8. Subprocess output is still materialized in worker memory before model-context shaping.
   The protocol does not bound worker memory or temporary-disk consumption.

## Operational Signals

Output-budget instrumentation uses low-cardinality structured counters or events for:

- spill count by producer/tool class;
- original and artifact UTF-8 byte totals;
- measured exemption use; and
- spill failures grouped by bounded cause code.

Signals must never contain artifact paths, hashes, or output content. This decision does
not introduce artifact quota, artifact cleanup, cumulative reserve accounting, or
reserve-trigger metrics, so instrumentation must not claim those mechanisms exist.

## Forward Obligations

- Re-measure the 96,000-byte empirical `open_kitchen` bound after CLI upgrades.
- Any ceiling relaxation, command-guard rule removal, response-backstop exemption
  addition, or output-discipline policy-version change invalidates the applicable cached
  capability probe.
- Run and pass the live large-output probe before making any of those changes effective.
- Preserve ADR-0004's end-to-end `load_recipe` re-delivery obligation if the
  999,999,999 auto-compaction sentinel is ever relaxed.

## Consequences

- Ordinary large responses become lossless artifacts with bounded inline evidence.
- High-confidence unbounded shell calls are refused before their output is produced.
- Transport limits are derived from a measured control-plane payload instead of an
  unrelated generous constant.
- Artifact/invariant failures become explicit bounded errors instead of context floods.
- The accepted gaps above remain visible work rather than implicit guarantees.
