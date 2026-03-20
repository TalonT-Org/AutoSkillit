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
├── sessions.jsonl                    # Append-only index (one JSON line per session)
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
2. **Flush**: After the session completes, `flush_session_log()` writes all data to disk
3. **Detect**: Anomaly detection runs over the complete snapshot series at flush time
4. **Index**: Each session appends one line to `sessions.jsonl` for quick scanning
5. **Retain**: Automatic cleanup keeps at most 500 session directories

## Configuration

In `.autoskillit/config.yaml`:

```yaml
linux_tracing:
  enabled: true          # default: true
  proc_interval: 5.0     # seconds between snapshots
  log_dir: ""            # empty = platform default, set absolute path to override
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
