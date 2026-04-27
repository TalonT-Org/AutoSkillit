---
name: validate-audit
categories: [audit]
description: Validate audit findings from audit-arch, audit-tests, audit-cohesion, or audit-feature-gates against actual code, git history, and design intent using 9–10 parallel subagents. Removes contested findings, documents exceptions, adjusts severities. Use when user says "validate audit", "validate findings", "validate report", or "check audit results".
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo '[SKILL: validate-audit] Validating audit findings against code...'"
          once: true
---

# Validate Audit Findings Skill

Validate audit findings from `audit-arch`, `audit-tests`, `audit-cohesion`, or
`audit-feature-gates` against actual code, git history, and design intent using 9–10 parallel
subagents. Contested findings are separated into their own file. The validated report carries a
`validated: true` marker to signal downstream processing.

## When to Use

- User says "validate audit", "validate findings", "validate report", "check audit results"
- After running `audit-arch`, `audit-tests`, `audit-cohesion`, or `audit-feature-gates` to filter noise before acting

## Arguments

```
{audit_report_path}
```

- `audit_report_path` — absolute path to an audit report produced by `audit-arch`,
  `audit-tests`, `audit-cohesion`, or `audit-feature-gates`. If omitted, use the most
  recent file under `{{AUTOSKILLIT_TEMP}}/audit-arch/`, `{{AUTOSKILLIT_TEMP}}/audit-tests/`,
  `{{AUTOSKILLIT_TEMP}}/audit-cohesion/`, or `{{AUTOSKILLIT_TEMP}}/audit-feature-gates/`
  (most recent mtime wins across all four).
  If no files exist under any of these directories, print an error message and exit
  with a non-zero status.

## Critical Constraints

**NEVER:**
- Modify any source code files
- Create files outside `{{AUTOSKILLIT_TEMP}}/validate-audit/`
- Issue subagent Task calls sequentially — ALL must be in a single parallel message
- Write output files before synthesizing ALL subagent results
- Subagents must NOT create their own files — they return findings in response text only
- Do NOT include VALID BUT EXCEPTION WARRANTED findings in the validated report body — they belong in the validation summary only

**ALWAYS:**
- Use `model: "sonnet"` when spawning all subagents via the Task tool
- Issue all Task calls in a single message to maximize parallelism
- Write `validated: true` as the **first line** of the validated report file
- Respect interactive vs headless mode for the approval step (Step 6)

## Finding Verdicts

| Verdict | Meaning | Action |
|---------|---------|--------|
| **VALID** | Finding confirmed by code evidence | Include as-is in validated report |
| **VALID BUT EXCEPTION WARRANTED** | Real issue; documented constraint applies | Include with exception note |
| **CONTESTED** | Factually wrong or counterproductive | Exclude from report; write to contested file |

---

## Workflow

### Step 1 — Detect Audit Format and Parse Findings

Read the audit report file. Detect its source by examining the document title or preamble:

- **audit-arch**: Title contains "Architectural Audit" or findings reference "Principle P{N}"
- **audit-tests**: Title contains "Test Suite Audit" or findings reference issue categories
- **audit-cohesion**: Title contains "Cohesion Audit" or findings reference "Dimension C{N}"
- **audit-feature-gates**: Title contains "Feature Gate Audit" or findings reference
  BLOCK/WARN/INFO severity badges. Normalize severities: BLOCK→HIGH, WARN→MEDIUM, INFO→LOW.
  Use the normalized severity in the Validation Status table and severity adjustments.

  | feature-gates severity | Normalized severity |
  |----------------------|-------------------|
  | BLOCK | HIGH |
  | WARN | MEDIUM |
  | INFO | LOW |

  Verdict rules by finding type:
  - **BLOCK (D2, D3, D4)**: require code verification at the cited `file:line` — confirm the
    unguarded import, missing gate, or unguarded tool handler actually exists.
  - **WARN (D2–D6)**: check for intentional design exceptions documented in comments or ADRs.
  - **INFO (D1, D5 table rows)**: accepted as-is unless the table value contradicts the actual
    code (verify by reading the config/constants file directly).
  - **D1/D5 table rows**: always place in cross-cutting batch; validate by reading
    the config source file named in the table row.

