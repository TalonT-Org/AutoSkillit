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

### Unified Exec Assumption

The hook contract for `exec_command` is identical (tool `"Bash"`, string `command`),
so the rewrite applies there too. AutoSkillit does not enable Codex's experimental
`unified_exec` surface in the config it writes; interactive stdin-driven sessions are
the only case where file-redirected output would change observable behavior.

### Capture Lifecycle Ownership

The lifecycle store records `RESERVED`, `STAGED`, `PUBLISHED_WRITING`,
`FINALIZED`, `FAILED`, `ABANDONED`, `DELETING`, `DELETED`, and `TAMPERED`.
The immutable FINAL, reference, delivery, and reader semantics belong to
ADR-0008. A finalized or failed artifact becomes eligible one hour after its
terminal transition; an abandoned producer becomes eligible one hour after its
durable creation time. Eligibility is reclaimed only on the next enabled,
trusted trigger, not by a wall-clock scheduler.

There are two installed cleanup roles. Every valid runner invocation performs
one bounded tail sweep after all producer resources and the writer lease are
released. The independent cleanup-only `capture_lifecycle_hook.py` performs a
bounded `SessionStart` sweep in interactive and headless sessions.
Cleanup failure is fail-open and cannot replace the mapped command result. If
hooks are disabled, neither owner runs and eligible artifacts remain.

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
3. A bare trailing backslash at EOF loses its literal backslash from output under
   continuation semantics. Exit code is preserved.
4. Vendored-tree version discrepancy: the checkout tag is `rust-v0.143.0-alpha.10`
   vs the 0.144.1 description in the issue/ADR. The hook contract must be re-verified
   against the deployed Codex version before shipping.
5. A supplied symlink spelling of `cwd` is accepted only by opening it first as
   the `ProjectAnchor`; `.autoskillit`, `temp`, and `shell_capture` symlinks are
   rejected. Physical path strings are display hints, not filesystem authority.
6. General retrieval, publication/privacy policy, quota accounting, and upstream
   live visibility remain downstream work identified by ADR-0008.

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
  the same lock at startup. Non-blocking lock acquisition now retries with
  jittered, doubling backoff bounded by the sweep's own `max_duration_seconds`
  budget — no new configuration knob — so a contended lock recovers within the
  same invocation instead of zeroing it.

## Enforcement

Whether a failure occurring after this ADR's containment mechanism has
already captured and verified output may still discard that output is
governed by [ADR-0009](0009-verified-output-delivery-disposition.md)'s
`FAILURE_DISPOSITIONS` registry (`hooks/_capture/_failure_policy.py`) and its
import-time totality assertion. `DS-012` in
`audit-defense-standards/SKILL.md` is the audit-time check that a verified
primary result is never discarded or misreported by a bookkeeping failure.

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
- One-hour lifecycle reclamation is owned by the installed runner tail and the
  cleanup-only `SessionStart` hook, with bounded retry and quarantine recovery.
