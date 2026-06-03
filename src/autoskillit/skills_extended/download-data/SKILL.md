---
name: download-data
categories: [research]
backend_requirements: [claude-code]
uses_capabilities: [agent_model]
description: >
  Download external and gitignored datasets declared in the experiment plan's
  data_manifest. Executes acquisition commands sequentially into pre-created
  directories, verifies each download, and emits a PASS/WARN/FAIL verdict.
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo '[SKILL: download-data] Downloading external datasets...'"
          once: true
---

# Download Data Skill

Download external and gitignored datasets declared in the experiment plan's
`data_manifest` frontmatter section. Executes acquisition commands sequentially
into the directories pre-created by `stage_data`, verifies each download, and
emits a PASS/WARN/FAIL verdict. PASS and WARN proceed to `setup_environment`
(research.yaml) or `decompose_phases` (research-implement.yaml); FAIL escalates
immediately.

## When to Use

- Invoked by the research recipe's `download_data` step between `stage_data`
  and `setup_environment` (research.yaml) or `decompose_phases` (research-implement.yaml)
- Whenever external or gitignored datasets need to be acquired before experiment execution

## Arguments

```
/autoskillit:download-data <experiment_plan_path>
```

- `experiment_plan_path` — Absolute path to the experiment plan (positional).
  Default: `$AUTOSKILLIT_TEMP/experiment-plan.md`.

## Critical Constraints

**NEVER:**
- Modify the experiment plan or any source files
- Run subagents in the background (`run_in_background: true` is prohibited)
- Skip the `depends_on` command when it is specified for an entry
- Proceed with a failed download without escalating
- Write files outside `{{AUTOSKILLIT_TEMP}}/download-data/`
- Fabricate or hallucinate download results — only report actual command output
- Attribute a missing or partial file to a completed download
- Issue subagent Task calls sequentially — ALL must be in a single parallel message

**ALWAYS:**
- Read the `data_manifest` frontmatter section of the experiment plan
- Filter for `source_type: external` and `source_type: gitignored` entries only
- Skip entries that lack an `acquisition` command (e.g., `source_type: literature`
  or `source_type: database` entries without download commands)
- Execute `depends_on` before `acquisition` for the same entry
- Verify each download using the entry's `verification` criteria
- Write the download report before emitting the verdict token
- Spawn all subagents via `Agent(model="sonnet")`
- Issue all Task calls in a single message to maximize parallelism

## Workflow

### Step 1 — Parse the Experiment Plan

Read the experiment plan at the provided path (or default path). Parse the
`data_manifest` YAML frontmatter section. Identify all entries where
`source_type` is `"external"` or `"gitignored"`. Skip `synthetic`, `fixture`,
`literature`, `database`, and `wet_lab` entries — these do not require external
data downloads.

### Step 2 — Short-Circuit for No-Downloads Plans

If no `external` or `gitignored` entries with `acquisition` commands exist,
emit `verdict = PASS` with a note that no external data downloads are required.
Write a minimal download report and exit. This covers synthetic/fixture-only
plans and plans with only `source_type: literature`, `source_type: database`,
or `source_type: wet_lab` entries.

### Step 3 — Execute Acquisition Commands Sequentially

For each `external`/`gitignored` entry (respecting `depends_on` ordering):

**a. Skip entries without acquisition commands** — Entries whose `source_type`
is `literature` or `database` and lack an `acquisition` field are skipped.

**b. Handle retry cleanup** — If this is a retry (the step's `retries: 1`
triggers re-execution on failure), remove any partial output files at the
entry's target location before proceeding to prevent partial-file corruption
on re-download.

**c. Execute `depends_on` if specified** — If the entry has a `depends_on`
field, execute that command first in the worktree CWD.

**d. Execute the acquisition command** — Run the `acquisition` command via Bash
in the worktree CWD. Pass `timeout=14400000` (ms) to each Bash invocation to
prevent the tool's 120 s default from killing multi-GB downloads; the step-level
`stale_threshold: 14400` provides the outer bound. Log stdout/stderr to:
```
{{AUTOSKILLIT_TEMP}}/download-data/download_{entry_index}_{timestamp}.log
```

**e. Verify the download** — After each command completes, run the `verification`
check. Verification forms include:
- checksum file comparison (`.md5`, `.sha256` sidecars)
- minimum file size threshold
- directory non-empty assertion
- custom shell command specified in the entry's `verification` field

### Step 4 — Assess Results

Build a download results table:
```
| Entry | Source Type | Location | Status | Duration |
| ...   | external    | data/geo/| OK     | 42m 18s  |
| ...   | external    | data/rna/| FAILED | 12m 03s  |
```

- **PASS**: all entries succeeded with no warnings
- **WARN**: all entries completed but at least one triggered a recoverable condition
  (e.g., a verification check passed after retry, or the directory is non-empty but
  below the expected size threshold)
- **FAIL**: any entry failed and could not be recovered

### Step 5 — Write the Download Report

Save to:
```
{{AUTOSKILLIT_TEMP}}/download-data/download_report_{YYYY-MM-DD_HHMMSS}.md
```

Report structure:
```markdown
## Download Report
**Date:** {timestamp}
**Verdict:** PASS | WARN | FAIL

### Download Results
| Entry | Source Type | Location | Status | Duration |
|-------|-------------|----------|--------|----------|
...

### Individual Logs
- {entry}: {{AUTOSKILLIT_TEMP}}/download-data/download_{entry_index}_{timestamp}.log
```

### Step 6 — Emit Structured Output Tokens

Emit structured output tokens as LITERAL PLAIN TEXT with NO markdown
formatting on the token names. Do not wrap token names in `**bold**`,
`*italic*`, or any other markdown. The adjudicator performs a regex match
on the exact token name — decorators cause match failure.

```
verdict = PASS|WARN|FAIL
download_report = /absolute/path/to/download_report_{YYYY-MM-DD_HHMMSS}.md
```

## Output

Include the completion marker from the ORCHESTRATION DIRECTIVE at the end of the
structured output block.

> **IMPORTANT:** Emit the structured output tokens as **literal plain text with no
> markdown formatting on the token names**. Do not wrap token names in `**bold**`,
> `*italic*`, or any other markdown. Do not wrap the output block in a code fence.
> The adjudicator performs a regex match on the exact token name — decorators and
> code fences cause match failure.

```
verdict = PASS|WARN|FAIL
download_report = /absolute/path/to/{{AUTOSKILLIT_TEMP}}/download-data/download_report_{YYYY-MM-DD_HHMMSS}.md
%%ORDER_UP::<hex>%%
```