# ADR-0009: Verified-Output Delivery Disposition

**Status:** Accepted
**Date:** 2026-08-07
**Source issue:** [#4479](https://github.com/TalonT-Org/AutoSkillit/issues/4479)
**Historical decisions:** [ADR-0006](0006-output-containment.md), [ADR-0008](0008-shell-capture-snapshot-authority.md)

## Context

ADR-0008 made `commit_verified_snapshot()` the sole path from a verified
capture snapshot to an immutable FINAL manifest. Until now, any exception
raised by that call — including one with nothing to do with output
correctness, such as ledger capacity bookkeeping, a contended recovery lock,
or a transient filesystem error on the ledger file — was indistinguishable
from an output-integrity failure. The runner discarded the already-verified,
checksummed bytes and reported a bare failure, even though the managed
stream had reached pipe EOF, `verify_capture_snapshot()` had recomputed and
matched the digest, and the child process had already exited with a known
code. The user lost real command output to a bookkeeping fault that never
touched the bytes themselves.

The fix requires the runner to distinguish, for every failure that can occur
after verification, whether that failure impugns the output or only the
ledger's ability to record it — and to act on that distinction consistently
rather than case-by-case.

## Decision

### 1. Delivery-or-explicit-refusal

A checksum-verified capture (a `VerifiedCaptureSnapshot` produced by
`verify_capture_snapshot()`, per ADR-0008) is either delivered to the caller
or the failure returned to the caller explicitly states why it was not. There
is no third outcome in which verified bytes are silently dropped and the
caller receives an undifferentiated failure that looks identical to a case
where the command itself never produced usable output.

When `commit_verified_snapshot()` raises after verification has already
succeeded, `run_capture()`'s narrow finalization guard inspects the raised
failure's `CaptureFailureReason` against the disposition registry (below)
before deciding whether to discard. If the disposition, the presence of a
verified snapshot, and the presence of a completed child outcome all permit
it, the runner delivers bounded output through `render_degraded_capture()`
and a `CaptureDegradedV3`-framed diagnostic (a distinct wire prefix from the
ordinary failure V3 framing, so a client can tell "delivered under
degradation" apart from "failed outright"). Bookkeeping for the underlying
fault is still attempted, best-effort, via `commit_capture_failure()` — its
own failure is logged and does not block delivery.

### 2. Bookkeeping failures never discard verified output

`_failure_policy.py` adds `CaptureFailureDisposition` — `PRESERVE_OUTPUT` or
`DISCARD_OUTPUT` — and `FAILURE_DISPOSITIONS`, a total mapping from every
`CaptureFailureReason` to one of the two:

| Disposition | Reasons | Rationale |
|---|---|---|
| `PRESERVE_OUTPUT` | `ACTIVE_CAPACITY_EXHAUSTED`, `RETENTION_CAPACITY_EXHAUSTED`, `EVIDENCE_CAPACITY_EXHAUSTED`, `PROJECTED_COMPACTED_BYTES_EXHAUSTED`, `HARD_LEDGER_CAPACITY_EXHAUSTED`, `MIGRATION_BLOCKED`, `LEDGER_INTEGRITY`, `FILESYSTEM_AUTHORITY`, `PERMISSION_DENIED`, `FILESYSTEM_IO`, `RECOVERY_CONTENDED` | The fault is in ledger bookkeeping (capacity admission, migration, ledger-file I/O or integrity, lock contention) — the verified output bytes are unaffected by it. |
| `DISCARD_OUTPUT` | `SNAPSHOT_INTEGRITY`, `UNKNOWN_SETUP` | The fault is (or may be) in the output itself — a checksum mismatch, tamper detection, or an unclassified condition that cannot be trusted to be bookkeeping-only. |

`PRESERVE_OUTPUT` is an eligibility classification only. It never manufactures
output and is never, by itself, sufficient authority to deliver: the
finalization guard additionally requires a live `VerifiedCaptureSnapshot` and
a completed child (`command_outcome is not None`). A `PRESERVE_OUTPUT`
disposition reached before those two facts are established does not unlock
delivery — the guard falls through to the ordinary fail-closed handler in
that case, matching the pre-existing behavior for every failure that occurs
before verification completes.

### 3. Exit-code parity under degraded delivery

`degraded_delivery_return(shell_returncode)` returns the child's exact exit
code and raises if `shell_returncode is None` (a completed-child precondition
violation would be a programming error, not a runtime condition). Combined
with (2), this fixes the invariant: **whenever the child process ran, the
disposition preserves output, and the degraded stdout delivery succeeds, the
runner's own exit code equals the child's exit code.** The runner never
substitutes its own bookkeeping-failure status for the child's real outcome
in this path.

If the stdout write of the degraded payload itself fails, the runner does
not attempt a second degraded outcome — it re-raises the original
finalization exception into the unchanged outer fail-closed handler (exit 1),
because the condition "degraded stdout delivery succeeded" no longer holds.
The single-envelope invariant follows: the degraded diagnostic marker is only
ever written to stderr after the stdout payload has been flushed
successfully, so a caller never observes a degraded marker for output it did
not receive.

### 4. Totality is a forcing function, not a convention

`FAILURE_DISPOSITIONS` is checked against `CaptureFailureReason` at import
time:

```python
if set(FAILURE_DISPOSITIONS) != set(CaptureFailureReason):
    raise AssertionError(...)
```

A new `CaptureFailureReason` member added without a corresponding
`FAILURE_DISPOSITIONS` entry prevents `_failure_policy.py` from importing at
all, anywhere it is imported. This converts "did the author remember to
classify the new failure reason?" from a question answered by code review
attentiveness into one answered by module load — the module cannot be used
in a state where a reason exists without a declared disposition. The same
forcing-function pattern is applied to the two adjacent registries introduced
in the same change: `STATE_RECLAIMABILITY` in `_lifecycle_policy.py` (every
`CaptureState` must declare a `ReclaimKind` and grace/hold duration) and
`CAPACITY_REASON_GATES` in `_capacity.py` (every `CaptureCapacityReason` must
declare which capacity gates — `ADMISSION`, `TRANSITION` — it can fire from).
Those two registries are not delivery-disposition invariants in their own
right; they are recorded here because the failure reasons they classify are
the same failure reasons whose disposition this ADR fixes, and the totality
technique that makes disposition trustworthy is the same technique that makes
reclaimability and gate-reachability trustworthy.

## Accepted Gaps

1. `UNKNOWN_SETUP` currently doubles as the wire label for the verify-stage
   tamper detector. It is classified `DISCARD_OUTPUT` (fail-closed) until a
   follow-up splits a dedicated reason out of it; an unclassified failure
   must never default to `PRESERVE_OUTPUT`.
2. This ADR governs only the AutoSkillit Codex native-shell capture runner
   established by ADR-0008. It does not extend disposition semantics to
   Claude Code native shell, MCP `run_cmd`, or any other artifact contract.
3. Degraded delivery inherits ADR-0008's bounded-transport limits
   (`render_degraded_capture()` reuses the same inline-byte cap and V2
   reference framing as the success path) — it does not add a new output
   size allowance.

## Consequences

- A bookkeeping fault occurring strictly after output verification can no
  longer cause AutoSkillit to report a bare failure for a command whose
  output was, in fact, captured and verified intact.
- The child's exit code is preserved end-to-end for every `PRESERVE_OUTPUT`
  reason once verification has succeeded, closing the previous gap where a
  ledger-side fault silently overwrote a real, meaningful exit code.
- Every future `CaptureFailureReason`, `CaptureState`, and
  `CaptureCapacityReason` member is structurally required to declare its
  disposition, reclaimability, and gate membership respectively before the
  module can load — see DS-012 in
  `audit-defense-standards/SKILL.md` for the audit-time check that keeps this
  true outside of import-time enforcement (e.g., in a partially-applied
  edit under review).
