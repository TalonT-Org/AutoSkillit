# Codex Recipe Delivery Conformance

Status: blocked

**Probe contract:** `codex-recipe-delivery-v1`
**Decision date:** 2026-07-22
**Model identity:** `gpt-5.6-sol`

## Decision

`SUPPORTED_CODEX_RECIPE_EVIDENCE_REGISTRY` remains empty. Current Codex rollout and
rollout-trace files are writable by the same OS identity as model-invoked commands. They
record requested input but do not expose an independently observed resolved outer limit or
raw outer pre-truncation bytes before the nested MCP call. They therefore cannot authorize
the attested-inline mode.

The production resolver fails closed to the bounded, content-addressed `recipe_pull` path.
A positive identity requires a protected pre-call host channel that the model and direct MCP
callers cannot mint, alter, replace, or replay.

## Evidence Domains

| Domain | Current observation |
|---|---|
| Caller pragma/request | The generated contract names the exact 56,750-token pragma, but no protected request values are available to the current runtime. |
| Host thread/turn/call and selected limit | Rollout `thread_id` is diagnostic. Protected turn/call identity and a resolved selected limit are unavailable. |
| History configuration | `tool_output_token_limit` is 56,750 and is recorded only as later-history retention. |
| Canonical payload and persisted blob | Envelope/pull tests independently validate `payload_sha256`, `artifact_blob_sha256`, and `body_sha256`. |
| Compact MCP wire and nested result | The live probe hashes its transcript. A distinct nested JavaScript result is unavailable without Code Mode host instrumentation. |
| Raw outer result | Raw outer pre-truncation bytes are unavailable in the current host schema. |
| Model-visible retention | The credentialed probe pulls a bounded content section and rejects transport truncation markers. |
| Next request | The next model request must reproduce the envelope's `body_sha256` and the probe completion marker. |

## Negative Matrix

| Case | Required outcome |
|---|---|
| Direct MCP or forged nested fields | Envelope/pull |
| Explicit high without protected selected-limit evidence | Envelope/pull |
| Omitted, lower, malformed, or over-ceiling maximum | Envelope/pull |
| Writable rollout or unsigned trace | Envelope/pull |
| Replayed call, repeated recipe, or changed recipe | Envelope/pull |
| `recipe://{name}` resource | Envelope/pull; negotiation ineligible |

## Live Probe

**Envelope/pull oracle:** PASS (2026-07-22)

The dedicated smoke test initializes an isolated Codex home against the worktree's MCP
server, opens `remediation` ingredients-only, loads it without a forged delivery request,
pulls the immutable `content` generation, and requires the following model request to echo
the retained body digest. With the isolated MCP process explicitly identified as Codex, the
probe retained the bounded `recipe_pull`, pulled content with the same `body_sha256`, found
no transport-truncation marker, and echoed the digest in the terminal next-request canary.
`task test-smoke-codex` passed the dedicated oracle on `gpt-5.6-sol`.

This pass proves the fail-closed envelope/pull recovery path. It does not prove an
authoritative selected outer limit, raw pre-truncation bytes, or a protected pre-call event,
so it cannot enable `ATTESTED_INLINE`.

Until the protected-host prerequisite exists and this report contains a passing
authoritative-high/next-request oracle, no supported evidence identity may be added.
