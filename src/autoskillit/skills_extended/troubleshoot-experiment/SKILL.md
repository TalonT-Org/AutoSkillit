---
name: troubleshoot-experiment
categories: [research]
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo '[SKILL: troubleshoot-experiment] Diagnosing experiment failure...'"
          once: true
---

# troubleshoot-experiment Skill

Read session logs and process traces for a failed research pipeline step, classify
why it failed, write a structured diagnosis artifact, and emit `is_fixable` signal
for orchestrator routing.

Called by the `research` recipe on `implement_phase` failure before routing to
`plan_phase` (fixable) or `escalate_stop` (not fixable).

## Invocation

```
/autoskillit:troubleshoot-experiment {worktree_path} {step_name}
```

**Positional args:**
- `{worktree_path}` — absolute path to the worktree where execution failed
- `{step_name}` — name of the failed pipeline step (e.g. `implement_phase`)

## Critical Constraints

**NEVER:**
- Modify any source code files
- Run tests
- Write files outside `{{AUTOSKILLIT_TEMP}}/troubleshoot-experiment/`
- Abort when session data is missing — emit `failure_type=unknown`, `is_fixable=false` and exit cleanly

**ALWAYS:**
- Write the diagnosis file before emitting output tokens
- Emit the three output tokens (`diagnosis_path`, `failure_type`, `is_fixable`) at the end

## Workflow

### Step 1: Locate the Most Recent Failed Session for this Worktree

Query the global session index:
```bash
jq -r 'select(.success == false)' \
  ~/.local/share/autoskillit/logs/sessions.jsonl \
  | jq -r --arg worktree_path "{worktree_path}" 'select(.cwd // "" | startswith($worktree_path))' \
  | tail -n 1
```

If the `cwd` field is absent from matching records, fall back to the most recent
`success=false` entry regardless of `cwd`. Extract `session_id` from the record.

If `sessions.jsonl` does not exist or no matching session is found, proceed to
Step 5 with a minimal diagnosis noting the log lookup failure. Never abort.

### Step 3: Read Session Diagnostics

From `~/.local/share/autoskillit/logs/sessions/{session_id}/`:

- `summary.json` — identity fields: `termination_reason`, `write_call_count`,
  `exit_code`, `anomaly_count`, `peak_rss_kb`, `elapsed_seconds`
- `anomalies.jsonl` — structured anomaly records (one per line): `kind`,
  `severity`, `detail`

Also read from `{worktree_path}/{{AUTOSKILLIT_TEMP}}/run-experiment/` any
`results_*.md` files (if the failed step was `run_experiment` and produced
partial results).

If `summary.json` is not found, emit `failure_type=unknown`, `is_fixable=false`
and proceed to Step 5.

### Step 4: Classify Failure Type

Apply this decision table in priority order (stop at the first match):

| Priority | Condition | `failure_type` | `is_fixable` |
|----------|-----------|----------------|--------------|
| 1 | `termination_reason == "context_limit"` | `context_exhaustion` | `true` |
| 2 | `termination_reason == "stale"` | `stale_timeout` | `true` |
| 3 | `exit_code != 0` AND logs contain build/compile error keywords (`SyntaxError`, `ModuleNotFoundError`, `ImportError`, `error: command`) | `build_failure` | `true` |
| 4 | `step_name == "run_experiment"` AND (`blocked_hypotheses` token present in run-experiment results OR results file contains `## Status: FAILED` with data acquisition errors) | `data_missing` | `true` |
| 5 | Logs contain environment/infra keywords (`Permission denied`, `No such file or directory` for system paths, `Connection refused`) | `environment_error` | `false` |
| 6 | `anomaly_count > 0` AND any anomaly `kind` in `["oom_critical", "zombie_persistent"]` | `environment_error` | `false` |
| 7 | All other cases | `unknown` | `false` |

### Step 5: Write Diagnosis Report

Create directory `{{AUTOSKILLIT_TEMP}}/troubleshoot-experiment/` if it does not exist.
Write the diagnosis file to:

`{{AUTOSKILLIT_TEMP}}/troubleshoot-experiment/diagnosis_{YYYY-MM-DD_HHMMSS}.md` (relative to the current working directory)

```markdown
# Experiment Failure Diagnosis

## Context
- Step: {step_name}
- Worktree: {worktree_path}
- Session ID: {session_id}
- Diagnosis Time: {timestamp}

## Failure Classification
- **failure_type:** {type}
- **is_fixable:** {true|false}

## Evidence
- termination_reason: {value}
- write_call_count: {value}
- exit_code: {value}
- anomaly_count: {value}
- elapsed_seconds: {value}

## Anomalies Detected
{list anomalies from anomalies.jsonl, or "none"}

## Recommended Action
{One paragraph describing what likely happened and what the orchestrator should do next:

- stale_timeout: "The agent was idle while a background task ran. Re-attempt the phase
  with a revised plan scope or split long-running operations into separate steps."
- context_exhaustion: "The session hit the context limit mid-implementation. Retry
  the phase — the committed artifacts remain intact."
- build_failure: "A code build or import error prevented completion. The next phase
  attempt should focus on resolving the identified error."
- data_missing: "Required experiment data was not available. Adjust experiment
  parameters or data acquisition strategy."
- environment_error/unknown: "Automated remediation is not feasible. Human review
  of the session log is required."}
```

### Step 6: Emit Structured Output Tokens

Emit these tokens on their own lines at the end of the response as literal plain text
with no markdown formatting on the token names. The adjudicator performs a regex match
on the exact token name — decorators cause match failure.

```
diagnosis_path = {absolute_path_to_report}
failure_type = {stale_timeout|context_exhaustion|build_failure|data_missing|environment_error|unknown}
is_fixable = {true|false}
```

## Graceful Degradation

If at any point session data is unavailable (missing `sessions.jsonl`, no matching
session, missing `summary.json`):
- Set `failure_type = unknown`
- Set `is_fixable = false`
- Write a minimal diagnosis noting the specific lookup failure
- Emit output tokens and exit cleanly — never abort

## gh Unavailable Fallback

This skill does not require `gh`. It reads only local session log files.
