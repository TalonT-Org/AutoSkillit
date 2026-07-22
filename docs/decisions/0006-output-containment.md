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
   command in a capture harness: complete output to a mechanism-owned artifact,
   bounded inline slice with provenance marker, exit code preserved. The
   ordinary outer-result limit remains the backstop for hook-failure paths. The
   separately configured `CODEX_HISTORY_RETENTION_TOKEN_LIMIT` governs later history.

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

### Future Direction

Route (c) — upstream pre-truncation integration — is the ideal end state but outside
this repo's control. If Codex exposes a pre-truncation hook point, the shell capture
hook can be retired in favor of that mechanism.

## Accepted Gaps

1. `disown`ed/job-table-detached children are outside the `wait` drain guarantee.
   Their post-exit writes into the artifact are best-effort — mirrors native
   detached-output semantics.
2. Fatal self-signals (e.g. `kill -TERM $$`) skip the slice-emission postlude.
   Exit-code parity holds, the artifact persists as lossless evidence, but no inline
   slice is emitted and the file is not deleted. SIGKILL is untrappable, so full
   closure is impossible.
3. Head/tail slices are byte-cut and may split multibyte UTF-8 at slice edges. The
   artifact is authoritative.
4. Draining non-detached background jobs makes the harness synchronous for their
   duration, bounded by the tool timeout. `disown` is the fire-and-forget escape.
5. A bare trailing backslash at EOF loses its literal backslash from output under
   continuation semantics. Exit code is preserved.
6. Vendored-tree version discrepancy: the checkout tag is `rust-v0.143.0-alpha.10`
   vs the 0.144.1 description in the issue/ADR. The hook contract must be re-verified
   against the deployed Codex version before shipping.

## Consequences

- Claude Code sessions no longer see AutoSkillit shell deny or rewrite surfaces.
- The classification engine (`classify_command_output_budget` and supporting
  functions) is deleted; shared tokenization utilities are preserved.
- Configuration surface reduces: `small_file_max_bytes` is removed (existed solely
  for the classifier's literal-small-JSONL exception).
- `shell_max_inline_bytes` survives with its new capture-threshold meaning.
- Complete Codex shell output is captured to mechanism-owned artifacts at
  `<cwd>/.autoskillit/temp/shell_capture/shell_<uuid16>.log`.
