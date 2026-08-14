# ADR-0004: Codex Recipe Knowledge Re-Delivery via load_recipe

**Status:** Accepted
**Date:** 2026-06-27
**Issue:** [#4051](https://github.com/TalonT-Org/AutoSkillit/issues/4051)

## Context

Codex auto-compaction fires at 90% of the 258K context window. When triggered, it can
destroy recipe content delivered by `open_kitchen` in the initial orchestrator response.
After compaction, the agent loses the recipe steps it needs to complete the pipeline.

Two defenses exist:

1. **Primary — disable compaction**: `ensure_codex_mcp_registered` writes
   `model_auto_compact_token_limit = 999_999_999` (an unreachable threshold) to
   `~/.codex/config.toml`. `setup_session_dir` copies this config into each session
   directory, ensuring every headless session inherits the setting. *Re-verified at
   codex-cli 0.145.0 and found neutralized upstream — see "Re-verification" below.*

2. **Guard — block raw recipe reads**: The `recipe_read_guard.py` PreToolUse hook
   blocks `run_cmd`/`Bash` from reading recipe YAML, SKILL.md, or agent definition
   files, and blocks `run_python` from calling `autoskillit.recipe.*` callables.
   This prevents the agent from self-recovering from compaction loss via raw file
   access — compaction loss is treated as a hard failure, not a recoverable state.

If the primary defense is ever relaxed (e.g., Codex fixes its compaction behavior or
the context window grows), the agent needs a sanctioned channel to re-acquire recipe
knowledge without reading raw files.

## Decision

> **`load_recipe` / `open_kitchen` (the MCP tools exposed by `server/tools/tools_recipe.py`)
> remain the sanctioned entry points for recipe knowledge re-delivery after context
> compaction. When the response is a bounded envelope (oversized recipes exceeding the
> delivery bound — see ADR-0005), full re-delivery is completed by pulling each step via
> `get_recipe_section` (chunked via `part` / `has_more` / `next_part` for oversized
> sections). Both remain the only channels not blocked by `recipe_read_guard`'s
> deny-list on raw `run_cmd`/`Bash`/`run_python` recipe access — `recipe_read_guard.py`
> has no allow-list keyed on tool name; it blocks the raw-access alternatives rather
> than allow-listing `load_recipe`/`open_kitchen`/`get_recipe_section` by name.**

If recipe content is lost, the agent must call `load_recipe` (or `open_kitchen`) to
re-acquire it; if the response is a bounded envelope, follow up by calling
`get_recipe_section(section=<step_name>)` to pull each step body. The `recipe_read_guard`
deny-list on raw `run_cmd`/`Bash`/`run_python` recipe access remains the operative
constraint — raw file access is denied; the MCP tools above are the unblocked paths.

### Progressive delivery for long-running recipes

`implementation` and `remediation` opt into checkpoint-gated delivery. Their immutable
artifact still persists the complete canonical, post-prune recipe, flow, and execution
snapshot, while the public `open_kitchen` response contains only the segment overview and
initial bodies. Each mapped checkpoint tool verifies READY against that exact durable
generation before its effect and then returns `recipe_segment` with either authenticated
future bodies or an ordered manual `recipe_pull` closure. No raw YAML, LRU lookup, runtime
skip carrier, or separately persisted projection participates in recovery.

Acknowledged `run_skill` receipts are replayable only for the same kitchen, request
session, and receipt identity. Their tracker effect remains one-shot and cached, so a
response retry can reproduce the segment without crediting progress twice. A post-effect
delivery failure carries the selected recovery authority and explicitly forbids repeating
the operation.

This is a forward obligation: when the primary defense (unreachable auto-compact limit)
is relaxed, the `load_recipe` / `open_kitchen` / `get_recipe_section` end-to-end
recovery path — including envelope re-delivery plus per-step pulls — must be tested as
the recovery path.

### Schema-driven pull continuation

The fixed pullable sections are `content`, `ingredients_table`, `orchestration_rules`,
`stop_step_semantics`, `errors`, and `warnings`; a validated post-prune step is a
separate dynamic raw-YAML definition. Every page pins `pagination_version`,
`section_registry_sha256`, `section_sha256`, and `page_plan_sha256`.

The four exhaustive reconstruction algorithms are:

- `raw-text`: verify contiguous UTF-8 byte ranges and concatenate content.
- `json-array-page`: run `json.loads` on every complete array page and extend in order.
- `json-scalar-page`: run `json.loads` on every string page and concatenate decoded text.
- `json-element-fragment`: decode string fragments, concatenate one canonical element,
  verify `element_sha256`, then parse that element once.

Consumers reject an unknown pagination version or unknown content format, mixed
identities, gaps, overlaps, duplicates, a page after the terminal page, or a terminal
page carrying `next_part`. They must not guess or repair a malformed continuation.

### Terminal pages and embedded completion receipts

When the server has a verified host attestation with annotation support and the
payload fits within the annotation ceiling, the recipe is delivered inline in a
single `open_kitchen` call without pagination. Terminal-page invariants apply
only to the ENVELOPE fallback path.

The last required `get_recipe_section` page may carry the deterministic completion
receipt and recipe-execution credential. The page credit and transition to READY occur
only after the universal response boundary preserves the exact page bytes. When the
receipt is present, consumers skip `complete_recipe_initialization`; otherwise the
separate completion call remains the fallback.

Four invariants are non-negotiable:

1. **Server-state-only derivation.** Receipts contain no client nonce. The server caches
   the exact terminal response by `(initialization_id, part_index)` for 24 hours, so an
   exact re-fetch returns byte-identical receipt and execution fields.
2. **Atomic credit and transition.** Page credit and the transition to `ReadyRecipe`
   occur under one lock with one final state assignment. Splitting these writes would
   reintroduce the dual-call race.
3. **Content hash binding.** Receipt derivation includes the terminal page's
   `page_content_sha256`; a replay with different content is rejected before any state
   transition.
4. **Full READY authority.** The receipt carries recipe, immutable artifact and flow
   identities plus the complete recipe-execution credential. Together with the installed
   snapshot and audit-ledger occurrence, this reconstructs the attested READY context
   rather than representing a standalone done flag.

## Consequences

- `recipe_read_guard.py` error messages direct the agent to call `load_recipe` (and,
  for envelope responses, `get_recipe_section`).
- Terminal-page delivery enforces the four invariants above while preserving the
  separate completion call as a compatibility fallback for non-receipt responses.
- Segmented recipes recover from the latest `recipe_segment`; the startup credential is
  intentionally scoped to delivered bodies rather than the full future recipe.
- Future work relaxing `CODEX_AUTO_COMPACT_LIMIT` must add integration tests verifying
  `load_recipe` / `open_kitchen` / `get_recipe_section` re-delivery restores full
  pipeline execution capability, including envelope re-delivery plus per-step pulls.
- `test_snapshotted_config_has_auto_compact_limit` validates the primary defense path:
  `setup_session_dir` preserves the override in the copied `config.toml`.

## Re-verification at codex-cli 0.145.0 (2026-07-26)

Issue #4369 re-verified the primary defense against upstream `rust-v0.145.0`
(commit `25af12f7e61572b0bc18ddb1008be543b91519b0`). `ModelInfo::auto_compact_token_limit()`
clamps the configured `model_auto_compact_token_limit` to 90% of the resolved context
window; for gpt-5.6-sol (`resolved_context_window = 272_000`) the clamped, effective
threshold is **244,800** — not the configured `999_999_999`. **`CODEX_AUTO_COMPACT_LIMIT`
is measurably neutralized upstream at this CLI version**: 34 compaction events were
observed under 0.145.0. The forward obligation recorded above — testing the
`load_recipe` / `open_kitchen` / `get_recipe_section` end-to-end recovery path — is
therefore live, not hypothetical, and is owned by #4271. The finding is recorded
machine-readably in `CODEX_LIMIT_VERIFICATION_REGISTRY["CODEX_AUTO_COMPACT_LIMIT"]`
(`execution/backends/_codex_config.py`).
