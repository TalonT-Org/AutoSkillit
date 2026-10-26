# ADR-0006: Output-Boundary Containment

**Status:** Accepted
**Date:** 2026-07-18
**Issue:** #4286

## Context

ADR-0005 (Layer 3) established a pre-execution command-shape classifier
(`output_budget_guard`) to deny unbounded shell commands before they run. The
classifier's failure mode is structural: static pre-execution boundedness proof
over arbitrary shell is unwinnable — every enumerated shape can be expressed in
an equivalent, un-enumerable form. Enforcement must move to the output boundary:
bound what actually enters model context (measured bytes, lossless spill to
durable artifacts, bounded inline slices), per backend.

## Decision

Retire the pre-execution command-shape classifier and replace it with per-backend
output-boundary bounding on measured bytes:

1. **Claude Code native shell** — already bounded by the harness's native Bash
   spill mechanism (no AutoSkillit surface needed).
2. **MCP `run_cmd` channel** — lossless capture-file promotion: subprocess output
   goes to artifact-directory files; only bounded slices enter worker memory;
   oversized outputs are promoted in place with a contract (bytes, sha256,
   completeness).
3. **Codex native shell** — PreToolUse input-rewrite hook wraps every shell
   command in a minimal isolated runner invocation. The runner opens `cwd` first,
   establishes descriptor-relative authority for policy and capture components,
   durably reserves private staging before publishing the public artifact without
   replacement, acquires a writer lease, and drains child output through its owned
   fd. Small output remains inline and oversized output uses a bounded transport.
   [ADR-0008](0008-shell-capture-snapshot-authority.md) is the normative contract
   for pipe EOF, verified FINAL snapshots, opaque V2 references, output delivery,
   and reader leases. The ordinary outer-result limit remains the backstop for
   hook-failure paths. The separately configured
   `CODEX_HISTORY_RETENTION_TOKEN_LIMIT` replaces the model's `truncation_policy`
   outright, so it governs both the current-turn exec output sent to the model and
   retained history — not later history alone.

### Sequencing Rule

The guard is retired on Codex only in the same change that delivers the Codex
lossless mechanism. Claude Code relief is immediate (Phase A).

### Provenance Rule

Every residual hook message uses a typed policy event rendered by a shared
formatter. Suggested rewrites are classifier-validated before emission.

**Implemented.** `hooks/_policy_event.py`'s `PolicyEvent`/`render_provenance_prefix`
is the shared formatter; `hooks/_capture/_reconcile.py`, `shell_capture_hook.py`,
and `capture_lifecycle_hook.py` construct every residual message through it — no
ad-hoc `[AutoSkillit ...]` literal exists outside `_policy_event.py` in those
modules (`tests/arch/test_hook_message_provenance.py`). The classifier is
`classify_cleanup_outcome` (`hooks/_capture/_types.py`), which derives a
`CleanupSeverity` from `(progress, blocker, errors)`:

| Severity | Meaning | Emission |
|---|---|---|
| `healthy` | no blocker, or store absent | none |
| `deferred` | bounded budget work exhausted, some progress made this pass | none — backlog remains but isn't attention-grade |
| `stalled` | externally blocked (lock contention, migration, filesystem authority), or a budget blocker with zero progress | one neutral line |
| `failed` | `errors > 0` | one failure-worded line — the only severity whose rendered text may contain "failed" |

`hooks/_capture/_reconcile.py`'s `emit_owner_diagnostic(outcome, owner, write)` is
the single owner-neutral emission path for both cleanup owners: the per-command
runner-tail sweep (`owner="runner_tail"`) and the SessionStart sweep
(`owner="session_start"`). `shell_capture_hook.py`'s native-shell control
resolution (`_resolve_control`) is a separate three-way declared-mode contract,
also routed through `PolicyEvent`: a declared `capture` mode with no managed
identity is silent (normal, not anomalous); an undeclared mode emits a neutral
note; an incomplete/invalid managed-identity tuple emits a distinct neutral
note naming the fallback. Codex cook sessions (`codex.py build_interactive_cmd`)
positively declare `AUTOSKILLIT_NATIVE_SHELL_CAPTURE_MODE=capture`, so absence
of that declaration is a genuine anomaly again rather than the common case.