If none of the four patterns match, print:
`"Error: unrecognized audit report format — expected title 'Architectural Audit', 'Test Suite Audit', 'Cohesion Audit', or 'Feature Gate Audit'. Aborting."`
and exit with a non-zero status.

For each finding, extract:
- **ID** — principle/category/dimension label (e.g., P3, Category 1, C5, D2) or a short slug
- **Text** — the full finding description
- **Severity** — CRITICAL / HIGH / MEDIUM / LOW (arch, tests), STRONG/ADEQUATE/WEAK/FRACTURED
  (cohesion), or BLOCK / WARN / INFO (feature-gates)
- **Location** — `file:line` references, if present
- **Category** — the principle, issue category, dimension label, or gate dimension

Collect all findings into a flat list. Record the source audit skill (`arch`, `tests`,
`cohesion`, or `feature_gates`) for use in output filenames.

### Step 2 — Group into Thematic Batches

Cluster findings by **code area**: inspect `file:line` references in each finding and group
by the top-level package touched (e.g., `pipeline/`, `execution/`, `server/`, `core/`,
`recipe/`, `cli/`, `workspace/`).

- Target **8–9 code-area batches** for code validation agents.
- Findings without file references: place in a "cross-cutting" batch.
- Fewer than 8 distinct areas: assign each area its own batch; use however many batches are available.
- More than 9 distinct areas: merge smallest clusters until ≤ 9 groups remain.
- The 10th slot is reserved for the history research agent (runs against ALL findings).

**Feature-gates table-format findings (D1 and D5):**
The Config Projection (D1) and Boundary Coupling (D5) dimensions produce inventory/coupling
tables rather than single-finding lines. For these dimensions:
- Treat each table row as an individual finding.
- Place all D1 and D5 findings in the "cross-cutting" batch (their inventory tables span multiple files rather than single code locations).
- Code validation agents for cross-cutting findings must verify D1/D5 rows by reading the
  referenced config source or constants file directly (e.g., `_type_constants.py` for
  FEATURE_REGISTRY entries, `config/defaults.yaml` for config projection values).

### Step 3 — Launch Parallel Subagents (SINGLE MESSAGE)

**Issue ALL Task calls in a single message.**

Spawn the following agents simultaneously using `model: "sonnet"`:

**Code Validation Agents (8–9 agents)**

Each agent receives its assigned finding batch and these instructions:

> You are validating audit findings against the actual codebase. For each finding in your
> batch:
> 1. Read the source code at the referenced `file:line` location using Glob/Grep/Read.
> 2. Check recent git history: `git log -10 --oneline -- {file}`.
> 3. Evaluate whether the finding accurately describes the code as it currently exists.
> 4. Assign a verdict: VALID, VALID BUT EXCEPTION WARRANTED, or CONTESTED.
> 5. If CONTESTED: provide specific code evidence that refutes the finding.
> 6. If VALID BUT EXCEPTION WARRANTED: describe the constraint that warrants an exception.
> 7. If severity should be adjusted, state the new severity and rationale.
> Do NOT modify any files. Return structured text only — no files created.

**History Research Agent (1 agent)**

Receives ALL findings. Instructions:

> You are researching historical context for audit findings. For each finding:
> 1. Search git log for commits touching the referenced files in the last 90 days.
> 2. Check for open or recently-closed GitHub issues or PRs related to the code area:
>    `gh issue list --state all --search "{keyword from finding}"`.
> 3. If a finding references a known in-progress fix or tracked issue, note it.
> Do NOT create any files. Return structured text only.

**Subagent output format — code validation agents:**

