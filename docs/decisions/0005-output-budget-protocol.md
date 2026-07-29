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
   slice. The ordinary outer-result limit remains the backstop for hook-failure paths.*
4. **Producer-aware discipline and derived transport ceiling:** one evidence-output
   policy is delivered on backend surfaces that support it, while Codex's stored
   tool/function output receives a derived damage ceiling. The policy is advisory;
   the ceiling is downstream containment. Neither is cumulative-context accounting.

The four recipe-bearing routes are owned by `RECIPE_DELIVERY_SURFACE_REGISTRY`. Its
canonical digest, together with `RESPONSE_BACKSTOP_EXEMPTION_REGISTRY`, participates in
the delivery contract and probe-cache identity. `finalize_recipe_delivery` persists every
canonical payload as an immutable content-addressed generation, then selects one explicit
mode: ordinary inline, host-attested inline, or a bounded `recipe_pull` envelope. The
registered FastMCP handlers still return exact strings, and the response decorator consumes
the selected decision without applying a second static shaping pass.

## Numeric Limits and Rationale

| Limit | Decision and rationale |
|---|---|
| `load_recipe`: `max_chars = 195_000`, `max_utf8_bytes = 195_000` | The registered 2026-07-22 all-recipe/all-mode measurement identity is `bundled-recipes-all-modes-2026-07-22/load-recipe`. Growth beyond the measured ceiling fails closed to an immutable pull generation. |
| `open_kitchen`: `max_chars = 195_000`, `max_utf8_bytes = 195_000` | The independent registered identity is `bundled-recipes-all-modes-2026-07-22/open-kitchen`; the deferred branch shares the producer but remains a separate delivery surface. |
| `ordinary_omitted_result_token_limit = 10_000` | Conservative outer result for ordinary Codex calls and every untrusted or unsupported recipe request. |
| `authoritative_attested_recipe_result_token_limit = 56_750` | Derived as `((195_000 + 3) // 4) + 8_000`. It is selectable only from protected host evidence for the current call, never from nested arguments or rollout files. |
| `CODEX_HISTORY_RETENTION_TOKEN_LIMIT = 56_750` | Written to upstream `tool_output_token_limit`; controls later stored history and does not select the current outer result. Equality with the attested result limit is intentional but does not merge the authority domains. |
| `CODEX_AUTO_COMPACT_LIMIT = 999_999_999` | Retain the unreachable sentinel and the recovery obligation accepted in [ADR-0004](0004-recipe-redelivery.md). This protocol does not relax recipe-preservation policy. |
| `inline_max_chars = 5_000` | Preserve the previous truncation threshold while changing the representation from destructive clipping to an artifact-backed preview. The configured 2,500-character head and 2,500-character tail retain both diagnostic setup and terminal status; a spill marker is added outside those source slices. |
| `response_max_bytes = 90_000` | Bound the exact compact serialized handler payload before a coarser transport can clip it. Bytes are authoritative here; this is not a token or full JSON-RPC-envelope estimate. |
| `MAX_MCP_OUTPUT_TOKENS = 50_000` | Keep Claude's independently defined setting separate. It has no shared source of truth with Codex result or history limits and does not control Claude Code's observed disk-persistence gate. Claude's native Bash spill behavior covers shell output on that backend. |

The shell capture hook uses `shell_max_inline_bytes = 12_000` as the inline threshold:
commands whose combined output fits within that budget are inlined in full (artifact
retained for one hour after durable finalization); larger outputs are captured losslessly
to the same descriptor-owned artifact with a bounded head/tail slice and provenance
marker inlined. A per-artifact writer lease protects quiet live producers. Every valid
installed runner performs one bounded tail sweep after producer resources release, and
the cleanup-only `capture_lifecycle_hook.py` performs the same sweep at `SessionStart`
for interactive and headless sessions. Eligible artifacts are reclaimed on the next
enabled trusted trigger through identity-revalidated quarantine deletion. Cleanup
failure is fail-open and cannot replace the command's stdout, stderr, or exit status.
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
`response_max_bytes // 3 < ordinary_omitted_result_token_limit`. The three-byte divisor is
deliberate margin: the 90,000-byte response backstop must fire before an ordinary 10,000-token
outer result can clip a producer-blind response. A static test pins the relationship, and the
live large-output probe must pass before either side is retuned.