### Pre-Spend Decision

No shape-based pre-execution backstop remains in the end state. Execution cost is
accepted — bounded by tool timeouts — because context cost is what this mechanism
exists to bound. Catastrophic side-effect prevention belongs to `write_guard` and
the Codex sandbox, not to output budgeting.

### Unified Exec Host Model

The hook contract for `exec_command` is identical (tool `"Bash"`, string `command`),
so the rewrite applies there too. Codex CLI 0.156.1 runs `exec_command` through
`unified_exec` by default. It runs each command in its own POSIX session, and one
Codex session can hold several such processes at once. It SIGKILLs the command's
process group when an exited process is released, when a process is evicted at
capacity (possibly while still running), and at session shutdown. Codex's own
parent-death signal reaches only its direct child. The capture runner's
process-lifetime contract is defined against that host model in ADR-0008.

### Capture Lifecycle Ownership

The lifecycle store records `RESERVED`, `STAGED`, `PUBLISHED_WRITING`,
`FINALIZED`, `FAILED`, `ABANDONED`, `DELETING`, `DELETED`, and `TAMPERED`.
The immutable FINAL, reference, delivery, and reader semantics belong to
ADR-0008. Retention follows readability: after delivery completes, captures
with no outstanding bearer become eligible immediately; a delivered capture
with a published reference remains readable until expiry; unsettled deliveries
and failed artifacts keep the one-hour hold.

| FINALIZED record | Eligibility time | Why |
|---|---|---|
| Delivered with no outstanding bearer (`not_requested`, `unavailable`, `expired`, `revoked`) | Immediately after the delivered transition commits; its due timestamp is `manifest.finalized_at` | Inline bytes have already been delivered and no bearer can open the artifact |
| Delivered with a `published` reference | `manifest.reference_expiry` | The bearer remains readable until expiry |
| Any other FINALIZED state (undelivered, delivery `failed`/`unknown`, or reference `unknown`) | `manifest.retention_deadline` | Preserve the one-hour forensic hold |

`FAILED` artifacts, including pre-FINAL failures, remain eligible one hour after
their terminal transition; abandoned producers remain eligible one hour after
durable creation time. A FINALIZED record with no outstanding bearer is not due
until delivery completes, even though its due timestamp is `manifest.finalized_at`.

Eligibility is reclaimed only on the next enabled, trusted trigger, not by a
wall-clock scheduler.

Admission participates in that trigger-owned model. When store-wide due debt
reaches the assist threshold, the admitting runner performs one debt-scaled
sweep under a fixed 50 ms/16-transition ceiling before retrying its admission.
At the higher stall threshold, admission is refused until reclamation lowers
the observed debt. This adds no owner or scheduler: the assist is work
performed by the existing `same_runner` role on its existing trigger.

There are two installed cleanup roles. Every valid runner invocation performs
one bounded tail sweep after all producer resources and the writer lease are
released. The independent cleanup-only `capture_lifecycle_hook.py` performs a
bounded `SessionStart` sweep in interactive and headless sessions.
Cleanup failure is fail-open and cannot replace the mapped command result. If
hooks are disabled, neither owner runs and eligible artifacts remain.
Trigger-only ownership remains adequate while assist work stays bounded and
at least one installed reclamation owner retains a positive directory-scan
budget. Removing either condition requires a new ownership decision; the
read-only doctor must not become a scheduler implicitly.

The producer's own runner tail tries its just-delivered inline capture first,
before the cursor-rotated backlog, within the existing `RUNNER_TAIL_BUDGET`;
this moves the same required deletion work earlier without increasing total
deletion work or adding an owner or scheduler.

Eligibility is distinct from access and deletion. Inline output has already
been delivered as in-memory bytes and grants no later artifact-opening
authority; `open_verified_capture()` requires a published bearer. A holder of a
delivered published reference may open it until expiry. Expiry prevents new
authorized opens but does not erase previously delivered bytes. An already-open
verified reader's shared lease prevents reclamation until it closes, after which
identity checks still have to pass before deletion.

