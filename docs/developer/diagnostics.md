# Session Diagnostics

## Overview

AutoSkillit captures two kinds of diagnostic output:

- **Structured logs** (structlog) go to stderr for real-time monitoring
- **Session diagnostics** go to files for post-hoc analysis of headless session behavior

Session diagnostics capture process-level data (memory, OOM scores, file descriptors, signals, CPU state) at regular intervals during headless sessions, then write structured JSON files after the session completes.

## Directory Structure

Logs are stored in a **global** directory (not per-project), so they persist across worktrees and clones.

### Platform Defaults

| Platform | Default Path |
|----------|-------------|
| Linux | `$XDG_DATA_HOME/autoskillit/logs` (defaults to `~/.local/share/autoskillit/logs`) |
| macOS | `~/Library/Application Support/autoskillit/logs` |

### Layout

```
~/.local/share/autoskillit/logs/
├── sessions.jsonl                    # Retained derived index (one row per committed session)
├── otlp.jsonl                        # Current scrubbed vendor-native OTLP capture
├── otlp.jsonl.1                      # Single rotated generation
└── sessions/
    └── {session_id}/                 # or pid_{pid}_{timestamp} if session_id unavailable
        ├── proc_trace.jsonl          # Full ProcSnapshot series
        ├── summary.json              # Session metadata and outcome
        └── anomalies.jsonl           # Present only if anomalies detected
```

## What Gets Captured

### ProcSnapshot Fields

| Field | Source | Description |
|-------|--------|-------------|
| `state` | psutil | Process state (running, sleeping, zombie, etc.) |
| `vm_rss_kb` | psutil | Resident set size in KB |
| `threads` | psutil | Thread count |
| `fd_count` | psutil | Open file descriptor count |
| `fd_soft_limit` | psutil | Soft limit for open file descriptors |
| `ctx_switches_voluntary` | psutil | Voluntary context switches |
| `ctx_switches_involuntary` | psutil | Involuntary context switches |
| `sig_pnd` | /proc | Pending signals bitmask (hex) |
| `sig_blk` | /proc | Blocked signals bitmask (hex) |
| `sig_cgt` | /proc | Caught signals bitmask (hex) |
| `oom_score` | /proc | OOM killer score (0-1000) |
| `wchan` | /proc | Kernel wait channel |

### Session Summary Fields

`summary.json` contains: `session_id`, `dir_name`, `pid`, `cwd`, `skill_command`, `success`, `subtype`, `exit_code`, `start_ts`, `snapshot_count`, `anomaly_count`, `peak_rss_kb`, `peak_oom_score`, `peak_fd_ratio`.

### Anomaly Types

| Kind | Condition | Severity |
|------|-----------|----------|
| `oom_spike` | OOM score delta > 200 between consecutive snapshots | warning |
| `oom_critical` | OOM score >= 800 | critical |
| `zombie_detected` | Process in zombie state | warning |
| `zombie_persistent` | Zombie state for >= 3 consecutive snapshots | critical |
| `signals_pending` | Pending signals transition from zero to non-zero | warning |
| `rss_growth` | RSS grows > 2x initial over 5+ snapshots | warning |
| `fd_high` | fd_count / fd_soft_limit > 0.80 | warning |

## How It Works

1. **Accumulate**: During a headless session, `LinuxTracingHandle` collects `ProcSnapshot` objects in memory at the configured interval (default 5s)
2. **Flush**: After the session completes, `flush_session_log()` writes all per-session artifacts
3. **Commit**: `summary.json` is published last and commits an eligible diagnostic session
4. **Index**: One exclusive transaction upserts the committed session into the retained `sessions.jsonl` projection
5. **Retain**: Automatic cleanup targets at most 2,000 committed directories; active-campaign protection may keep more

`sessions.jsonl` is a bounded derived index, not an append-only ledger. Its
`timestamp` field remains the session `start_ts`, not completion or index-write
time. Starting from an exact summary/index projection, a successful transaction
preserves that projection, while deterministic crash-recovery replay heals its
current key. Historical inconsistencies for other keys remain doctor-visible.
Publication is atomic for concurrent writers and process crashes, but does not
promise strict power-loss durability or snapshot isolation for unlocked readers.

