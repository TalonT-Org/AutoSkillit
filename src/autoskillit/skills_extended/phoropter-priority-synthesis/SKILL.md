---
name: phoropter-priority-synthesis
categories: [research, vis-lens]
description: >
  Resolve inter-lens conflicts via a configurable priority hierarchy and
  produce synthesis-result.md, report.md, and synthesis-trace.md for
  downstream worktree creation — the configurable-hierarchy counterpart to
  synthesize-vis-plan which hardcodes the priority order.
---

# Phoropter Priority-Synthesis Skill

Configurable priority-order synthesis step of the phoropter. Reads captured
lens token outputs from lens output files, resolves inter-lens conflicts via
the `--hierarchy` argument (defaulting to
`accessibility,anti-pattern,methodology-norms,chart-select`), and produces
three output files: a synthesis result, a conflict report, and a trace log.

Contrast with `synthesize-vis-plan`, which hardcodes the four-level priority
hierarchy and parses `yaml:figure-spec` fenced blocks. This skill reads
structured tokens (`selected_lenses`, `lens_context_paths`) instead and does
NOT parse `yaml:figure-spec` blocks.

## When to Use

- As the `synthesize` step of the vis-lens phoropter in the `research` recipe,
  after the apply step and before `create_worktree`
- on_success: `create_worktree`
- on_failure: `escalate_stop`

## Arguments

```
/autoskillit:phoropter-priority-synthesis {source_dir} {experiment_plan_path} {capture_dir} --hierarchy=<comma-separated priority order>
```

**Positional arguments:**

- `{source_dir}` — Absolute path to the source repo (the CWD before worktree
  creation)
- `{experiment_plan_path}` — Absolute path to the finalized experiment plan
  markdown
- `{capture_dir}` — Absolute path to the directory containing lens output
  files

**Named arguments:**

- `--hierarchy` — Comma-separated list of lens-category labels in priority
  order (index 0 = highest priority). Default:
  `accessibility,anti-pattern,methodology-norms,chart-select`. Consumed at
  Step 0 and controls the conflict resolution order in Step 2.

## Critical Constraints

**NEVER:**
- Fabricate, invent, or embellish information not present in the input files.
- Parse `yaml:figure-spec` fenced blocks (contrast with `synthesize-vis-plan`
  which does)
- Write outputs outside `{{AUTOSKILLIT_TEMP}}/phoropter-priority-synthesis/`
- Omit any of the three required path tokens (`synthesis_result_path`,
  `report_path`, `synthesis_trace_path`)
- Spawn sub-agents or run sub-agents in the background

**ALWAYS:**
- Emit all three tokens as literal plain text with no markdown formatting
- Log every conflict resolution as a row in the Conflict Resolution Log table
  with columns: Lens A, Lens A Rec, Lens B, Lens B Rec, Dimension, Winner,
  Reason
- Use `{{AUTOSKILLIT_TEMP}}/phoropter-priority-synthesis/` for all output
  paths
- Write all three output files unconditionally — even when no conflicts exist,
  write files with empty tables and zero-row logs

---

## Workflow

### Step 0 — Parse Arguments

Extract positional arguments:
- `source_dir` — the source repository path
- `experiment_plan_path` — path to the experiment plan
- `capture_dir` — directory containing lens output files

Parse `--hierarchy` into an ordered list of lens-category labels. Default to
`[accessibility, anti-pattern, methodology-norms, chart-select]` when absent.

### Step 1 — Read Lens Token Outputs

Read `selected_lenses` and `lens_context_paths` tokens from lens output files
in `capture_dir`. Do NOT parse `yaml:figure-spec` blocks. Build a per-lens
recommendation map from these structured tokens.

### Step 2 — Resolve Conflicts

Apply the ordered hierarchy from `--hierarchy` (index 0 = highest priority) to
resolve any dimension where two lenses disagree.

Write `synthesis-result.md` and `report.md` to
`{{AUTOSKILLIT_TEMP}}/phoropter-priority-synthesis/`.

Log every resolution as a row in the Conflict Resolution Log table:

| Lens A | Lens A Rec | Lens B | Lens B Rec | Dimension | Winner | Reason |
|--------|------------|--------|------------|-----------|--------|--------|

If no conflicts exist, write the Conflict Resolution Log table with headers
only and zero data rows.

### Step 3 — Write synthesis-trace.md

Write `synthesis-trace.md` to
`{{AUTOSKILLIT_TEMP}}/phoropter-priority-synthesis/synthesis-trace.md`.

Record:
- The resolved hierarchy order used
- Total conflict count
- Per-lens contribution summary
- The full Conflict Resolution Log

### Step 4 — Emit Structured Tokens

> **IMPORTANT:** Emit the structured output tokens as **literal plain text with
> no markdown formatting on the token names**. Do not wrap token names in
> `**bold**`, `*italic*`, or any other markdown. Do not wrap the output block
> in a code fence. The adjudicator performs a regex match on the exact token
> name — decorators and code fences cause match failure.

```
synthesis_result_path = {absolute_path_to_{{AUTOSKILLIT_TEMP}}/phoropter-priority-synthesis/synthesis-result.md}
report_path = {absolute_path_to_{{AUTOSKILLIT_TEMP}}/phoropter-priority-synthesis/report.md}
synthesis_trace_path = {absolute_path_to_{{AUTOSKILLIT_TEMP}}/phoropter-priority-synthesis/synthesis-trace.md}
```

All three tokens are mandatory — always emit non-null absolute paths. All
three output files are always written (even when no conflicts exist), so all
three tokens are always non-null.