Deletion is confined to the shared store's identity-revalidated quarantine
transaction. Only lifecycle-recorded `shell_[0-9a-f]{16}.log` artifacts are
eligible. Fresh records, live writers, nonmatching names, symlinks, FIFOs,
hardlinks, world-writable files, identity replacements, unexpected link
counts, and tampered observations survive. Row, monotonic-time, frame, ledger,
and compaction bounds limit each sweep and its backlog; contention and operational
failures become durable capped-backoff retries. `deleted_bytes` counts
logical managed bytes, not evidence of physical block reclamation.

The guarantee is process-termination recovery on native local Linux filesystems.
It does not extend the advisory-lease contract to macOS/Darwin or claim OS-crash
or power-loss durability from ordinary `fsync()`. Advisory leases and identity
revalidation establish a cooperative same-UID boundary; a hostile same-UID
process that ignores advisory locks is outside it.

### Future Direction

Route (c) — upstream pre-truncation integration — is the ideal end state but outside
this repo's control. If Codex exposes a pre-truncation hook point, the shell capture
hook can be retired in favor of that mechanism.

## Accepted Gaps

1. The historical containment decision does not establish snapshot or marker
   authority. ADR-0008 replaces those claims and makes actual pipe EOF, including
   descendant-writer liveness, part of the managed-stream contract.
2. Head and tail slices remain byte-cut and may split multibyte UTF-8 at slice
   edges. Verified bytes are available only through the opaque-reference reader.
3. Vendored-tree version discrepancy: the checkout tag is `rust-v0.143.0-alpha.10`
   vs the 0.144.1 description in the issue/ADR. The hook contract must be re-verified
   against the deployed Codex version before shipping.
4. A supplied symlink spelling of `cwd` is accepted only by opening it first as
   the `ProjectAnchor`; `.autoskillit`, `temp`, and `shell_capture` symlinks are
   rejected. Physical path strings are display hints, not filesystem authority.
5. General retrieval, publication/privacy policy, quota accounting, and upstream
   live visibility remain downstream work identified by ADR-0008.
6. Undelivered and otherwise unsettled-delivery anomalies retain the one-hour
   hold and sit outside the declared load; their encoded frames still count
   against actual capacity.

## Resolved

- **Unledgered orphan files:** a `shell_[0-9a-f]{16}.log` written before a crash, a
  ledger reset, or a legacy pre-ledger run had no record and was permanently
  invisible to cleanup. The budget-bounded directory-reconciliation scan phase
  (`hooks/_capture/_orphan_scan.py`, `docs/safety/hooks.md`) adopts eligible
  orphans into the same `LEGACY_CLEANUP_ONLY` shape the legacy-ledger decode
  path already produces, so the existing quarantine-deletion path retires them
  under normal budgets and invariants.
- **Lock contention:** a single non-blocking `flock()` attempt aborted a sweep
  immediately on any contention, including the 256-attempt `SESSION_START_BUDGET`
  pass, even though `session_scope="any"` makes every concurrent session contend
  the same lock at startup. Every lifecycle-lock acquisition now uses
  non-blocking acquisition with a deadline: sweep callers compose that deadline
  with `max_duration_seconds`, while ordinary callers use the lifecycle
  lock-wait policy. There is no blocking-acquisition form, so contention either
  recovers within the invocation or fails as bounded `LOCK_CONTENDED` work.

## Enforcement

Whether a failure occurring after this ADR's containment mechanism has
already captured and verified output may still discard that output is
governed by [ADR-0009](0009-verified-output-delivery-disposition.md)'s
`FAILURE_DISPOSITIONS` registry (`hooks/_capture/_failure_policy.py`). The
arch rule is the registry's own import-time totality assertion — a
`CaptureFailureReason` member with no declared disposition raises
`AssertionError` at import, so the module cannot load until every reason is
classified — backed by `tests/hooks/test_capture_failure_disposition.py`'s
`TestDispositionRegistryTotality`. `tests/arch/test_hook_flock_nonblocking.py`
also inventories every executable hook `fcntl.flock()` acquisition and fails
closed unless it statically includes `LOCK_NB` (with `LOCK_UN` releases
exempt). `DS-012` in
`audit-defense-standards/SKILL.md` is the periodic audit-time check that no
bookkeeping failure discards or misreports a verified primary result outside
of what the import-time assertion alone can catch (e.g. a `PRESERVE_OUTPUT`
classification applied to a reason that should be `DISCARD_OUTPUT`).