```
## Batch {N} Verdicts

### [{ID}] {short finding description}
- **Verdict**: VALID | VALID BUT EXCEPTION WARRANTED | CONTESTED
- **Code evidence**: {file:line + what the code actually shows}
- **Rationale**: {why this verdict}
- **Severity adjustment**: {new severity and reason} (omit if unchanged)
- **Exception note**: {constraint that warrants the exception} (EXCEPTION only)
```

**Subagent output format — history research agent:**

```
## Historical Context

### [{ID}] {short finding description}
- **Recent commits**: {commit hashes + summaries, or "none in last 90 days"}
- **Related issues/PRs**: {numbers and titles, or "none found"}
- **Context note**: {how history affects the verdict, or "no impact"}
```

### Step 4 — Synthesize Results

After all agents return:

1. For each finding, merge the code agent verdict with historical context.
2. If history reveals an in-progress fix (open PR or tracked issue) for a VALID finding,
   upgrade to VALID BUT EXCEPTION WARRANTED; use the PR/issue number as the exception note.
3. Tally: `N_valid`, `N_exception`, `N_contested`.
4. Collect all severity adjustments.

### Step 5 — Generate Output Files

Ensure `{{AUTOSKILLIT_TEMP}}/validate-audit/` exists (`mkdir -p`).

**File 1 — Validated report**
Path: `{{AUTOSKILLIT_TEMP}}/validate-audit/validated_report_{source}_{YYYY-MM-DD_HHMMSS}.md`

Structure:

```markdown
validated: true

# Validated Audit Report — {source} ({YYYY-MM-DD})

**Original report:** {audit_report_path}
**Findings processed:** {total} | **Valid:** {N_valid} | **Exception warranted:** {N_exception} | **Contested:** {N_contested}

---

## Validation Status

| Finding | Original Severity | Verdict | Adjusted Severity |
|---------|------------------|---------|------------------|
| ... | ... | ... | ... |

---

## Validated Findings

{Each **VALID** finding only — do NOT include VALID BUT EXCEPTION WARRANTED findings here.
Exception-warranted findings go exclusively in the validation summary file.
Format: original finding text, VALID verdict badge, severity adjustment note if applicable.}

---

*{N_contested} finding(s) contested and excluded — see contested_findings_{source}_{ts}.md*
```

**File 2 — Contested findings** (write only when `N_contested > 0`)
Path: `{{AUTOSKILLIT_TEMP}}/validate-audit/contested_findings_{source}_{YYYY-MM-DD_HHMMSS}.md`

Structure:

```markdown
# Contested Findings — {source} ({YYYY-MM-DD})

{For each CONTESTED finding:}

## [{ID}] {short description}

**Original severity:** {severity}
**Contest rationale:** {why it is factually wrong or counterproductive}
**Code evidence:** {specific file:line + what the code actually shows}
**Historical context:** {from history agent, if relevant; else omit}
```

### Step 5b — Write Validation Summary

Write the full audit trail to a separate file. This file is NOT part of the issue body —
it is posted as a comment after issue creation.

Path: `{{AUTOSKILLIT_TEMP}}/validate-audit/validation_summary_{source}_{YYYY-MM-DD_HHMMSS}.md`

Structure:

```markdown
# Validation Summary — {source} ({YYYY-MM-DD})

**Original report:** {audit_report_path}
**Total findings:** {total} | **Valid:** {N_valid} | **Exception warranted:** {N_exception} | **Contested:** {N_contested}

---

## Per-Finding Verdicts

| Finding ID | Verdict | Severity (adj.) | Reasoning summary |
|------------|---------|-----------------|-------------------|
| ... | ... | ... | ... |

---

## Exception-Warranted Findings

{For each VALID BUT EXCEPTION WARRANTED finding:}

### [{ID}] {short description}

**Original severity:** {severity}
**Exception note:** {constraint that warrants exception}
**Code evidence:** {file:line + what code shows}
**Historical context:** {from history agent, if relevant; else omit}

---

## Contested Findings (Removed)

{For each CONTESTED finding: full text, contest rationale, code evidence.}

---

## Severity Adjustments

{For each finding where severity was adjusted: original → adjusted, rationale.}
```