The measured recipe surfaces must each remain below their registered character and UTF-8
byte ceilings. Their measurements are independent release gates; the heuristic is not
permission to omit those tests or reuse one surface's observed maximum as another domain's
authority.

### Codex Authority Domains

The generated calling contract keeps five values distinct: caller-requested outer tokens,
host-observed requested tokens, derived selected result tokens, required serialized tokens,
and history-retained tokens. Ordinary calls retain the 10,000-token rule. A full recipe call
may use the exact 56,750-token pragma only when a protected host channel supplies the
immutable `RecipeDeliveryRequest`; otherwise the request is omitted and the response uses
the bounded pull path. The `recipe://{name}` resource is never negotiation-eligible.

## Corrections of Record

Commit `6b421e38e` introduced the `_codex_config.py` comment framing
`tool_output_token_limit` as a per-MCP-tool response budget sized for `open_kitchen`.
That framing was incorrect. The Codex setting governs tool/function output retained in
later context, including native shell, `unified_exec`, and MCP output. It is a history
damage bound, not the current call's outer result selector, and does not make
`open_kitchen` lossless.

[PR #4259](https://github.com/TalonT-Org/AutoSkillit/pull/4259) included
`Closes #4253`, but GitHub did not auto-close the issue because the PR merged into
`develop`, not the repository's default branch, `main`. Closure must therefore be
recorded explicitly in the issue body.

Issue #4369 re-verified both governed limits against upstream `rust-v0.145.0` (see
Forward Obligations, below). Two claims in the "Numeric Limits and Rationale" table
above are superseded by that re-verification: `CODEX_HISTORY_RETENTION_TOKEN_LIMIT`'s
"does not select the current outer result" framing incorrectly implied `tool_output_token_limit`
only governs later-stored history — it also governs the current turn's tool output,
per `CODEX_LIMIT_VERIFICATION_REGISTRY["CODEX_HISTORY_RETENTION_TOKEN_LIMIT"]`
(`execution/backends/_codex_config.py`); and `CODEX_AUTO_COMPACT_LIMIT`'s "unreachable
sentinel" framing no longer holds, since `ModelInfo::auto_compact_token_limit()` clamps
it to 90% of the resolved context window (244,800 for gpt-5.6-sol), per
`CODEX_LIMIT_VERIFICATION_REGISTRY["CODEX_AUTO_COMPACT_LIMIT"]`.

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
7. A durable per-thread receipt limits cumulative context insertion by preventing more than
   one oversized attested insertion across normal, deferred, load, and resource routes.
   Repeated or changed recipes use pull.
8. Closed for the `run_cmd` channel by #4286 (capture files promoted in place; only
   bounded slices enter worker memory). Still open for `run_skill` and `test_check`,
   whose adjudication requires the full text.

## Resolved

- **Scalar budget coupling:** general responses now use
  `BackendCapabilities.unnegotiated_tool_result_token_limit`; recipe responses carry an
  explicit decision through final enforcement. History retention is never read as the
  selected outer result.

### Recipe-section byte budget

`RECIPE_SECTION_MANDATORY_FAILURE_CODES` defines the exact compact code-only failures,
and `RECIPE_SECTION_RESPONSE_FLOOR_BYTES` is their maximum UTF-8 size. Configuration
below that floor is invalid. After request admission, the request-specific ceiling is:

`recipe_section_bound_bytes = min(response_max_bytes, conservative_general_result_limit)`

The current ordinary Codex policy deliberately keeps the conservative limit at
10,000 bytes. It is not the generic token×4 projection. Planning trial-renders each
complete outer response with compact canonical JSON and accepts only pages within the
captured UTF-8 bound. Oversized arrays use complete pages or
`json-element-fragment`; there is no truncation and no dropped element. A terminal
page omits `next_part`.

## Operational Signals

Output-budget instrumentation uses low-cardinality structured counters or events for:

- spill count by producer/tool class;
- original and artifact UTF-8 byte totals;
- measured exemption use; and
- spill failures grouped by bounded cause code;
- recipe delivery decision mode and bounded reason; and
- receipt reservation outcomes without thread, call, path, or payload identities.

