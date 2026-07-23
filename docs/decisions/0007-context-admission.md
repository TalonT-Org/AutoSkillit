# ADR-0007: Cumulative Context Admission

**Status:** Accepted

**Date:** 2026-07-23

**Issue:** [#4333](https://github.com/TalonT-Org/AutoSkillit/issues/4333)

**Work package:** C1

## Context

AutoSkillit has lossless spill controls and raw per-result byte limits, but those controls do not
answer the cumulative question: whether the final request assembled for a model still fits the
active context window. Local history insertion, handler JSON, terminal billing usage, rollout
files, byte counts, and content digests each observe a different representation or a different
time. None proves both provider acceptance and remaining token capacity at admission time.

This decision freezes C1's implementation-independent contract before C2-C8 add persistence,
adapters, enforcement, or reuse. Its executable form is
`CONTEXT_ADMISSION_PROTOCOL_VERSION`, the pure `reduce_context_admission` reducer,
`replay_context_admission`, and the immutable `CONTEXT_ADMISSION_COVERAGE` registry.

## Decision

AutoSkillit adopts protocol version 1 as the shared accounting contract for every model-visible
producer. A debit is authorized only from an authoritative window snapshot and an exact,
representation-bound admission chain. Every transition consumes the complete immutable prior
state and returns the complete next state, a typed decision, and content-free publication effects
as one atomic unit.

This ADR defines a contract, not enforcement. When the required authority is absent, the only
valid result is a typed unavailable or upstream-gated decision; local estimates never acquire
authority by being conservative.

## Admission boundary and authority

The model-visible admission boundary is the exact incremental active-context growth of the final
canonical provider-request representation after all shaping, envelopes, spill markers, tool
arguments and results, hook feedback, retrieval, instructions, schemas, context injection, and
ordered batching.

Authority requires all of the following to refer to that same immutable representation and
window epoch:

1. an authoritative remaining-capacity snapshot;
2. an authoritative count for the proposed final input;
3. a binding between that count, the complete representation manifest, and the dispatched
   request; and
4. a durable/queryable provider-acceptance or explicit non-admission witness.

Local history insertion is not request inclusion. Request inclusion is not provider acceptance.
Terminal usage is post-hoc evidence, not admission authority. Bytes are emergency containment,
not token capacity. A digest is integrity metadata, not acceptance, access authority, or
occurrence identity.

## Protocol version 1

Protocol version 1 is identified by `CONTEXT_ADMISSION_PROTOCOL_VERSION = 1`. The static
`CONTEXT_ADMISSION_COVERAGE` registry has exactly one row for every `ProducerSurface`.
`reduce_context_admission(state, event)` is a pure, exhaustive transition function, and
`replay_context_admission(initial_state, events)` deterministically folds a recorded stream.
Neither function performs I/O, persistence, enforcement, response mutation, or estimation.

The lifecycle is:

```text
PROPOSED
  -> RESERVED
  -> PREPARED
  -> HISTORY_STAGED
  -> REQUEST_DISPATCHED
  -> COMMITTED
```

Before acceptance, an explicit non-admission or rollback witness may produce `RELEASED` or
`ROLLED_BACK`. A safe epoch rollover may produce `INVALIDATED` for work that was not dispatched.
An ambiguous crash produces `INDETERMINATE`, while inconsistent stored or provider facts produce
`QUARANTINED`; neither state silently releases capacity.

An uninitialized state has no spendable capacity. Only an authoritative epoch-open snapshot
creates capacity. Commands carry a protocol version, event ID, idempotency namespace, and expected
aggregate revision. Exact event-ID replay and equivalent idempotent retry are resolved before
stale-revision rejection.

## State, witnesses, and atomic batches

One occurrence describes one immutable producer contribution and representation revision. An
`AdmissionBatch` owns a request ID, batch ID, ordered unique members, one reserve class, one
complete `CanonicalRepresentationManifest`, and, for protected work, one authorized pool owner.
The batch is reserved, prepared, staged, dispatched, committed, released, rolled back, invalidated,
or quarantined atomically. A provider's exact whole-batch charge is never invented as independent
per-member charges.

Witnesses are typed and distinct:

| Stage | Required evidence |
|---|---|
| Epoch open | authoritative snapshot sequence, model/tokenizer identity, and window ID/number |
| Input measured | exact counted representation and complete canonical representation manifest |
| Prepared | representation-binding witness for the counted and dispatch revisions |
| History staged | local history-staging witness |
| Request dispatched | exact request-inclusion witness and receiver fence |
| Committed | durable/queryable provider-acceptance witness for the request, batch, revision, epoch, and snapshot |
| Released/rolled back | explicit non-admission or rollback witness |
| Reconciled | authoritative reconciliation or exact output-usage witness |
| Epoch replaced | truncation/compaction replacement attestation and an authoritative new snapshot |

Commit reconciles the reservation and exact authoritative charge in the same capacity
transaction. Timeouts, process exit, missing telemetry, and local rendered equality are not
release witnesses. A deadline may request or escalate reconciliation only.

The reducer's `next_state`, decision, and effects are an atomic publication unit. C2 may persist
that unit with compare-and-swap, but may not split its semantic result.

## Accounting and identity invariants

1. An occurrence ID identifies one immutable descriptor. Reuse with changed lineage, surface,
   epoch, representation, or batch membership is a conflict or corruption.
2. Admission occurrence identity, storage identity, integrity identity, and access authority are
   separate domains. Canonical span IDs are opaque representation-local identities, not hashes.
3. The final manifest owns every model-visible span exactly once. Overlap or omission is rejected;
   spans are provenance and duplicate-insertion controls, not additive token truth.
4. Input-context charge and output-generation allowance are separate domains. Generated-output
   usage, including invisible tokens, reconciles its maximum; text later added to history is
   measured again in the next final input representation.
5. Numeric values enter as authoritative inputs. There are no runtime numeric defaults and no
   byte-to-token conversion.
6. Reservations, committed input, unresolved input, and generation reservations have exactly one
   canonical accounting owner. They cannot be reconstructed from effects or logs.
7. Retry and resume are idempotent only for the same descriptor, representation revision, key, and
   authoritative epoch. The attempt ID is not part of the reservation key.
8. A same-key retry with changed intent returns `CONFLICT` without corrupting valid state. Explicit
   witnessed expiry creates a tombstone; wall-clock age neither expires identity nor releases
   capacity.
9. Forked or child work has distinct lineage and epoch identity. It affects the parent only when
   the parent-visible child delivery is accepted at the parent's admission boundary.
10. Serializable values contain bounded reason codes, counts, versions, timestamps, and opaque
    non-secret IDs only. They never contain captured payloads.

## Protected reserve and epoch isolation

For one authoritative snapshot, protocol v1 computes global unallocated capacity from snapshot
remaining minus committed input, outstanding input reservations, unresolved input charge, and
outstanding generation reservations. Each protected pool separately subtracts its own committed,
outstanding, unresolved, and generation charge from its injected capacity. Ordinary availability
is global unallocated minus all unused protected capacity; a protected owner can spend only the
non-negative unused capacity of its own class, bounded by global unallocated.

Protected `SYNTHESIS` and `FINAL_RESPONSE` pools have explicit capability owners, priority, and
release-witness rules. Version 1 forbids borrowing and mixed-reserve-class batches. Every input and
output reservation for one request has the same owner/class, and total injected protected
capacity cannot exceed authoritative remaining capacity at epoch open. This preserves reserve
without subtracting protected use twice.

A compaction, model, tokenizer, or window change closes the old epoch. Undispatched reservations
are invalidated and cannot transfer. Dispatched or indeterminate work and its conservative charge
remain in immutable closed-epoch audit state. A new identity is not itself a fence: rollover
requires a receiver-validated epoch fence, proof that all old dispatches resolved, or a new
authoritative snapshot that already deducts every unresolved old-epoch charge.

## Producer coverage matrix

The states have these meanings:

- `VERIFIED`: primary source evidence proves the named local observation/control point, but never
  implies a global token watermark.
- `PARTIAL`: a useful observation/control point exists, but the final representation, complete
  identity, or capacity authority is missing.
- `UPSTREAM_GATED`: no local boundary can observe or control the required fact; Codex/provider
  participation is required.

Every authority state is `UPSTREAM_GATED` for the tested configuration. The following is the
complete, version-pinned projection of `CONTEXT_ADMISSION_COVERAGE`; each row has a stable
`COV-<surface>` claim ID and deterministically degrades on version or configuration mismatch.

| Producer surface | Control-point owner | Observation | Authority |
|---|---|---|---|
| `NATIVE_SHELL` | `shell_capture_hook` | `VERIFIED` | `UPSTREAM_GATED` |
| `UNIFIED_EXEC_AND_WRITE_STDIN` | `codex_host` | `PARTIAL` | `UPSTREAM_GATED` |
| `APPLY_PATCH` | `codex_host` | `PARTIAL` | `UPSTREAM_GATED` |
| `AUTOSKILLIT_MCP` | `track_response_size` | `VERIFIED` | `UPSTREAM_GATED` |
| `EXTERNAL_MCP` | `fastmcp_client` | `PARTIAL` | `UPSTREAM_GATED` |
| `AUTOSKILLIT_LOCAL_FUNCTION` | `local_function_dispatch` | `VERIFIED` | `UPSTREAM_GATED` |
| `OTHER_LOCAL_FUNCTION` | `codex_host` | `PARTIAL` | `UPSTREAM_GATED` |
| `MCP_RESOURCE` | `fastmcp_client` | `PARTIAL` | `UPSTREAM_GATED` |
| `CLIENT_PROVIDER_RETRIEVAL` | `codex_host` | `UPSTREAM_GATED` | `UPSTREAM_GATED` |
| `CODE_MODE_AGGREGATE` | `codex_host` | `PARTIAL` | `UPSTREAM_GATED` |
| `HOSTED_SPECIALIZED_TOOL` | `codex_host` | `PARTIAL` | `UPSTREAM_GATED` |
| `HOOK_FEEDBACK` | `hook_registry` | `VERIFIED` | `UPSTREAM_GATED` |
| `TOOL_ARGUMENT` | `final_request_assembler` | `PARTIAL` | `UPSTREAM_GATED` |
| `TOOL_RESULT_ENVELOPE` | `final_request_assembler` | `PARTIAL` | `UPSTREAM_GATED` |
| `USER_PROMPT` | `final_request_assembler` | `PARTIAL` | `UPSTREAM_GATED` |
| `ASSISTANT_OUTPUT_HISTORY` | `final_request_assembler` | `PARTIAL` | `UPSTREAM_GATED` |
| `SKILL_PLUGIN_CONTEXT` | `final_request_assembler` | `PARTIAL` | `UPSTREAM_GATED` |
| `OTHER_CONTEXT_INJECTION` | `final_request_assembler` | `UPSTREAM_GATED` | `UPSTREAM_GATED` |
| `HEADLESS_CHILD_PROMPT` | `headless_prompt_builder` | `VERIFIED` | `UPSTREAM_GATED` |
| `PARENT_VISIBLE_CHILD_DELIVERY` | `child_delivery_receipt` | `VERIFIED` | `UPSTREAM_GATED` |
| `COMPACTION_MODEL_WINDOW_TRANSITION` | `compaction_receiver` | `PARTIAL` | `UPSTREAM_GATED` |

`VERIFIED` rows use AutoSkillit source at revision `ac8f653a00d2`. Codex-backed rows use
codex-cli `0.145.0` at revision `25af12f7e61572b0bc18ddb1008be543b91519b0`.
`CLIENT_PROVIDER_RETRIEVAL` and `OTHER_CONTEXT_INJECTION` are explicitly inference-backed gap
claims, not verified source claims. Static source pins are documentation provenance, not runtime
lineage.

## Authority unavailable and byte ceilings

If the watermark, exact final measurement, receiver fence, or provider witness is absent,
`reduce_context_admission` returns a typed `watermark_unavailable` or `upstream_gated` decision.
Uninitialized and authority-unavailable states are non-spendable. Estimates provide **no numeric**
authorization for cumulative admission or enforcement.

Existing lossless spill controls and raw-byte emergency ceilings remain independent and
unchanged. They continue to bound transport and result risk even when token authority is
unavailable, but cannot authorize a token debit. Likewise, token authority must not weaken any
existing raw per-producer byte ceiling.

## Upstream authority request

Codex/provider support must expose all three parts as one authority contract:

1. An **atomic snapshot/reservation** operation for interceptable input producers, plus a separate
   **generated-output maximum** reservation.
2. A **synchronous blocking** operation on the **final ordered batch**, after every
   transformation, that performs exact measurement and admission using the provider's input-token
   count operation when available. It must bind the result to an immutable
   canonical representation manifest and a receiver fence immediately before provider request
   assembly.
3. A **durable/queryable journal** that records distinct facts for **history staging**, exact
   **request inclusion**, **provider acceptance**, **output-usage reconciliation**, **rollback**,
   **truncation/compaction replacement**, and **authoritative reconciliation**.

The minimum request and event fields are: `request_id`, `batch_id`, **ordered members**,
**reservation IDs**, **thread/turn/agent lineage**, **admission sequence**, **window ID/number**,
**model/tokenizer identity**, **snapshot sequence**, **measurement kind/source**,
**active/hard-limit/remaining/proposed/max-output counts**, **reserve class**, and
**representation revision**. A compaction, model, window, or tokenizer transition additionally
carries old and new identities and before/after authority status.

The OpenAI input-token endpoint can measure one resolved representation but does not reserve
capacity, attest that later mutable conversation state is identical, fence a receiver, or prove
provider acceptance. Post-hoc usage likewise cannot substitute for this three-part contract.

## Privacy and observability

Runtime IDs are random, opaque, non-secret values and are never derived from personal data.
Runtime/audit records and Aggregate telemetry are governed separately by field. Each field below
maps to a concrete protocol dataclass attribute (`protocol_version`, `claim_id`, `reason_code`,
etc.) and is governed by an explicit maximum length/cardinality, retention, access, deletion, and
export rule.

### Runtime/audit fields (per-record, persisted with the session)

| Field | Purpose | Maximum length/cardinality | Retention | Access | Deletion | Export |
|---|---|---|---|---|---|---|
| `protocol_version` | pinned reducer semantics | 1 non-negative integer | session lifetime + audit retention | owning runtime + audit tooling | dropped with the session; ADR header is the static anchor | content-free structured records only |
| `aggregate_revision` / `admission_sequence` | monotonic per-session counters | 64-bit non-negative | session lifetime + audit retention | owning runtime + audit tooling | dropped with the session | content-free structured records only |
| `event_id`, `reservation_id`, `witness_id`, `batch_id`, `request_id`, `reservation_key`, `occurrence_id`, `attempt_id`, `delivery_occurrence_id`, `generation_reservation_id` | identity | opaque 96 ASCII chars (`[A-Za-z0-9_.:-]`); no `-` leading/trailing segments | session lifetime + audit retention | owning runtime + audit tooling | dropped with the session; tombstone keys preserve identity until retention | content-free structured records only |
| `reason_code` (event/decision) | typed provenance | 64 ASCII chars; kebab-case `^[a-z][a-z0-9-]*$`; no `bearer`, `sha256:`, `blake2:`, `content:` prefix | session lifetime + audit retention | owning runtime + audit tooling | dropped with the session | content-free structured records only |
| `count` / `requested_count` / `available_ordinary_count` / `available_protected_count` / `reserved_count` / `committed_input_count` / `unresolved_input_count` / `retained_unresolved_count` / `maximum_allowance` / `exact_terminal_usage` / `injected_count` / `priority` / `predicted_authoritative_maximum` / `active_count` / `hard_limit` / `remaining_count` / `highest_admitted_dispatch_sequence` | exact accounting counts | 64-bit non-negative integer | session lifetime + audit retention | owning runtime + audit tooling | dropped with the session | content-free structured records only |
| `version` / `representation_revision` / `tested_version` / `tested_revision` / `publication_revision` | binding identity | opaque 96 ASCII chars; tested_revision is a pinned git SHA (40 hex chars) | session lifetime + audit retention | owning runtime + audit tooling | dropped with the session; static ADR pins preserve release provenance | content-free structured records only |
| `checked_at` | evidence freshness date | ISO-8601 date (10 ASCII chars: `YYYY-MM-DD`) | coverage record lifetime | maintainers with release-provenance access | dropped with the coverage record | content-free structured records only |
| `witness_ids` / `span_owners` / `occurrence_ids` / `input_reservations` / `generation_reservations` / `protected_pools` / `idempotency_records` / `expired_idempotency_tombstones` / `closed_epochs` / `processed_events` / `occurrence_records` / `batch_records` / `reservations` / `terminal_occurrence_records` / `terminal_reservations` / `processed_event_tombstones` | ordered collection of typed records | bounded by one epoch (≤ 1 active + ≤ N closed audits, each audit ≤ 10⁴ occurrence records) | session lifetime + audit retention | owning runtime + audit tooling | dropped with the session; closed audits survive until audit retention | content-free structured records only |
| `freshness_policy` | typed degradation policy | 128 ASCII chars; literal enum (`verify_on_version_or_configuration_change`, `verify_on_revision_change`, `infer_only`) | coverage record lifetime | maintainers with release-provenance access | dropped with the coverage record | content-free structured records only |
| `verifier`, `configuration_mode`, `backend`, `control_point_owner` | evidence metadata | 64 ASCII chars (verifier, configuration_mode, backend); 96 ASCII chars (control_point_owner); no secrets or paths | coverage record lifetime | maintainers with release-provenance access | dropped with the coverage record | content-free structured records only |

### Lineage and source locator fields (per-record)

| Field | Purpose | Maximum length/cardinality | Retention | Access | Deletion | Export |
|---|---|---|---|---|---|---|
| `root_session_id`, `current_session_id`, `root_agent_id`, `current_agent_id`, `parent_agent_id`, `root_thread_id`, `current_thread_id`, `parent_thread_id`, `fork_occurrence_id`, `turn_id`, `producer_surface`, `producer_instance_id`, `tool_call_id`, `model_item_id`, `dispatch_identity`, `delivery_occurrence_id` | correlate a request, turn, agent, source claim, and authority witness | opaque 96 ASCII chars; `producer_surface` is a closed enum; `dispatch_identity` is a single opaque dispatch ID plus derived sentinels (no per-call secrets) | same as the owning session or coverage record | authorized operators and maintainers | delete runtime lineage with its session; retain static provenance only with the released contract | export only to access-controlled audit channels; never to aggregate telemetry |
| `source_locator` (coverage evidence) | static relative source path under `src/` | 256 ASCII chars; forward-slash relative path only (no `/` or `~` prefix, no `\`); no absolute paths, no home-directory paths, no URLs, or secrets | coverage record lifetime (static) | maintainers with release-provenance access | dropped with the coverage record; release pins remain in the ADR | export only to access-controlled audit channels; never to aggregate telemetry |

### Aggregate telemetry fields (population-level, never per-session)

| Field | Purpose | Maximum length/cardinality | Retention | Access | Deletion | Export |
|---|---|---|---|---|---|---|
| `state`, `reason_code` counters (privacy-only aggregates) | measure coverage state, unavailable decisions, and protocol health | fixed enum dimensions; counter buckets ≤ 10⁴ distinct labels per dimension | configured aggregate-metrics retention (≤ 30 days) | operators with aggregate-metrics access | delete by aggregate retention schedule | aggregates only; no opaque IDs, lineage, source locator, or user-controlled values |
| `version` counters | track pinned protocol version usage | one bucket per released protocol version | configured aggregate-metrics retention (≤ 30 days) | operators with aggregate-metrics access | delete by aggregate retention schedule | aggregates only |

### Forbidden content (zero cardinality, rejected at the validator)

The following are forbidden from every serialized contract value, `repr`, and exception message:

- model content, payloads, prompts, tool results, tool arguments, retrieval bodies, or
  `system_message` text;
- absolute paths (any string starting with `/` or `~`, or containing `\\`);
- bearer tokens, credentials, API keys, session cookies, or any string starting with `bearer`,
  `sha256:`, `blake2:`, or `content:`;
- content/artifact hashes (any string matching `sha256:[hex]`, `blake2:[hex]`, `content:[hex]`,
  or other digest prefixes);
- newlines or carriage returns (`\n`, `\r`) anywhere in a serialized field.

Validator behavior: each contract type carries a `__post_init__` check that rejects the
construction outright before reduction. Rejection raises `ContextAdmissionValidationError` with a
bounded reason code; the rejected value never reaches storage, telemetry, or the reducer. Tests
in `tests/core/types/test_context_admission_contract.py` and
`tests/core/test_context_admission_coverage.py` freeze this contract by parameterizing against
canary values for every forbidden category.

Serialization, `repr`, and exception paths must remain content-free. Contract tests use canaries to
prove that content, absolute paths, bearer tokens, and content/artifact hashes cannot escape.
Static commit pins in coverage evidence document provenance only and are not runtime identities.

## Capability decision for Codex 0.145.0

The tested runtime is **codex-cli 0.145.0**, tag `rust-v0.145.0`, peeled commit
`25af12f7e61572b0bc18ddb1008be543b91519b0`.

Primary source evidence at that exact commit:

- [context status and window fields](https://github.com/openai/codex/blob/25af12f7e61572b0bc18ddb1008be543b91519b0/codex-rs/core/src/session/context_window.rs)
- [history accounting and estimation](https://github.com/openai/codex/blob/25af12f7e61572b0bc18ddb1008be543b91519b0/codex-rs/core/src/context_manager/history.rs)
- [the disabled experimental `token_budget` feature](https://github.com/openai/codex/blob/25af12f7e61572b0bc18ddb1008be543b91519b0/codex-rs/features/src/lib.rs)
- [`get_context_remaining` tool registration](https://github.com/openai/codex/blob/25af12f7e61572b0bc18ddb1008be543b91519b0/codex-rs/core/src/tools/spec_plan.rs)
- [`get_context_remaining` polling handler](https://github.com/openai/codex/blob/25af12f7e61572b0bc18ddb1008be543b91519b0/codex-rs/core/src/tools/handlers/get_context_remaining.rs)
- [App Server usage fields](https://github.com/openai/codex/blob/25af12f7e61572b0bc18ddb1008be543b91519b0/codex-rs/app-server-protocol/src/protocol/v2/thread.rs)
- [App Server raw-response documentation](https://github.com/openai/codex/blob/25af12f7e61572b0bc18ddb1008be543b91519b0/codex-rs/app-server/README.md)
- [compaction identity](https://github.com/openai/codex/blob/25af12f7e61572b0bc18ddb1008be543b91519b0/codex-rs/protocol/src/protocol.rs)
- [four-byte truncation heuristic](https://github.com/openai/codex/blob/25af12f7e61572b0bc18ddb1008be543b91519b0/codex-rs/utils/string/src/truncate.rs)

Official provider documentation: [input-token-count API
reference](https://platform.openai.com/docs/api-reference/responses/input-tokens),
[token-counting guide](https://platform.openai.com/docs/guides/token-counting), and
[compaction guide](https://platform.openai.com/docs/guides/compaction).

The `token_budget` capability is disabled and experimental. `get_context_remaining` is a
model-callable/polling observation, not an atomic reservation available synchronously to every
producer. Internal `base_window_tokens_remaining`, terminal usage, and rollout evidence are
estimated or post-hoc and do not bind the final request to provider acceptance. App Server usage
may be null and is reconciliation evidence only. The pinned hook schemas expose `PreCompact` and
`PostCompact` notification identities, but no atomic hook-visible admission watermark that fences
all producers and the final representation.

**Inference (clearly labeled):** taken together, these pinned source facts imply that a plugin
cannot close the count-then-mutate interval or prove that the provider accepted the exact
representation it counted.

**Verdict:** Codex 0.145.0 cannot supply authoritative admission capacity. Its observations may
support diagnostics and reconciliation, but every cumulative admission authority claim remains
`UPSTREAM_GATED`.

## Protocol evolution

Version 1 event semantic fields, state meanings, and witness requirements are frozen. Unknown
protocol versions fail closed; they are never silently coerced to version 1. Replay selects
semantics from each stream's recorded protocol version.

Any semantic change requires a new protocol version and explicit versioned conformance vectors.
C2 (#4334) owns durable encoding, stored-event migration, and upcasting. Upcasting must preserve
the recorded meaning and audit identity; it cannot manufacture an authority witness or reinterpret
an old unavailable decision as admitted.

## Downstream dependency graph

This exact graph identifies contract consumers and owners; it does not authorize their
implementation in C1:

```text
#4333 C1 -> #4334 C2
#4333 C1 + #4334 C2 + #4335 C3 -> #4336 C4
#4333 C1 + #4334 C2 + #4335 C3 -> #4337 C5
#4333 C1 + #4334 C2 -> #4338 C8

#4319/#4320/#4321/#4322/#4325/#4326/#4327 -> #4335 C3 artifact authority
#4334 C2 + #4336 C4 + #4337 C5 + #4324 + #4338 C8 -> #4339 C6
#4334 C2 + #4335 C3 + #4271 + #4338 C8 -> #4340 C7
```

Thus C2 is #4334, C3 is #4335, C4 is #4336, C5 is #4337, C6 is #4339, C7 is #4340, and C8 is
#4338. C2-C8 must use this shared accounting contract rather than invent per-adapter semantics.

## Non-goals

- This decision adds no enforcement and chooses no numeric budget defaults.
- Existing raw per-producer ceilings remain in force and are not replaced or relaxed.
- Bytes are not an exact token proxy and are not converted into admission authority.
- A digest is not an access capability or deduplication identity.
- C1 adds no journal, store, producer adapter, provider integration, response mutation, fleet
  schema, deduplication store, artifact reuse, or compaction rehydration.
- This decision does not claim that a local observation is provider acceptance or that post-hoc
  billing usage can reserve capacity.

## Traceability

| ID | Requirement | Contract or verification target |
|---|---|---|
| INV-1 | model-visible admission boundary | Admission boundary and authority |
| INV-2 | stable identities | Accounting and identity invariants |
| INV-3 | atomic reserve/commit/release protocol | State, witnesses, and atomic batches |
| INV-4 | version-pinned coverage matrix | CONTEXT_ADMISSION_COVERAGE |
| INV-5 | token_budget/get_context_remaining | Capability decision for Codex 0.145.0 |
| INV-6 | upstream Codex contract | Upstream authority request |
| INV-7 | privacy-safe observability | Privacy and observability |
| OUT-1 | versioned admission protocol and state machine | CONTEXT_ADMISSION_PROTOCOL_VERSION |
| OUT-2 | producer/control-point coverage matrix | CONTEXT_ADMISSION_COVERAGE |
| OUT-3 | accounting and identity invariants | Accounting and identity invariants |
| OUT-4 | failure and reconciliation semantics | reduce_context_admission |
| OUT-5 | authoritative token accounting | Admission boundary and authority |
| OUT-6 | upstream Codex request | Upstream authority request |
| OUT-7 | implementation dependency graph | Downstream dependency graph |
| NG-1 | no enforcement or numeric budget defaults | Non-goals |
| NG-2 | retain existing raw per-producer ceilings | Non-goals |
| NG-3 | bytes are not an exact token proxy | Non-goals |
| NG-4 | digest is not an access capability or deduplication identity | Non-goals |
| AC-1 | every model-visible producer | Producer coverage matrix |
| AC-2 | idempotent reservation keys and compaction/window reset rules | Accounting and identity invariants |
| AC-3 | outstanding concurrent calls and protected reserve | Protected reserve and epoch isolation |
| AC-4 | Codex claims cite tested version and primary sources | Capability decision for Codex 0.145.0 |
| AC-5 | C2-C8 use the shared accounting contract | Downstream dependency graph |
