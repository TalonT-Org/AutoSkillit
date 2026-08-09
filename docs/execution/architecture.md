# Architecture

How AutoSkillit runs a recipe end to end: orchestrator, kitchen gating, clone and worktree isolation, session model, and diagnostics.

## Overview

AutoSkillit is a Claude Code plugin that orchestrates automated workflows using headless sessions. It provides 69 MCP tools and 142 bundled skills, organized into a gated visibility system.

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

AutoSkillit uses a three-tier tool visibility model:

- **Free-range (4 tools)**: Always visible — `open_kitchen`, `close_kitchen`, `disable_quota_guard`, `reload_session`
- **Headless tools (7 tools)**: Revealed in headless sessions via
  `mcp.enable({'headless'})` — `test_check`, `unlock_agent_pack`, `commit_files`,
  `write_audit_semantic_result`, `write_standalone_audit_evidence`,
  `write_audit_disposition_bundle`, and `post_pr_review`.
- **Kitchen-tagged tools (45 tools total)**: Gated behind `open_kitchen` — `run_skill`,
  `run_cmd`, `run_python`, `merge_worktree`, `clone_repo`, `push_to_remote`, and 40 more.
  Six kitchen tools also carry the `headless` tag and are
  additionally pre-enabled in headless sessions. `post_pr_review` is headless-only and
  deliberately not application-gated.

When you call `open_kitchen` (automatically done by `order`), all 45 kitchen-tagged tools become
available for that session. This keeps normal Claude Code sessions clean — no pipeline tools
cluttering the tool list.

Functional category subsets (`github`, `ci`, `clone`, `telemetry`) can be disabled in config;
those tools remain hidden even after `open_kitchen`.
See **[MCP Tool Access Control](tool-access.md)** for the complete tool map.

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
  loaded. Sees 4 Free Range MCP tools (`open_kitchen`, `close_kitchen`, `disable_quota_guard`, `reload_session`) and Tier 1 skills only
  (`open-kitchen`, `close-kitchen`). After calling `/open-kitchen`, all 45 kitchen-tagged MCP
  tools become available.

- **`$ autoskillit cook`**: Interactive development session. Sees all three skill tiers
  (Tier 1+2+3) via an ephemeral session directory. MCP tools are initially ungated (same as
  `$ claude`); `/open-kitchen` reveals kitchen tools.

- **`$ autoskillit order`**: Pipeline orchestrator session. Kitchen is pre-opened at startup —
  all 69 MCP tools are available immediately. All skill tiers are accessible. The orchestrator
  delegates work through `run_skill` (headless sessions) and `run_cmd` (shell commands).

- **`run_skill` (headless)**: Worker sessions launched by the orchestrator. Sees 4 always-visible
  tools plus the 7 headless-tagged tools listed above. Cannot call `run_skill`, `run_cmd`, or `run_python`
  — enforced by hooks and code guards. Has access to all native Claude Code tools (Read, Write,
  Bash, etc.) and all skill tiers via `--add-dir skills_extended/`.

This prevents recursive session nesting and keeps the orchestrator as a pure routing engine.
See **[Skill Visibility](../skills/visibility.md)** for the full tier breakdown and configuration.

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
```

Before a view is created, all three roots must have the same `st_dev` and map
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

### PTY and hook-trust ownership

`_session_process.py` alone owns process groups, foreground-PGID transfer,
TERM/KILL escalation, group-empty verification, and direct-child reap.
`pty/_observer.py` owns raw-mode entry, window propagation, transparent relay,
semantic observation, signal-handler restoration, and master-FD closure.
The parent starts `pty/_exec.py` as a session leader; that minimal launcher
uses the inherited slave as controlling terminal, duplicates standard
streams, and immediately execs Codex. It does not perform a second session
transition.

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

Query the index: `jq 'select(.success == false)' ~/.local/share/autoskillit/logs/sessions.jsonl`

See **[Session Diagnostics](../developer/diagnostics.md)** for details.
