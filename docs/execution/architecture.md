# Architecture

How AutoSkillit runs a recipe end to end: orchestrator, kitchen gating, clone and worktree isolation, session model, and diagnostics.

## Overview

AutoSkillit is a Claude Code plugin that orchestrates automated workflows using headless sessions. It provides 74 MCP tools and 141 bundled skills, organized into a gated visibility system.

## Core Concepts

### Recipes
YAML pipeline definitions that describe a sequence of steps. Each step invokes an MCP tool or a skill. Recipes define the flow; skills do the work.

### Skills
Markdown instruction files (`SKILL.md`) that define what a headless Claude session should do. Skills are registered as `/autoskillit:*` slash commands. Each skill runs in its own context window, so pipelines can run for hours without hitting context limits.

Skills that adopt specialized repository exploration declare a reviewed vector inventory in a
per-skill `exploration.yaml` sidecar (slim schema: `vectors` for migrated entries, `retained` for
prose-only review ledger entries). Exact HTML markers in SKILL.md bind each vector to its canonical
prose. Source resolution validates the sidecar schema and marker coverage; session projection then
builds a deterministic router plan and replaces only migrated marker bodies after the backend is
bound. Claude materializes native `Agent` calls and Codex materializes native `spawn_agent` calls,
both using the same typed task packets and parent-owned merge/synthesis rules. Retained vectors
remain prose, so a conditional or unsupported investigation is not silently promoted to
unconditional native dispatch. The projection-cache `skill_identity` includes the sidecar content
digest, so sidecar-only edits bust the cache. See [Explorer Agents](explorer-agents.md).

### The Orchestrator
When you run `autoskillit order`, Claude Code acts as a pipeline orchestrator. It reads the recipe, collects ingredients from you, and executes steps in sequence. The orchestrator never reads or writes code itself — it delegates all work through `run_skill` (headless sessions) and `run_cmd` (shell commands).

## Tool Visibility (Kitchen Gating)

AutoSkillit uses several overlapping tool visibility surfaces:

- **Free-range (8 tools)**: Ordinarily visible — `open_kitchen`, `close_kitchen`,
  `disable_quota_guard`, `enable_exploration`, `reload_session`, `configure_fleet`,
  `configure_order`, and `lock_ingredients`.
- **Headless tools (8 tools)**: Revealed in headless sessions via
  `mcp.enable({'headless'})` — `test_check`, `unlock_agent_pack`, `commit_files`,
  `write_audit_semantic_result`, `write_standalone_audit_evidence`,
  `write_audit_disposition_bundle`, `post_pr_review`, and `delegate_evidence_reader`.
- **Kitchen-tagged tools (51 tools total)**: Gated behind `open_kitchen` — `run_skill`,
  `run_cmd`, `run_python`, `merge_worktree`, `clone_repo`, `push_to_remote`, and 45 more.
  Seven kitchen tools also carry the `headless` tag and are
  additionally pre-enabled in headless sessions. `post_pr_review` is headless-only and
  deliberately not application-gated.

The two authenticated evidence-reader broker tools are outside the kitchen, free-range,
and fleet surfaces. A sterile evidence-reader child presents a complete private startup identity
that reveals exactly those two brokers; partial or malformed identity fails startup closed.

When you call `open_kitchen` (automatically done by `order`), all 51 kitchen-tagged tools become
available for that session. This keeps normal Claude Code sessions clean — no pipeline tools
cluttering the tool list.

Functional category subsets (`github`, `ci`, `clone`, `telemetry`) can be disabled in config;
those tools remain hidden even after `open_kitchen`.
See **[MCP Tool Access Control](tool-access.md)** for the complete tool map.

### Behavioral evidence readers

A writable headless L1 Codex session can call `delegate_evidence_reader` without giving up its own
write authority. The pilot `pr-source-reader` accepts only a repository-relative artifact path and
a bounded requested-field list. AutoSkillit captures the trusted worktree's current one-artifact
state—including dirty, staged, or untracked content—and serves immutable pages through two
authenticated brokers with receipt-backed citations.

