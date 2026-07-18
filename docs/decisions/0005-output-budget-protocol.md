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
3. **Pre-spend command guard:** *Retired by ADR-0006 (#4286).* The command-shape
   classifier and its `output_budget_guard` are deleted. Codex native shell is now
   bounded by a PreToolUse input-rewrite hook (`shell_capture_hook`) that captures
   complete output to a mechanism-owned artifact and emits only a bounded inline
   slice. The transport ceiling (CODEX_TOOL_OUTPUT_TOKEN_LIMIT) remains as the
   backstop for hook-failure paths.*
4. **Producer-aware discipline and derived transport ceiling:** one evidence-output
   policy is delivered on backend surfaces that support it, while Codex's stored
   tool/function output receives a derived damage ceiling. The policy is advisory;
   the ceiling is downstream containment. Neither is cumulative-context accounting.

The raw-text `open_kitchen` and `load_recipe` responses are measured exemptions from the
universal backstop. `RESPONSE_BACKSTOP_EXEMPTION_REGISTRY` is the closed authority for
their independent character ceiling, UTF-8 byte ceiling, and measurement identity. Its
canonical digest is carried in tool metadata and probe-cache identity. Adding or relaxing
an exemption requires re-measurement and a deliberate registry-digest change.

## Numeric Limits and Rationale

| Limit | Decision and rationale |
|---|---|
| `load_recipe`: `max_chars = 185_000`, `max_utf8_bytes = 185_000` | The 2026-07-16 independent all-recipe/all-mode pre-backstop measurement reached 183,103 characters and UTF-8 bytes for `remediation` with all truthy ingredients. The 1,897-unit margin makes serving growth explicit. Measurement identity: `bundled-recipes-all-modes-2026-07-16/load-recipe`. |
| `open_kitchen`: `max_chars = 186_000`, `max_utf8_bytes = 186_000` | The matching current-version pre-backstop measurement reached 183,103 characters and UTF-8 bytes for `remediation` with all truthy ingredients. The 2,897-unit margin covers the open-kitchen routing fields without conflating this handler ceiling with the smaller formatted presentation. Measurement identity: `bundled-recipes-all-modes-2026-07-16/open-kitchen`. |
| `CODEX_TOOL_OUTPUT_TOKEN_LIMIT = 54_500` | Derive it from the largest registered exemption as `((186_000 + 3) // 4) + 8_000`: 46,500 tokens under the current client's four-byte heuristic, plus 8,000 tokens for serialized-payload headroom. It is a blast-radius damage bound, not the mechanism that makes evidence lossless. |
| `CODEX_AUTO_COMPACT_LIMIT = 999_999_999` | Retain the unreachable sentinel and the recovery obligation accepted in [ADR-0004](0004-recipe-redelivery.md). This protocol does not relax recipe-preservation policy. |
| `inline_max_chars = 5_000` | Preserve the previous truncation threshold while changing the representation from destructive clipping to an artifact-backed preview. The configured 2,500-character head and 2,500-character tail retain both diagnostic setup and terminal status; a spill marker is added outside those source slices. |
| `response_max_bytes = 90_000` | Bound the exact compact serialized handler payload before a coarser transport can clip it. Bytes are authoritative here; this is not a token or full JSON-RPC-envelope estimate. |
| `MAX_MCP_OUTPUT_TOKENS = 50_000` | Keep Claude's independently defined setting separate. It has no shared source of truth with `CODEX_TOOL_OUTPUT_TOKEN_LIMIT` and does not control Claude Code's observed disk-persistence gate. Claude's native Bash spill behavior covers shell output on that backend. |

The shell capture hook uses `shell_max_inline_bytes = 12_000` as the inline threshold:
commands whose combined output fits within that budget are inlined in full (artifact
deleted); larger outputs are captured losslessly to a mechanism-owned artifact with a
bounded head/tail slice and provenance marker inlined.
`small_file_max_bytes` was removed — it existed solely for the classifier's
literal-small-JSONL exception, which is no longer needed.

## Ceiling and Backstop Reconciliation

Codex CLI 0.144.1 uses `APPROX_BYTES_PER_TOKEN = 4` and integer byte arithmetic in
[`codex-rs/utils/string/src/truncate.rs` at tag `rust-v0.144.1`, commit
`44918ea10c0f99151c6710411b4322c2f5c96bea`](https://github.com/openai/codex/blob/44918ea10c0f99151c6710411b4322c2f5c96bea/codex-rs/utils/string/src/truncate.rs).
This is a coarse one-token-per-four-UTF-8-bytes truncation heuristic. It performs no real
tokenization and provides neither a tokenizer guarantee nor a cumulative-context
estimate.

The project therefore requires the stricter relationship
`response_max_bytes // 3 < CODEX_TOOL_OUTPUT_TOKEN_LIMIT`. The three-byte divisor is
deliberate margin: the 90,000-byte response backstop must fire before Codex's 54,500-token
transport ceiling can clip a producer-blind response. A static test pins the relationship,
and the live large-output probe must pass before either side is retuned.

The measured raw-text exemptions, `open_kitchen` and `load_recipe`, must each remain below
their own registered character and UTF-8 byte ceilings, which in turn remain below the
218,000-byte budget implied by the current 54,500-token, four-byte heuristic. Their
measurements are independent release gates; the heuristic is not permission to omit those
tests or reuse one surface's observed maximum as the other's authority.

### Per-Repo Ceiling Guidance

The global `~/.codex/config.toml` `tool_output_token_limit` (54,500, written by
`autoskillit init` via `ensure_codex_mcp_registered`) is intentionally sized for
autoskillit kitchen sessions (the `open_kitchen` exemption ceiling above). For
non-autoskillit repos, two lower-ceiling launch paths cap casual reads at ~40 KB
(10,000 tokens × 4 bytes/token):

1. **Per-invocation**: `codex -c tool_output_token_limit=10000`
2. **Per-project `CODEX_HOME`**: `export CODEX_HOME=<project>/.codex` with its own
   `config.toml` containing `tool_output_token_limit = 10000`

The ceiling clamps regular-path exec/tool output via
`min(model request, tool_output_token_limit)`, but code-mode models (gpt-5.6-sol)
honor model-declared `max_output_tokens` unclamped — for those sessions the ceiling
is not a hard cap and the intake discipline digest's numeric rule
(`max_output_tokens` <= 10000) is the operative bound.

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

1. Non-JSONL single-file searches and arbitrary shell verbs (`python -c` file dumps,
   `git log -p`, `curl`) are now captured losslessly on Codex by the shell capture hook
   (#4286 / ADR-0006). The transport ceiling remains the backstop for hook-failure paths.
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
8. Closed for the `run_cmd` channel by #4286 (capture files promoted in place; only
   bounded slices enter worker memory). Still open for `run_skill` and `test_check`,
   whose adjudication requires the full text.

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

- Re-measure both registered raw-response ceilings after CLI upgrades.
- Any ceiling relaxation, command-guard rule removal, response-backstop exemption
  addition, or output-discipline policy-version change invalidates the applicable cached
  capability probe.
- Run and pass the live large-output probe before making any of those changes effective.
- Preserve ADR-0004's end-to-end `load_recipe` re-delivery obligation if the
  999,999,999 auto-compaction sentinel is ever relaxed.
- After each codex-cli upgrade, re-verify the truncation heuristic
  (`CODEX_TOOL_OUTPUT_TOKEN_LIMIT`) and auto-compact sentinel
  (`CODEX_AUTO_COMPACT_LIMIT`) against the upstream registry AND observed session
  windows, then bump `CODEX_LIMITS_LAST_VERIFIED_VERSION`. Doctor Check 39
  (`codex_limits_verified`) mechanizes the reminder. Issue #4280's investigation
  found upstream models.json (post-0.144.1) listing gpt-5.6-sol at 372,000 tokens
  vs 258,400 in cli 0.144.1, but the effective window is server/catalog-controlled
  and oscillated during July 2026 (openai/codex#31860, #32806) — re-verify, do not
  assume an upgrade restores headroom.
- openai/codex#25458 / #27830: `fork_turns "none"` task-envelope delivery bug gates
  the intake digest's sub-agent spawn rule — until the upstream bug is fixed,
  `codex --json` sessions cannot reliably deliver task-envelope context to
  sub-agents, so the intake discipline's "do not spawn sub-agents" guard remains
  the operative constraint.
- openai/codex#33881: agent-TOML `model`/`model_reasoning_effort` reportedly ignored
  on 0.144.5 — affects the pins `_generate_agent_tomls` writes.
- Upstream auto-compact semantics are scope-dependent
  (`model_auto_compact_token_limit` is consulted only under `BodyAfterPrefix` scope;
  a separate full-context-window trigger ignores it) — re-verify
  `CODEX_AUTO_COMPACT_LIMIT`'s disabling effect per upgrade.

## Consequences

- Ordinary large responses become lossless artifacts with bounded inline evidence.
- High-confidence unbounded shell calls are refused before their output is produced.
- Transport limits are derived from a measured control-plane payload instead of an
  unrelated generous constant.
- Artifact/invariant failures become explicit bounded errors instead of context floods.
- The accepted gaps above remain visible work rather than implicit guarantees.
