---
name: analyze-pipeline-health
categories: [diagnostics]
description: Analyze pipeline session logs for anomalies and regressions. Spawns parallel Haiku scanner subagents per step group, each investigating its batch of sessions and reporting findings with evidence.
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo '[SKILL: analyze-pipeline-health] Analyzing pipeline health...'"
          once: true
---

# Pipeline Health Analysis Skill

Coordinator skill that reads session logs from a pipeline run, groups them by step, spawns parallel scanner subagents, and consolidates findings into a report.

## Arguments

`/autoskillit:analyze-pipeline-health <kitchen_id>`

- **kitchen_id** (required) — The kitchen session identifier to scope log queries.

## Critical Constraints

**NEVER:**
- Modify any source code files
- Create issues or PRs (findings are reported to the calling session only)
- Run subagents in the background (`run_in_background: true` is prohibited)

**ALWAYS:**
- Filter sessions.jsonl by kitchen_id to scope to this pipeline run
- Spawn scanner subagents in parallel (one per step group)
- Use `model: "haiku"` for scanner subagents
- Report "no issues found" clearly when the pipeline is clean

## Workflow

### Step 1: Read sessions.jsonl

Read `~/.local/share/autoskillit/logs/sessions.jsonl` and filter entries where `kitchen_id` matches the provided argument.

```bash
jq -c 'select(.kitchen_id == "<kitchen_id>")' ~/.local/share/autoskillit/logs/sessions.jsonl
```

### Step 2: Group by step_name

Group the filtered entries by `step_name`. Each group represents one phase of the pipeline (e.g., plan, implement, test, merge).

### Step 3: Spawn scanner subagents

For each step group, spawn a scanner subagent via the Agent tool with `subagent_type: "autoskillit:pipeline-health-scanner"`.

Each scanner receives in its prompt:
- The `sessions.jsonl` entries for its batch (JSON array)
- The session directory paths: `~/.local/share/autoskillit/logs/sessions/<dir_name>/`
- Context about what the step does (derive from step_name)

Issue ALL Agent calls in a single message for parallel execution.

### Step 4: Consolidate findings

Collect results from all scanners. Produce a consolidated report:
- Group findings by severity (confirmed bugs > regressions > anomalies > informational)
- Include the scanner's evidence and adversarial validation status for each finding
- If no scanners found issues, report "Pipeline health check: no issues found"

### Step 5: Output

Present the consolidated report as your final output text. The calling orchestrator session will receive this as the `run_skill` result.
