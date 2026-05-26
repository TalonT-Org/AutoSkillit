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

`/autoskillit:analyze-pipeline-health <kitchen_id> [--dispatch-id=<id>] [--diagnostics-log-dir=<path>]`

- **kitchen_id** (required) — The kitchen session identifier to scope log queries.
- **--dispatch-id** (optional) — Fleet dispatch identifier. When non-empty, write a structured JSON report file in addition to text output.
- **--diagnostics-log-dir** (optional) — Resolved path to the diagnostics log directory. Defaults to ~/.local/share/autoskillit/logs/ if not provided.

## Critical Constraints

**NEVER:**
- Fabricate, embellish, or invent findings not supported by evidence in session data
- Modify any source code files
- Create issues or PRs (findings are reported to the calling session only)
- Run subagents in the background (run_in_background: true is prohibited)

**ALWAYS:**
- Filter sessions.jsonl by kitchen_id to scope to this pipeline run
- Spawn scanner subagents in parallel (one per step group)
- Use model: "haiku" for scanner subagents
- Report "no issues found" clearly when the pipeline is clean

## Workflow

### Step 1: Read sessions.jsonl

Read ~/.local/share/autoskillit/logs/sessions.jsonl and filter entries where kitchen_id matches the provided argument.

### Step 2: Group by step_name

Group the filtered entries by step_name. Each group represents one phase of the pipeline (e.g., plan, implement, test, merge).

### Step 3: Spawn scanner subagents

For each step group, spawn a scanner subagent via the Agent tool with subagent_type: "autoskillit:pipeline-health-scanner".

Each scanner receives in its prompt:
- The sessions.jsonl entries for its batch (JSON array)
- The session directory paths: ~/.local/share/autoskillit/logs/sessions/<dir_name>/
- Context about what the step does (derive from step_name)

Issue ALL Agent calls in a single message for parallel execution.

### Step 4: Consolidate findings

Collect results from all scanners. Produce a consolidated report:
- Group findings by severity (confirmed bugs > regressions > anomalies > informational)
- Include the scanner's evidence and adversarial validation status for each finding
- If no scanners found issues, report "Pipeline health check: no issues found"

### Step 5: Write report file (fleet dispatches only)

If --dispatch-id was provided and is non-empty:

1. Build a JSON report object with these fields:
   - kitchen_id: the kitchen_id argument value
   - dispatch_id: the --dispatch-id value
   - timestamp: current UTC ISO 8601 timestamp
   - findings: array of finding objects from Step 4, each with severity, step_group, summary, evidence
   - summary: the human-readable consolidated report text from Step 4

2. Determine the report directory:
   - If --diagnostics-log-dir was provided and non-empty, use {diagnostics-log-dir}/health-reports/
   - Otherwise use ~/.local/share/autoskillit/logs/health-reports/

3. Write the JSON to {report_dir}/{dispatch_id}_health_report.json using the Write tool.
   Create the health-reports/ directory first if it doesn't exist (use Bash: mkdir -p {report_dir}).

If --dispatch-id was not provided or is empty, skip this step entirely.

### Step 6: Output

Present the consolidated report as your final output text. The calling orchestrator session will receive this as the run_skill result.