Containment remains in force under setup-stage capacity degradation because
agent-visible output stays bounded by `shell_max_inline_bytes`.

The same enforcement surface bounds admission by measured reclamation debt:
assist and stall are admission-only capacity reasons, and import-time totality
requires their gate, failure, disposition, detail, and blocker classifications.

The capacity model derives the production byte floor and the operational,
retained, and evidence record caps from one required record count.
`capacity_envelope_violations(spec)` supplies both the import-time check for the
production default and runtime override validation. The import-time assertion
checks only the static default; live admission and transition gates use actual
projected bytes. The success-path hold uses `REFERENCE_LIFETIME_SECONDS`: at the
declared peak of 0.5333 admissions per
second and a 1,800-second reference lifetime, `ceil(0.5333… * 1,800) + 16`
producers yields 976 held records. The floor adds the due debt that can accrue
before reclamation assist engages, `reclamation_debt_assist_records - 1`; this
produces 1,231 required records at the default 256-record assist threshold.
The same count is required by each record cap.

`PLANNED_RECORD_FRAME_BYTES` is 2,608 bytes: the largest measured encoding of
real `FINALIZED`, `DELETED`, and `FAILED` record shapes under the declared
numeric profile, rounded up to a multiple of eight. The measurement sets
non-schema counters and identities to `2**63 - 1`, uses the same compaction
epoch, valid finite timestamps, and bounded command outcomes. Adding the
default 256 tombstones gives `(1,231 + 256) * 2,608 = 3,878,096` bytes, below
the default `compaction_low_bytes` of 3,932,160. The declared allowance models
that load and profile; `CompactedFrameSizer` counts every encoded byte in the
retained projection, including opaque frames, for both admission and compaction.
A remeasured allowance above 2,644 bytes makes the default
import-time check fail and requires re-deriving the floor. Overrides that break
the byte or shared record-count envelope are rejected by the same function.

`compaction_low_bytes` is a soft limit for state-advancing work. Admission is
gated separately; transitions that advance a record through stage, publish,
and finalize may be refused above it. Same-state bookkeeping, including
delivery and reference updates, open-time normalization, and sweep expiry,
remains subject to the hard bound. Recovery transitions retain their reserved
headroom. `_with_capacity_rescue` first attempts the operation; for its existing
rescuable refusal reasons, it performs one bounded sweep and retries from a
fresh locked load before final refusal.

The retained-record envelope is a compacted projection of latest record
frames, allowed tombstones, and opaque bytes. Superseded transition frames
remain in the physical ledger until compaction, so they are temporary physical
amplification and are not multiplied into the retained-record floor.
`_append_locked` checks prospective physical size and compacts instead of
appending when the append trigger would be crossed. Compaction output may itself
exceed that trigger while remaining within the hard `MAX_LEDGER_BYTES` bound;
the trigger is not a universal ceiling. A projected-capacity refusal is a
`CaptureCapacityError`; an existing physical file above `MAX_LEDGER_BYTES` is
rejected on load as `CaptureLedgerError` and does not enter the capacity-only
degraded path.

## Consequences

- Claude Code sessions no longer see AutoSkillit shell deny or rewrite surfaces.
- The classification engine (`classify_command_output_budget` and supporting
  functions) is deleted; shared tokenization utilities are preserved.
- Configuration surface reduces: `small_file_max_bytes` is removed (existed solely
  for the classifier's literal-small-JSONL exception).
- `shell_max_inline_bytes` survives with its new capture-threshold meaning.
- Complete Codex shell output is captured to a descriptor-anchored carrier.
  Oversized replay reports an opaque V2 reference or an unavailable status,
  never an authoritative pathname.
- Readability-derived lifecycle reclamation is owned by the installed runner
  tail and the cleanup-only `SessionStart` hook, with bounded retry and
  quarantine recovery; unsettled artifacts retain the one-hour hold.
