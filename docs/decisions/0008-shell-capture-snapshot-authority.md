# ADR-0008: Shell-Capture Snapshot Authority

**Status:** Accepted
**Date:** 2026-07-29
**Source issue:** [#4322](https://github.com/TalonT-Org/AutoSkillit/issues/4322)
**Historical decision:** [ADR-0006](0006-output-containment.md)

## Context

ADR-0006 moved Codex native-shell containment to a measured output boundary, but
its first artifact marker treated a current pathname and mutable metadata as if
they proved a coherent completed capture. A descendant could retain the output
pipe after the direct shell exited, pathname bindings could change, and
finalization, publication, output delivery, and cleanup were not independent
durable facts.

Codex shell capture needs one authority contract that starts with the bytes
observed from the managed pipe, verifies the completed carrier through a retained
descriptor, commits an immutable FINAL manifest, and exposes oversized output
only through a checked opaque reference.

## Decision

This decision applies only to the AutoSkillit Codex native-shell capture runner.
It does not change Claude Code native shell, MCP `run_cmd`, or arbitrary
application artifact contracts.

### Managed stream and completion

The managed stream is the byte sequence read from the isolated command's single
pipe after stderr is redirected to stdout. Measurement records the merged order
observed by that pipe. It does not claim application-level causal ordering for
concurrent stdout and stderr writes.

Only pipe EOF ends the managed stream. Direct-shell exit, job-table detachment,
`setsid()`, silence, or elapsed time cannot synthesize completion while any
descendant retains a pipe writer. A detached descendant that closes or redirects
every inherited writer is outside the stream and does not delay EOF. There is no
capture-local completeness deadline. An outer tool or session timeout may
terminate the runner and produces failure evidence, not a completed snapshot.

One drain pass computes total bytes, SHA-256, bounded inline bytes, and bounded
head and tail bytes from that same stream domain. Those values cannot be supplied
again by a caller at finalization.

The runner executes the command text verbatim as `bash -c <command>` and installs
no trap, subshell, or other finalization in the user's shell. In capture mode,
pipe holders are waited for until actual pipe EOF whatever their trap state or
job style. The runner never signals the owned group before pipe EOF on its own
authority; host signals that the runner forwards are relayed, not runner
settlement. A runner-originated signal before EOF fails the capture with
`RUNNER_SETTLEMENT` and commits no FINAL.

### Process lifetime ownership

The runner starts a lifeline anchor before the command leader. The anchor ignores
every catchable signal whose default action terminates or stops a process, confirms
that it is armed before the leader is spawned, and holds the owned process-group
ID. When the runner's lifeline closes, the anchor runs `kill -KILL 0`, ending the
whole group. This also carries host `SIGKILL` to the user group: the host releases
its resources when the runner exits, but capacity eviction and session shutdown
can kill a still-running runner before it can forward that signal.

After pipe EOF and leader exit, normal settlement sends `SIGTERM`, waits through a
bounded grace period, then releases the lifeline for the anchor's final group
kill. Direct mode has no capture pipe and settles the group at leader exit to
match native process lifetime. A descendant that calls `setsid()` leaves the
owned group and is the only escape; it must close or redirect inherited writers
so it does not delay capture EOF.

### Execution conformance matrix

Command semantics use raw `bash -c`; process lifetime uses native Codex CLI
0.156.1 `unified_exec`, which SIGKILLs the command's process group on release,
capacity eviction, and session shutdown. The pinned baseline is declared by
`ARG CODEX_VERSION=0.156.1` in `scripts/docker/Dockerfile`, and the
executable registry is `tests/hooks/_shell_conformance_matrix.py`. Each
invariant has executable cases in capture and direct mode.

| Invariant | Baseline | Statement |
|-----------|----------|-----------|
| `command-text` | raw_bash | The runner executes the command text verbatim as `bash -c <command>`. |
| `initial-trap-state` | raw_bash | The command starts with the same trap state as raw `bash -c`. |
| `user-trap-semantics` | raw_bash | User-installed, replaced, and cleared traps run with raw-compatible output and status. |
| `exit-status` | raw_bash | The shell-compatible status comes from the leader's own wait status; the runner injects no finalization into the shell. |
| `signal-status` | documented_difference | A command that dies by signal n reports shell-compatible status 128+n. |
| `merged-descriptors` | documented_difference | Capture mode merges stderr into the managed stdout pipe; direct mode keeps the streams separate. |
| `pipe-eof-completion` | documented_difference | In capture mode, same-group pipe holders are waited for until actual pipe EOF whatever their trap state or job style; in direct mode they are settled at leader exit. |
| `descendant-settlement` | native_codex | Same-group descendants that do not hold the pipe are settled after completion whatever their trap state or job style. |
| `setsid-escape` | documented_difference | A descendant that calls setsid() leaves the owned group and is not settled. |
| `host-lifetime` | native_codex | When the host kills or terminates the runner, the user process group ends with it. |

### Rejected alternatives

- **Harden the shell trap** (chain user traps, override `trap`, hide state). A
  shell-level finalizer cannot be made invisible and unbypassable, and `wait`
  cannot reach nested, disowned, or exec-orphaned jobs.
- **Release non-pipe descendants without signalling.** Native Codex 0.156.1
  kills them, and because the user group sits outside Codex's group they would
  leak without bound.
- **Wait until the owned group is empty.** This blocks on long-lived jobs until
  Codex times out or the session ends. Neither raw bash nor native Codex waits.
- **Bounded drain, then abandon.** ADR-0008 forbids a capture-local deadline.
- **Keep the leader-exit kill and report it as failure.** Outcomes stay
  trap-dependent, and legitimate scripts would get spurious failures.
- **PR_SET_PDEATHSIG or PR_SET_CHILD_SUBREAPER.** Both are Linux-only. PDEATHSIG
  reaches only the direct child, and both need `ctypes`. Codex's own PDEATHSIG
  shows the limit: it arms only Codex's direct child, which under the hook is the
  harness shell, never the user's group.
- **Execution-layer lease tether** (`_process_tether.py`). It is not stdlib-only
  and its sweep latency runs to minutes or hours.
- **Keep the command in Codex's group.** The runner could no longer settle,
  forward, or hand over the terminal without killing itself, and it would lose
  settlement authority on Codex's legacy `shell` path, which never kills the
  group.

### Verified snapshot and FINAL

After EOF, the runner closes its drain writer, retains the raw process wait
result, and distinguishes an exited command from a signaled command. The
shell-compatible return code is derived separately; for example, exit 143 and
signal 15 both map to 143 without becoming the same `CommandOutcome`.

`verify_capture_snapshot()` reads the retained carrier descriptor, recomputes the
one-pass measurement, revalidates project, root, carrier, and inode identities,
and syncs the completed carrier. Only its factory-created
`VerifiedCaptureSnapshot` can be committed.

`commit_verified_snapshot()` appends one framed ledger transition containing the
immutable `CaptureFinalManifest`. FINAL fixes the capture ID, incarnation,
physical identities, stream domain, byte count, digest, preview lengths, raw
command outcome, finalization revision, optional one-time reference hash and
expiry, and retention deadline. Later reference, delivery, retention, and cleanup
transitions preserve the manifest's canonical bytes exactly.

A failure before FINAL records bounded `CaptureFailureEvidence` and no manifest,
reference, or success marker. Runner-induced terminate or kill results are
settlement evidence and never a fabricated user command outcome. A failure after
FINAL cannot rewrite the capture as failed.

### V2 transport, reference, and delivery

Inline captures replay only verified snapshot bytes. They emit no capture marker
and issue no bearer reference.

Oversized captures issue at most one opaque V2 reference while committing FINAL.
The ledger stores only a domain-separated hash bound to the physical project,
capture root, snapshot identity, producer, version, and expiry. Publication
revalidates the already-issued tuple and cannot mint or rotate a token.

`render_capture_v2()` emits canonical bounded transport fields: schema version,
producer, capture ID, finalized and verified status, FINAL revision, byte count,
digest, typed raw command outcome, derived shell return, and either a published
opaque reference or an explicit unavailable reason. V2 contains no authoritative
path and no `complete=true` claim. `parse_capture_v2()` rejects duplicate,
additional, truncated, oversized, noncanonical, or wrongly typed fields.

Reference issuance is strictly at most once. If restart loses an issued or
published token before delivery begins, the reference becomes unavailable. An
attempting delivery becomes unknown and is never re-emitted. A delivered token
holder may resolve until expiry, subject to carrier verification and retention;
recovery never returns a replacement token.

Reference states are `not_requested`, `issued`, `published`, `unavailable`,
`unknown`, `expired`, and `revoked`.
Delivery states are `not_attempted`, `attempting`, `delivered`, `failed`, and
`unknown`. Delivered means that the runner's checked write-all loop accepted
every byte and the hook stdout stream was flushed before the durable finish
transition. It does not prove host receipt, tool-result completion, UI
observation, retained-history inclusion, or model visibility.

### Verified reading and lease ownership

`open_verified_capture(token)` authenticates the token, linearizes against the
ledger, opens the carrier relative to the retained root, acquires a shared lease,
and revalidates identity, size, and digest. `VerifiedCaptureReader.read()` permits
only exact bounded half-open reads and exposes no descriptor, write method, or
path authority.

The producer transfers its retained carrier and exclusive lease into a verified
reader and keeps that exclusive lease through publication and initial delivery.
Normal shared readers cannot resolve before producer release. Cleanup attempts a
nonblocking exclusive lease and defers while the producer or any reader remains
live. The existing runner-tail and cleanup-only `SessionStart` owners remain the
only owners of the single `shell-captures` lifecycle resource.

References expire no later than the one-hour retention deadline. Expiry stops new
resolution, as does an explicit revoked state; cleanup removes an eligible carrier
only after leases release and descriptor-relative identity checks pass.

### Ledger, filesystem, and durability boundary

Ledger frames use bounded canonical JSON, revisions, checksums, and ordered sync.
Recovery may truncate only an incomplete final frame. It rejects corrupt middle
frames, checksum mismatches, revision gaps, unknown versions, noncanonical data,
and conflicting manifests. Compaction preserves exact manifest and issuance
bytes.

The frame checksum detects accidental or torn corruption. It is not an
authenticated ledger head and cannot detect a clean valid-suffix truncation,
replay of an older valid ledger, or a hostile same-UID rewrite of payload and
checksum. Advisory `fcntl.flock` leases are a cooperative native-local-Linux
boundary. Network filesystems and hostile same-UID processes that ignore locks
are excluded.

The ordered carrier, frame, temporary-ledger, and parent-directory syncs support
process-termination recovery. They do not claim full OS-crash or power-loss
durability on every filesystem. After restart, any byte or manifest mismatch
fails closed rather than being served.

## Downstream contracts and non-goals

- Resolved here: #4323's trap isolation is satisfied by runner-owned completion —
  the runner does not install shell traps, executes the command verbatim, and
  owns completion, settlement, and lifetime (see Process lifetime ownership and
  Execution conformance matrix).
- [#4324](https://github.com/TalonT-Org/AutoSkillit/issues/4324) owns a future
  rendered-output ceiling. It consumes `capture_v2_encoded_length()` and
  `capture_v2_worst_case_bytes()`; `shell_max_rendered_bytes` is not implemented
  here.
- [#4325](https://github.com/TalonT-Org/AutoSkillit/issues/4325) owns bounded MCP
  retrieval. It consumes `open_verified_capture()` and
  `verify_reference_publication_binding()`; no public retrieval tool is
  implemented here.
- [#4326](https://github.com/TalonT-Org/AutoSkillit/issues/4326) owns private
  publication policy. V2 supplies an opaque reference, but broader access and
  disclosure policy is not implemented here.
- [#4327](https://github.com/TalonT-Org/AutoSkillit/issues/4327) owns partial and
  quota status. It consumes separate capture, reference, delivery, and retention
  states; quota accounting is not implemented here.
- [#4329](https://github.com/TalonT-Org/AutoSkillit/issues/4329) is upstream-gated
  live progress and visibility work. Hook-stdout flush is the current boundary;
  live UI or model visibility is not implemented here.
- [#4335](https://github.com/TalonT-Org/AutoSkillit/issues/4335) owns general
  snapshot-authority adoption, including MCP `run_cmd`. ADR-0008 does not extend
  Codex shell authority to those producers.

## Consequences

- FINAL authority comes only from one-pass measurement plus retained-descriptor
  verification.
- Oversized V2 output is useful only with the physical project authority and
  production resolver; filenames and transcript-wide marker substrings are not
  authority.
- Completeness can wait indefinitely for a live descendant writer, subject only
  to outer execution cancellation.
- Background work that does not hold the pipe is settled at completion whatever
  its trap state; `setsid()` is the escape.
- A regression that signals the group before EOF fails closed as
  `RUNNER_SETTLEMENT`.
- Reference and delivery ambiguity is explicit and permanent rather than repaired
  by token rotation or replay.