### Step 6 — Parallel Post-Validation (SINGLE MESSAGE, READ-ONLY)

After both the validated report and validation summary are written, launch **two read-only
subagents in a single message**. Neither subagent may use Write, Edit, or any file-creation
tool — they return findings as response text only.

**Subagent A — Cross-Validator**

Receives paths to three files:
1. Original audit report (`{audit_report_path}`)
2. Validated report (`validated_report_{source}_{ts}.md`)
3. Validation summary (`validation_summary_{source}_{ts}.md`)

Instructions:
> You are cross-validating three audit artifacts for consistency. Read all three files.
> Check:
> 1. **No accidental deletions** — every finding in the validated report traces to a finding in the original
> 2. **No accidental survivors** — every CONTESTED finding in the summary is absent from the validated report
> 3. **No exception-warranted leakage** — no VALID BUT EXCEPTION WARRANTED finding appears in the validated report's `## Validated Findings` section
> 4. **Structural integrity** — valid markdown, Summary Table counts match actual finding count, finding IDs sequential, no orphaned references
> 5. **Count reconciliation** — N_valid + N_exception + N_contested equals original total; consistent between summary and validated report
> Return a structured discrepancy report. If no issues found, return "CROSS-VALIDATION PASSED".
> Do NOT create any files. Return structured text only.

Output format:
```
## Cross-Validation Report

Status: PASSED | DISCREPANCIES FOUND

### Discrepancy [{N}]: {type}
- **Finding ID**: {id}
- **Issue**: {what is wrong}
- **Expected**: {what should be there}
- **Actual**: {what is there}
```

**Subagent B — Ticket Grouper**

Receives the validated report path.

Instructions:
> You are analyzing validated audit findings to propose ticket groupings. Read the validated report.
> For each finding, assess scope: lines of code affected, complexity, criticality, file overlap.
> Grouping rules:
> - **Standalone ticket**: finding is large in scope (many files/lines), complex refactor, or touches a critical path
> - **Grouped ticket**: finding is small, low-risk, non-conflicting. Group same-category small findings together.
> - **Conflict awareness**: findings touching the same file(s) must be in the same ticket or explicitly sequenced
> - No rigid severity-to-grouping rule: a HIGH can be grouped if small; a LOW can be standalone if complex
>
> Return a grouping manifest listing each proposed ticket with:
> - Ticket title (descriptive, scoped)
> - Finding IDs included (e.g., P1-F09, P1-F11, P3-F18)
> - Rationale for grouping or standalone
> - Estimated scope: small / medium / large
> - File overlap notes (which findings touch the same files)
> Do NOT create any files. Return structured text only.

Output format:
```
## Grouping Manifest

### Ticket Group 1: {title}
- **Finding IDs**: {id1}, {id2}, ...
- **Rationale**: {why grouped or standalone}
- **Scope**: small | medium | large
- **File overlap**: {files touched by multiple findings in this group, or "none"}

### Ticket Group 2: {title}
...
```

### Step 7 — Apply Cross-Validation Corrections

After both parallel subagents return:

**From Cross-Validator:**
- If status is `CROSS-VALIDATION PASSED`: proceed directly to Step 8.
- If discrepancies found: for each discrepancy, re-read the relevant section of the validated
  report and validation summary, write the corrected content to a `.tmp` file first, then
  atomically move it over the original (to prevent partial-write corruption), and note the
  correction applied. Limit to at most 3 correction passes; after 3 passes, record any
  remaining discrepancies and continue to Step 8.
- Corrections are writes to existing output files only — no new findings are introduced.

**From Ticket Grouper:**
- Record the grouping manifest (it will be written to disk in Step 8).
- If the grouper returned fewer than 1 group: treat the entire validated report as a single ticket.

### Step 8 — Split Validated Report by Grouping Manifest