## Native OTLP capture and correlation

Headless execution enables vendor-native logs and metrics against one
invocation-scoped loopback HTTP/JSON sink. Claude Code is activated with
`CLAUDE_CODE_ENABLE_TELEMETRY`, `OTEL_LOGS_EXPORTER`, and
`OTEL_METRICS_EXPORTER`. Codex derives native per-launch `[otel]` CLI overrides
from the same sink endpoints; Claude-only activation and exporter-selection
variables are removed from the Codex child environment. Interactive Codex and
persistent `config.toml` files are not changed.

The persisted `otlp.jsonl` wrapper adds only `signal` and `payload`; the payload
keeps the vendor's OTLP nesting and event vocabulary. Claude log records use the
`com.anthropic.claude_code.events` instrumentation scope and carry `session.id`.
Codex log records retain `event.name=codex.*` and carry `conversation.id`.
Those native values equal the corresponding `sessions.jsonl.session_id` and are
the direct join keys:

| Backend | Native log attribute | Authoritative session field |
|---|---|---|
| Claude Code | `session.id` | `sessions.jsonl.session_id` |
| Codex | `conversation.id` | `sessions.jsonl.session_id` |

`thread_id` remains the Codex rollout/notify and resume identifier; it is not
the OTLP attribute name. Codex metrics may omit `conversation.id`, so the direct
join guarantee applies to emitted log records, not every signal. Field
availability otherwise remains whatever the vendor emitted, and Codex does not
currently provide a stable specialized-agent identity (#4634).

PII scrubbing happens recursively before persistence, including nested OTLP
attribute lists. Native join and event-name attributes are retained while user,
account, organization, and email identifiers are removed. Prompt, assistant,
tool-content, and raw-API-body capture are not enabled by this integration.

Consumers must preserve raw accounting and stop metadata. Do not add
cache-read tokens to input tokens, add reasoning tokens to output tokens, or
treat `finish_reasons=["length"]` as proof of context exhaustion.
`sessions.jsonl` is still only the retained session projection: #4646 owns the
future watermark walk and #4647 owns the rebuildable joined index and writer.

## Configuration

In `.autoskillit/config.yaml`:

```yaml
linux_tracing:
  enabled: true          # default: true
  proc_interval: 5.0     # seconds between snapshots
  log_dir: ""            # empty = platform default, set absolute path to override
```

## Per-Run Enablement

By default, `pipeline_health` resolves from `diagnostics.pipeline_health` (shipped
default: `false`). To enable diagnostics for a single run, the orchestrator can pass
`overrides={"pipeline_health": "true"}` to `open_kitchen`. The override takes effect
for that kitchen session only and does not modify the persistent config.

The orchestrator can also lock the value for the session via
`lock_ingredients(locked={"pipeline_health": "true"})` to ensure it is not overridden
by downstream steps.

For post-hoc diagnostics on a completed run, use:
```bash
run_skill /autoskillit:analyze-pipeline-health <kitchen_id>
```

## Finding Problematic Sessions

```bash
# Sessions with anomalies
jq 'select(.anomaly_count > 0)' ~/.local/share/autoskillit/logs/sessions.jsonl

# Failed sessions
jq 'select(.success == false)' ~/.local/share/autoskillit/logs/sessions.jsonl

# View anomalies for a specific session
cat ~/.local/share/autoskillit/logs/sessions/{session_id}/anomalies.jsonl | jq .

# High memory sessions
jq 'select(.peak_rss_kb > 1000000)' ~/.local/share/autoskillit/logs/sessions.jsonl
```

## Disabling

Set `linux_tracing.enabled: false` in your config to disable all session diagnostics file output. Non-Linux platforms produce no output regardless of this setting.

## Path components use hyphens, not underscores

Log directory names and session folder names are hyphen-separated. Never assume
underscores when constructing or searching for log paths — a hyphen mismatch
causes ENOENT (the root cause of the session `f9170655` debugging session).
The invariant is documented in `AGENTS.md` §6 and enforced when
`execution/session_log.py` lays out per-session directories.