The reader is a separate top-level Codex process in a sterile home and working directory. It uses
the role's fixed model and catalog projection, a `read-only` sandbox, `never` approvals, no direct
repository mount, and no command, delegation, web, app, plugin, or permission-request surface.
AutoSkillit validates its result and citations, recaptures the artifact for staleness, then revokes
the authority and verifies process and generated-state cleanup before returning success.

This is a behavioral evidence boundary, not a claim of complete native-tool observability. The
gate attests generated configuration, catalog projection, the exact configured AutoSkillit MCP
allowlist, observed calls, and adversarial canaries; Codex does not expose a complete inventory of
all built-in tools it offered. See
[MCP Tool Access Control](tool-access.md#behavioral-evidence-readers) for the caller and broker
contract.

## Clone Isolation

All pipeline work happens in a cloned copy of your repository:

1. `clone_repo` creates a full clone at `../autoskillit-runs/<run>-<timestamp>/`
2. Your working tree and uncommitted changes are never touched
3. The clone's `origin` remote is rewritten to prevent Claude Code from confusing the clone with your real project
4. After the pipeline, you choose whether to keep or delete the clone

## Worktree Isolation

Within the clone, implementation happens in git worktrees:

1. `implement-worktree-no-merge` creates a worktree branched from the feature branch
2. Code changes are committed phase by phase inside the worktree
3. `merge_worktree` rebases the worktree onto the target branch, runs tests, and merges
4. The worktree is cleaned up after a successful merge

## Session Model

AutoSkillit supports four session modes with different tool and skill visibility:

- **`$ claude` (plugin, no kitchen)**: Regular Claude Code session with the AutoSkillit plugin
  loaded. Sees the 8 Free Range MCP tools and Tier 1 skills only
  (`open-kitchen`, `close-kitchen`). After calling `/open-kitchen`, all 51 kitchen-tagged MCP
  tools become available.

- **`$ autoskillit cook`**: Interactive development session. Sees all three skill tiers
  (Tier 1+2+3) via an ephemeral session directory. MCP tools are initially ungated (same as
  `$ claude`); `/open-kitchen` reveals kitchen tools.

- **`$ autoskillit order`**: Pipeline orchestrator session. Kitchen is pre-opened at startup.
  The authenticated evidence-reader brokers remain hidden among the 74 registered MCP tools
  because only a separately launched reader child receives their binding. All skill tiers are
  accessible. The orchestrator delegates work through `run_skill` (headless sessions) and
  `run_cmd` (shell commands).

- **`run_skill` (headless)**: Worker sessions launched by the orchestrator. Sees 8 always-visible
  tools plus the 8 headless-tagged tools listed above. Cannot call `run_skill`, `run_cmd`, or `run_python`
  — enforced by hooks and code guards. Has access to all native Claude Code tools (Read, Write,
  Bash, etc.) and all skill tiers via `--add-dir skills_extended/`.

This prevents recursive session nesting and keeps the orchestrator as a pure routing engine.
See **[Skill Visibility](../skills/visibility.md)** for the full tier breakdown and configuration.

## Join Contract and Batch Admission

A skill that declares `semantic_requirements.join.required: true` enters a join-bound session the moment Claude loads it. The hook layer carries the join policy and the batch ledger:

1. **`declare_join_batch`** opens one parent/wave with resolved assignment labels, validates the loaded skill's `join.required` and `child_spawn_cardinality` against the projection manifest, and returns a fresh `join_batch_id`.
2. **JoinLedger** keys membership and outcomes by `(session_id, top_level_parent, join_batch_id, assignment, tool_use_id)`. Persistence uses `fcntl.flock` + atomic `os.replace` (`hooks/_join_ledger.py`).
3. **Claim → Settle → Stop lifecycle** —
   * `join_claim_guard` (PreToolUse, matcher `Agent`) atomically claims one declared assignment per top-level direct `Agent` `tool_use_id`.
   * `join_settle_guard` (PostToolUse + PostToolUseFailure) maps the upstream event to one of `success / failure / timeout / cancelled / interruption / missing` and records the outcome on the claimed handle. Empty results are mapped to `missing`, never `success`.
   * `join_followup_guard` (matcherless PreToolUse) denies non-`Agent` side-effecting calls while a wave is unresolved.
   * `join_stop_guard` (Stop, exit code 2) blocks Claude from completing until the wave is `complete`.
4. **Backend admission** — `BackendCapabilities.fixed_set_join_capable` is statically `True` only for Claude Code, and only when the full guard set is registered in the same commit. Codex returns `unsupported_operation(REQUIRED_JOIN)` at admission; current Codex has wait-any/mailbox semantics, not fixed-set fan-in.
5. **Session binding monotonicity** — `skill_load_post_hook.py` writes a JSON envelope with OR-accumulated `join_required`. A later join-false Skill load does not downgrade an established binding. A missing or unreadable projection manifest fails closed by forcing `join_required: true` so dispatch guards refuse all join-bearing work.
6. **Repository force-inactive option** — `agent_backend.force_claude_agent_teams_inactive` (default False) neutralizes `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS` and detects conflicting entries in the target repository's `.claude/settings.json` / `.claude/settings.local.json`. Repositories with the option disabled remain byte-for-byte unchanged.

The session flag carries join policy; the manifest carries projection identity; the ledger carries wave state. Each is read by the matching hook family. None of them is a duplicate authority — they are three projections of one decision.

## Durable Codex Cook Sessions

Codex cook history uses a per-attempt view rather than exposing the complete
canonical history beneath the generated `CODEX_HOME`. The identifiers are
deliberately distinct:

- `launch_id` is the 16-character identifier for one complete `cook()` call.
- `attempt` starts at 1 and increases for each reload.
- `view_id` is `<launch_id>-<attempt>` and names one durable attempt view.
- The Codex `thread_id` identifies a rollout and is never used as a launch or
  view identifier.

### Generated-home and configuration transaction

`DefaultSessionSkillManager.managed_session()` acquires the generated-home
lease before materialization and holds it across plugin resolution, explicit
history recovery, the resume picker, and every reload. It removes and verifies
the generated home before releasing that lease.

Three paths remain separate throughout the launch:

- `generated_home` owns `CODEX_HOME`, `CODEX_SQLITE_HOME`, inert rollout
  targets, and disposable state.
- `project_dir` is the canonical working directory used by native validation
  and the child.
- `skills_dir` is the interactive `--add-dir`.

The backend builds one immutable `CmdSpec`, including profile, trust, root, and
`sqlite_home` overrides. Cook replaces only its `cwd` with the canonical
project path and passes that exact instance to
`validate_interactive_invocation()`, `cook_session_context()`, and the child.
Ambient and profile-supplied `CODEX_HOME` or `CODEX_SQLITE_HOME` values cannot
override the generated home.

Before an attempt is entered, `sessions` and `archived_sessions` are symlinks
to private, empty inert directories within the generated home. Attempt entry
atomically points them at the view; every exit restores and verifies the inert
links.

### Canonical stores and attempt views

The authoritative roots beneath `default_log_dir()` are:

```text
codex-sessions/                    # canonical active rollouts
codex-archived-sessions/           # canonical archived rollouts
codex-active-sessions/
  <view-id>/
    manifest.json
    sessions/                      # active side of this attempt
    archived_sessions/             # archived side of this attempt
codex-attempt-reconciliations/     # immutable operator discard audits
codex-attempt-reconciliation-tombstones/  # crash-recoverable deletion staging
```

Before a view is created, all five roots must have the same `st_dev` and map
to one recognized local filesystem. Network, remote, cross-device, and
unclassifiable mounts fail closed because the design depends on hard-link
identity and advisory-lock behavior. Paths are containment-checked; symlink
and non-regular rollout inputs are rejected.

A fresh view begins empty. A named resume locates exactly one canonical
rollout and hard-links it at the same relative path on the matching active or
archive side. Copying is prohibited. SQLite databases and sidecars remain
inside the disposable generated home and are never promoted.

Each atomically written, directory-fsynced manifest records schema version,
launch/attempt/view identity, lifecycle state, optional resume thread and
source, child PID/PGID, durable reap proof, and final canonical store/path.
States are `prepared`, `running`, `finalizing`, `complete`, or `failed`.

### Lock order, process proof, and promotion

Lock acquisition order is:

1. generated-home lease;
2. attempt-view lease;
3. resume-thread lease, when applicable;
4. short-lived lifecycle lock for promotion, recovery, and index mutation.

The separate canonical-config lock may be held inside the generated-home
lease during prelaunch synchronization, but it is released before any view or
thread lease is acquired. No code waits for a long-lived lease while holding
the lifecycle lock.

The generated-home, view, and thread descriptors are inherited by the child.
Immediately after `Popen` returns, `record_spawn(pid, pgid)` durably changes
the manifest to `running`. Only after the whole process group is empty and the
direct child is reaped may `record_reaped()` write the matching proof.
Finalization refuses to promote without it. A pre-spawn failure restores the
inert links and removes the validated never-running view; an ambiguous or
colliding view is retained for diagnosis.

Promotion never overwrites. Recovery applies this inode table, where identity
means exactly `(st_dev, st_ino)`:

| Staged source | Canonical destination | Action |
|---|---|---|
| present | absent | Hard-link destination, verify identity, fsync file/directory, then unlink source |
| present | same inode | Unlink the redundant staged source |
| absent | expected inode present | Treat the interrupted promotion as complete |
| present | different inode present | Preserve both and report a collision |
| absent | absent | Report missing data and retain the view |

Supported `.jsonl` to `.jsonl.zst` representation transitions first make the
new same-thread representation durable, then retire the old canonical name.
A crash may temporarily leave both; recovery completes the transition only
when thread identity and manifest intent agree.

`recover_cook_history()` is explicit and idempotent. Bare resume calls it
before listing; ordinary fresh startup does not scan canonical history.
Canonical active/archive files remain authoritative. The bounded
`codex-session-index.json` is only an atomically replaced, rebuildable
`SessionSummary` snapshot; locator listing reads it without hidden mutation.

Schema-v1 attempts that were spawned and retained without rollout material remain
unknown; automatic recovery continues to fail closed. Operators can inspect them with
`autoskillit codex-attempts`. An explicit discard requires the exact view ID and a
non-empty reason, then revalidates the bounded manifest and strictly empty staged trees
while holding the view lock, sorted thread locks, and lifecycle lock. It publishes an
immutable manifest-digest audit before atomically renaming only that view to its
deterministic tombstone. Parent directories are fsynced before recursive tombstone
deletion. Retry either revalidates an intact view, resumes deletion of an authorized
tombstone, or reports an already-recorded reconciliation. Canonical stores, the launch
registry, and the derived index are never changed by this operation.

Clean-empty lifecycle completion remains blocked for interactive Codex 0.147.0. Its
exec JSONL and app-server thread events occur only after explicit `thread/start`, so
their absence is not a final negative proof for TUI `/quit`; OS SID and PTY identity
prove process startup only. The attempt manifest therefore remains schema v1 with
spawn/reap evidence, and no terminal-disposition callback or proven-empty completion
path is implemented until a version-pinned, attempt-bound host signal can distinguish
session establishment from no session started.

### PTY and hook-trust ownership

`_session_process.py` alone owns process groups, foreground-PGID transfer,
TERM/KILL escalation, group-empty verification, and direct-child reap.
`pty/_observer.py` owns raw-mode entry, window propagation, transparent relay,
semantic observation, signal-handler restoration, and master-FD closure.
The parent starts `pty/_exec.py` as a session leader; that minimal launcher
uses the inherited slave as controlling terminal, duplicates standard
streams, and immediately execs Codex. It does not perform a second session
transition.

Managed async, managed sync, and active-cook launches receive group authority
only from the controller that atomically spawned the direct child as a fresh
group leader. While that leader remains unreaped, it fences PGID reuse and the
controller may perform bounded TERM/KILL settlement before the sole reap.
Persisted PID/PGID values and recovery scans are observation-only: they can
target only identities and ancestry they positively establish and cannot
reconstruct group authority. Cleanup completeness means the requested bounded
observation finished without survivors or denied operations; it is not a
permanent group-emptiness proof, and descendants that leave the process group
are outside the owned-group scope.

Interactive Codex configs deliberately use
`HookTrustPolicy.REVIEW_EACH_SESSION`, so fresh, resumed, and reload commands
do not bypass hook review. Automated skill and food-truck builders retain
their explicit hook-trust bypass because they have a separate non-interactive
trust contract.

The opt-in installed-Codex canary is a release gate for each supported
version. It must prove that fresh and resumed writes remain on the staged
inode (or follow an explicitly supported representation transition) and that
a live Codex process retains the inherited lease after the parent closes its
copy. Failure blocks the hard-link design for that version.

### Exception rendering ownership

TTY logging renders the standard plain traceback. JSON and non-TTY logging retain
exception type, message, and stack locations but never serialize arbitrary frame
locals. For Codex attempt cleanup, the lease emits one concise structured event with
the view ID and exception type, then rethrows. Process-runner failure and cleanup
events likewise retain structured identity context without attaching their own
traceback. The outer CLI process is the sole owner of the user-facing traceback.

### Claude MCP addressability

Interactive Claude launches bind one canonical executable and a sealed
environment before the mandatory version probe. The client-owned MCP
connection deadline governs the initial tool-list snapshot; the server
readiness sentinel does not establish client addressability. Fresh-session
prompt retry is a bounded defense after a pre-dispatch failure. A received
`CallToolResult` crosses into tool/application recovery and cannot be
reclassified as a startup transport failure.

See [Claude startup readiness](claude-startup-readiness.md) for the pinned
versions, exact environment values, trace contract, and resume boundary.

## Authoritative pull-request review publication

Headless review skills publish through `post_pr_review`; they do not issue raw
GitHub review mutations. The server computes a deterministic operation key
from canonical repository, PR, requested head, logical iteration, and sorted
findings, then records preparation before network access. Non-dry operations
use an owner-private SQLite ledger at the platform AutoSkillit state location
(`$XDG_STATE_HOME/autoskillit/github-mutations/ledger.sqlite3` on Linux, with
the standard state-directory fallback). The ledger stores operations,
canonical findings, exact attempts, receipts, and salted credential/API-origin
rate scopes, but never credentials.

Every request is pinned to the supplied head SHA and carries server-generated
operation/finding markers. Transport errors and 5xx responses are reconciled
by read only; they are never blindly retried. Structured anchor-validation
422 responses may produce at most one strict-subset attempt, while verified
secondary-rate responses persist shared back-pressure. A capacity-one fenced
lease enforces at least one second between review mutation starts across
sessions.

Final success requires a server-authored receipt with the operation identity,
review/comment IDs, response and reconciliation classes, and an exhaustive
posted/already-present/omitted disposition for every original finding. Dry-run
performs validation, canonicalization, cap enforcement, identity computation,
and planned counts without credentials, SQLite, sleeps, receipts, or network
access.

## Safety

See **[Hooks](../safety/hooks.md)** for the complete safety system: protected branches, quota management, format validation, and session boundary enforcement.

## Session Diagnostics

Pipeline sessions are logged to `~/.local/share/autoskillit/logs/` (Linux) or `~/Library/Application Support/autoskillit/logs/` (macOS). Each session records token usage, timing, and process traces.

`summary.json` is the committed completion witness and is published last among
per-session artifacts. A shared exclusive-lease transaction upserts the session
row, applies committed-directory retention, and atomically replaces the bounded
`sessions.jsonl` derived index. Doctor compares retained summary parents with
physical index-row multiplicity under the corresponding shared lease.

Query the index: `jq 'select(.success == false)' ~/.local/share/autoskillit/logs/sessions.jsonl`

See **[Session Diagnostics](../developer/diagnostics.md)** for details.