Before writing any ticket body files, verify `$AUTOSKILLIT_TEMP` is non-empty
(`test -n "${AUTOSKILLIT_TEMP}"`); abort with an error message if unset to prevent
path collapse to filesystem root.

For each ticket group in the grouping manifest:

1. Extract the subset of findings assigned to this group from the validated report.
2. Build a per-ticket body file with:
   - The `validated: true` sentinel on line 1
   - An H1 heading: `# {ticket title}` (from grouping manifest)
   - A subset Summary Table (only the rows for included finding IDs)
   - Only the `## Validated Findings` sub-sections for included finding IDs
   - A footer: `*Part of validated {source} audit — see full report for remaining tickets.*`
3. Write to: `{{AUTOSKILLIT_TEMP}}/validate-audit/ticket_body_{source}_{N}_{YYYY-MM-DD_HHMMSS}.md`
   where `{N}` is 1-indexed from the grouping manifest.

Also write the grouping manifest itself to:
`{{AUTOSKILLIT_TEMP}}/validate-audit/grouping_manifest_{source}_{YYYY-MM-DD_HHMMSS}.md`

The grouping manifest file is the structured text returned by the ticket grouper subagent,
prefixed with:
```markdown
# Ticket Grouping Manifest — {source} ({YYYY-MM-DD})

**Validated report:** {validated_report_path}
**Total groups:** {N}

---
```

### Step 9 — Interactive vs Headless Approval

Detect headless mode: run `echo "${AUTOSKILLIT_HEADLESS:-0}"` via Bash. Output `1` means
headless.

**Headless mode:** Write all output files immediately without prompting. Print to terminal:

```
[validate-audit] Done.
  Valid: {N_valid} | Exceptions: {N_exception} | Contested: {N_contested}
  Summary:   {validation_summary_path}
  Manifest:  {grouping_manifest_path}
  Tickets:   {ticket_body_1_path}
             {ticket_body_2_path}  (one line per ticket group)
  Contested: {contested_findings_path}  (omit if N_contested == 0)
validated_report_path = {validated_report_path}
verdict = validated
```

**Interactive mode:** Display the validation status table (verdict counts), then ask:

> Write validated report and contested findings files? [Y/n]

On Y or empty input, write all files. After writing, offer:

> Run `/autoskillit:prepare-issue` for each ticket group? [Y/n]

On Y, call `prepare-issue` for each ticket body file (in parallel). After issue creation,
append the validation summary to each created issue body using `gh issue edit --body-file`:
fetch the current issue body, verify the fetched body is non-empty (abort the append for
that issue if empty to avoid overwriting with summary-only content), append a horizontal
rule and the validation summary content, write the combined text to a temp file, then run
`gh issue edit {issue_number} --body-file` with that temp file. Do NOT use `gh issue comment`.

---

## Output Location

All output files are written relative to the current working directory under `{{AUTOSKILLIT_TEMP}}/validate-audit/`:

```
{{AUTOSKILLIT_TEMP}}/validate-audit/
├── validated_report_{source}_{YYYY-MM-DD_HHMMSS}.md      (always written; VALID findings only)
├── contested_findings_{source}_{YYYY-MM-DD_HHMMSS}.md    (when N_contested > 0)
├── validation_summary_{source}_{YYYY-MM-DD_HHMMSS}.md    (always written; audit trail)
├── grouping_manifest_{source}_{YYYY-MM-DD_HHMMSS}.md     (always written; ticket grouping)
└── ticket_body_{source}_{N}_{YYYY-MM-DD_HHMMSS}.md       (one per ticket group, N ≥ 1)
```

`{source}` is `arch`, `tests`, `cohesion`, or `feature_gates` based on the input report.

## Related Skills

- `/autoskillit:audit-arch` — produces reports this skill validates
- `/autoskillit:audit-tests` — produces reports this skill validates
- `/autoskillit:audit-cohesion` — produces reports this skill validates
- `/autoskillit:audit-feature-gates` — produces reports this skill validates
- `/autoskillit:prepare-issue` — offered interactively for contested findings