Signals must never contain artifact paths, hashes, or output content. This decision does
not introduce an artifact quota or current-context token accounting, so instrumentation
must not claim those mechanisms exist. The one-high-insertion receipt is cumulative
insertion control, not a measurement of remaining context.

## Forward Obligations

- Re-measure both registered raw-response ceilings after CLI upgrades.
- Any ceiling relaxation, command-guard rule removal, response-backstop exemption
  addition, or output-discipline policy-version change invalidates the applicable cached
  capability probe.
- Run and pass the live large-output probe before making any of those changes effective.
- Preserve ADR-0004's end-to-end recipe re-delivery obligation if the
  999,999,999 auto-compaction sentinel is ever relaxed. Re-delivery is implemented
  as re-sending the envelope (when the original response was an envelope) and
  pulling each step body via `get_recipe_section(section=<step_name>)`, chunked via
  `part` / `has_more` / `next_part` for oversized sections — not as a replay of
  the full raw payload. Reconciles with the ADR-0004 cross-reference amendment.
- After each codex-cli upgrade, re-verify the result-limit parser, history-retention setting
  (`CODEX_HISTORY_RETENTION_TOKEN_LIMIT`), and auto-compact sentinel
  (`CODEX_AUTO_COMPACT_LIMIT`) against the upstream registry AND observed session
  windows, then bump `CODEX_LIMITS_LAST_VERIFIED_VERSION`. Doctor Check 39
  (`codex_limits_verified`) mechanizes the reminder. `CODEX_LIMIT_VERIFICATION_REGISTRY`
  is the durable, machine-readable record of what was checked and found;
  `CODEX_LIMITS_LAST_VERIFIED_VERSION` is derived from it as the minimum
  `checked_at_cli_version` across its entries. At codex-cli 0.145.0,
  `CODEX_AUTO_COMPACT_LIMIT` was found neutralized upstream:
  `ModelInfo::auto_compact_token_limit()` clamps it to 90% of the resolved context
  window, an effective threshold of 244,800 for gpt-5.6-sol. Issue #4280's investigation
  found upstream models.json (post-0.144.1) listing gpt-5.6-sol at 372,000 tokens
  vs 258,400 in cli 0.144.1, but the effective window is server/catalog-controlled
  and oscillated during July 2026 (openai/codex#31860, #32806) — re-verify, do not
  assume an upgrade restores headroom.
- openai/codex#25458 / #27830: `fork_turns "none"` task-envelope delivery bug — until
  the upstream bug is fixed, `codex --json` sessions cannot reliably deliver
  task-envelope context to sub-agents, so the intake discipline requires sub-agents to
  be spawned with `fork_turns "none"` passed explicitly and given an explicit narrow
  brief; sub-agents return a summary of their own task work, not raw file contents,
  and must not be delegated the reading or interpretation of the caller's instruction
  files.
- openai/codex#33881: agent-TOML `model`/`model_reasoning_effort` reportedly ignored
  on 0.144.5 — affects the pins `_generate_agent_tomls` writes.
- Upstream auto-compact semantics: `model_auto_compact_token_limit` is consulted
  directly only under the non-default `BodyAfterPrefix` scope; the default scope is
  `Total`, under which the trigger uses `ModelInfo::auto_compact_token_limit()`, which
  clamps the configured value to 90% of the resolved context window. The practical
  mechanism is the clamp, not a separate trigger — re-verify `CODEX_AUTO_COMPACT_LIMIT`'s
  disabling effect per upgrade.

## Consequences

- Ordinary large responses become lossless artifacts with bounded inline evidence.
- High-confidence unbounded shell calls are refused before their output is produced.
- Recipe and ordinary result decisions are explicit and independent from history retention.
- Artifact/invariant failures become explicit bounded errors instead of context floods.
- The accepted gaps above remain visible work rather than implicit guarantees.
- The Codex context-intake policy (#4351) is advisory prose with no runtime gate, so it
  is governed by the evidence-bound `CODEX_INTAKE_RULES` registry rather than by
  `INVARIANT_REGISTRY`, which maps prohibitions to runtime enforcement targets.
- The intake digest is deliberately excluded from `PROBE_POLICY_IDENTITY`. Wiring it in
  would make every intake-prose edit probe-gated under this ADR's Forward Obligations,
  a real recurring operational cost; excluding it keeps that a conscious future decision
  rather than a side effect of this change.
