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
   fd. The bounded inline slice includes a provenance marker whose path is present
   only after marker-time identity verification. The ordinary outer-result limit
   remains the backstop for hook-failure paths. The separately configured
   `CODEX_HISTORY_RETENTION_TOKEN_LIMIT` replaces the model's `truncation_policy`
   outright, so it governs both the current-turn exec output sent to the model and
   retained history — not later history alone.

### Sequencing Rule

The guard is retired on Codex only in the same change that delivers the Codex
lossless mechanism. Claude Code relief is immediate (Phase A).

### Provenance Rule

Every residual hook message uses a typed policy event rendered by a shared
formatter. Suggested rewrites are classifier-validated before emission.

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

The lifecycle store durably records `RESERVED`, `STAGED`,
`PUBLISHED_WRITING`, `FINALIZED`, `FAILED`, `ABANDONED`, `DELETING`,
`DELETED`, `RETRY`, and `TAMPERED`. The writer lease spans command execution,
drain, settlement, integrity verification, durable finalization, and marker
flush. A finalized or failed artifact becomes eligible one hour after its
terminal transition; an abandoned producer becomes eligible one hour after its
durable creation time. Eligibility is reclaimed only on the next enabled,
trusted trigger, not by a wall-clock scheduler.

There are two installed cleanup roles. Every valid runner invocation performs
one bounded tail sweep after all producer resources and the writer lease are
released. The independent cleanup-only `capture_lifecycle_hook.py` performs a
bounded `SessionStart` sweep without an interactive/headless distinction.
Cleanup failure is fail-open and cannot replace the mapped command result. If
hooks are disabled, neither owner runs and eligible artifacts remain.

Deletion is confined to the shared store's identity-revalidated quarantine
transaction. Only lifecycle-recorded `shell_[0-9a-f]{16}.log` artifacts are
eligible. Fresh records, live writers, nonmatching names, symlinks, FIFOs,
hardlinks, world-writable files, identity replacements, unexpected link
counts, and tampered observations survive. Row, monotonic-time, frame, ledger,
and compaction bounds limit each sweep; contention and operational failures
become durable capped-backoff retries. `deleted_bytes` is a logical managed-byte
count, not evidence of physical block reclamation.

The guarantee is process-termination recovery on supported local Linux and macOS filesystems.
It does not claim Darwin OS-crash or power-loss durability
from ordinary `fsync()`. Advisory leases and identity revalidation establish a
cooperative same-UID boundary; a hostile same-UID process that ignores advisory
locks is outside it.

### Future Direction

Route (c) — upstream pre-truncation integration — is the ideal end state but outside
this repo's control. If Codex exposes a pre-truncation hook point, the shell capture
hook can be retired in favor of that mechanism.

## Accepted Gaps

1. `disown`ed/job-table-detached children are outside the `wait` drain guarantee.
   Their post-exit writes into the artifact are best-effort — mirrors native
   detached-output semantics.
2. Fatal self-signals (e.g. `kill -TERM $$`) are reported as the shell-compatible
   `128 + signal` status by the runner. Output drained before termination remains
   available inline and in the retained artifact. SIGKILL remains untrappable.
3. Head/tail slices are byte-cut and may split multibyte UTF-8 at slice edges. The
   artifact is authoritative.
4. Draining non-detached background jobs makes the harness synchronous for their
   duration, bounded by the tool timeout. `disown` is the fire-and-forget escape.
5. A bare trailing backslash at EOF loses its literal backslash from output under
   continuation semantics. Exit code is preserved.
6. Vendored-tree version discrepancy: the checkout tag is `rust-v0.143.0-alpha.10`
   vs the 0.144.1 description in the issue/ADR. The hook contract must be re-verified
   against the deployed Codex version before shipping.
7. A supplied symlink spelling of `cwd` is accepted only by opening it first as
   the `ProjectAnchor`; `.autoskillit`, `temp`, and `shell_capture` symlinks are
   rejected. Physical path strings are display hints, not filesystem authority.
8. Same-user code inside the command can rename a verified directory entry after
   it is opened. Output, hashing, and replay remain fd-bound; if the live pathname
   chain no longer matches, the marker reports its path as `unavailable`.
9. Detached-writer/coherent-snapshot semantics remain outside this guarantee
   and are tracked by [#4322](https://github.com/TalonT-Org/AutoSkillit/issues/4322).
   Durable identities and leases are extension seams for retrieval
   ([#4325](https://github.com/TalonT-Org/AutoSkillit/issues/4325)), the broader
   publication/privacy contract
   ([#4326](https://github.com/TalonT-Org/AutoSkillit/issues/4326)), and quota
   accounting ([#4327](https://github.com/TalonT-Org/AutoSkillit/issues/4327));
   this decision does not claim those features are implemented.

## Consequences

- Claude Code sessions no longer see AutoSkillit shell deny or rewrite surfaces.
- The classification engine (`classify_command_output_budget` and supporting
  functions) is deleted; shared tokenization utilities are preserved.
- Configuration surface reduces: `small_file_max_bytes` is removed (existed solely
  for the classifier's literal-small-JSONL exception).
- `shell_max_inline_bytes` survives with its new capture-threshold meaning.
- Complete Codex shell output is captured to mechanism-owned artifacts at
  the descriptor-anchored project capture root. A pathname is reported only
  while it still binds to the opened project, directories, and artifact.
- One-hour lifecycle reclamation is owned by the installed runner tail and the
  cleanup-only `SessionStart` hook, with bounded retry and quarantine recovery.
