---
name: validate-test-audit
categories: [audit]
description: >-
  Validate test audit findings with test-domain semantic rules and intent
  analysis. Adds import-path-as-contract recognition, precondition-as-assertion
  detection, provenance verification, split-era lifecycle awareness, and
  deletion-vs-improvement distinction to the standard validation workflow.
  Use when validating reports from audit-tests specifically.
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo '[SKILL: validate-test-audit] Validating test audit findings with domain-specific rules...'"
          once: true
---

# Validate Test Audit Findings Skill

Validate findings from `audit-tests` (Test Suite Audit reports) against actual code, git history,
and test intent using 9–10 parallel subagents. Adds five test-domain semantic rules and a
mandatory intent analysis step to the standard validation workflow. Contested findings are
separated into their own file. The validated report carries a `validated: true` marker to
signal downstream processing.

## When to Use

- After running `/autoskillit:audit-tests` to validate findings before acting
- When user says "validate test audit", "validate audit-tests", "validate test findings"

## Arguments

```
{audit_report_path}
```

- `audit_report_path` — absolute path to an audit-tests report. If omitted, use the most
  recent file under `{{AUTOSKILLIT_TEMP}}/audit-tests/`. If no files exist there, print an
  error message and exit with a non-zero status.

## Critical Constraints

**NEVER:**
- Modify any source code files
- Create files outside `{{AUTOSKILLIT_TEMP}}/validate-audit/`
- Issue subagent Task calls sequentially — ALL must be in a single parallel message
- Write output files before synthesizing ALL subagent results
- Subagents must NOT create their own files — they return findings in response text only
- Do NOT include VALID BUT EXCEPTION WARRANTED findings in the validated report body — they belong in the validation summary only
- Run subagents in the background (`run_in_background: true` is prohibited)

**ALWAYS:**
- Use `model: "sonnet"` when spawning all subagents via the Task tool
- Issue all Task calls in a single message to maximize parallelism
- Write `validated: true` as the **first line** of the validated report file
- Respect interactive vs headless mode for the approval step (Step 9)
- Emit: `validated_report_path = <absolute path to the validated report file>`

## Finding Verdicts

| Verdict | Meaning | Action |
|---------|---------|--------|
| **VALID** | Finding confirmed by code evidence | Include as-is in validated report |
| **VALID BUT EXCEPTION WARRANTED** | Real issue; documented constraint applies | Include with exception note |
| **CONTESTED** | Factually wrong or counterproductive | Exclude from report; write to contested file |

---

## Workflow

### Step 0 — Validate Environment

Before any path resolution, verify `$AUTOSKILLIT_TEMP` is set:

```bash
test -n "${AUTOSKILLIT_TEMP}" || { echo "Error: \$AUTOSKILLIT_TEMP is unset. Cannot resolve default paths. Aborting."; exit 1; }
```

This guard must fire before auto-discovering `audit_report_path` (which resolves a path
under `$AUTOSKILLIT_TEMP/audit-tests/`) and before any output path construction.

### Step 1 — Detect Audit Format

Read the audit report file. Confirm it is a test audit by examining the document title:
- **audit-tests**: Title contains "Test Suite Audit" or findings reference issue categories
  (C1–C9, C11).

If the title does not contain "Test Suite Audit" and no findings reference issue categories,
print:
`"Error: this skill only validates test audit reports produced by /autoskillit:audit-tests. For other audit types, use /autoskillit:validate-audit. Aborting."`
and exit with a non-zero status.

Parse all findings. For each, extract:
- **ID** — category label (e.g., C1, C2) or short slug
- **Text** — the full finding description
- **Severity** — HIGH / MEDIUM / LOW
- **Location** — `file:line` references, if present
- **Category** — the issue category (e.g., "Useless Tests", "Redundant Tests")

Set `source = tests` for use in output filenames.

### Step 2 — Group into Thematic Batches

Cluster findings by **code area**: inspect `file:line` references in each finding and group
by the top-level package touched (e.g., `tests/core/`, `tests/pipeline/`, `tests/execution/`).

- Target **8–9 code-area batches** for code validation agents.
- Findings without file references: place in a "cross-cutting" batch.
- Fewer than 8 distinct areas: assign each area its own batch.
- More than 9 distinct areas: merge smallest clusters until ≤ 9 groups remain.
- The 10th slot is reserved for the history research agent (runs against ALL findings).

### Step 3 — Launch Parallel Subagents (SINGLE MESSAGE)

**Issue ALL Task calls in a single message.**

Spawn the following agents simultaneously using `model: "sonnet"`:

**Code Validation Agents (8–9 agents)**

Each agent receives its assigned finding batch and these instructions:

> You are validating audit findings from audit-tests against the actual codebase.
> For each finding in your batch:
> 1. Read the source code at the referenced `file:line` location using Glob/Grep/Read.
> 2. Check recent git history: `git log -10 --oneline -- {file}`.
> 3. Before assigning a verdict, apply the five test-domain semantic rules below.
> 4. For findings recommending test deletion or modification, also conduct the intent
>    analysis step below before finalizing your verdict.
> 5. Assign a verdict: VALID, VALID BUT EXCEPTION WARRANTED, or CONTESTED.
> 6. If CONTESTED: provide specific code evidence that refutes the finding.
> 7. If VALID BUT EXCEPTION WARRANTED: describe the constraint that warrants an exception.
> 8. If severity should be adjusted, state the new severity and rationale.
> Do NOT modify any files. Return structured text only — no files created.

**Semantic Rules for Test Audit Validation**

Apply these five principles when evaluating each finding:

**Rule 1 — Import-Path-as-Contract:**
When a finding cites an import statement as evidence of a problem, treat the import path
as a structural contract. The existence guard at the import site is not accidental noise —
it documents a dependency boundary. Removing or weakening the guard breaks a contract that
callers may rely on. Verify the import is used (at least one `__module__` or `__name__`
reference) before flagging it as useless. If the import is genuinely unused, verify it is
not re-exported before recommending removal — a module may import for side-effect-free
re-export.

**Rule 2 — Precondition-as-Assertion:**
When a finding flags a test that validates a precondition (e.g., null check, schema
validation, argument type guard), treat it as an assertion, not dead code. Precondition
checks document the calling contract and guard against downstream failures in code paths
that bypass the happy path. Verify the precondition applies to all callers before
recommending removal. If callers exist that pass invalid values, the precondition is a
necessary guard — VALID BUT EXCEPTION WARRANTED rather than VALID.

**Rule 3 — Provenance Verification vs. Existence:**
A test that looks unused may be a coverage artifact created to hit a branch or exception
path. Check `__module__` and git blame: `git log --follow -5 --oneline -- {test_file}`.
If the test was introduced in the same commit that introduced the code under test (co-creation),
it is structurally significant and warrants VALID BUT EXCEPTION WARRANTED rather than
removal — prefer improvement over deletion. If the test was introduced in isolation as a
coverage-for-its-own-sake artifact with no consumer, deletion is appropriate.

**Rule 4 — Split-Era Lifecycle:**
Some tests were written for code that has since been restructured. A test that was once
legitimate may now appear redundant due to a split-era change (e.g., a module was split
into submodules and the old import was replaced with a direct import). In this case,
evaluate the structural contract: is the old import path a published interface, or an
internal implementation detail that changed? If it is a published interface, the existence
guard serves a purpose. If it is purely internal, consolidation is appropriate. Look for
a corresponding existence test in the receiving module before recommending deletion.

**Rule 5 — Deletion vs. Improvement:**
Distinguish between tests that should be deleted and tests that should be improved.
Findings recommending deletion should be evaluated against three criteria:
- The test exercises a contract with no consumer: DELETE (VALID)
- The test exercises a contract with unknown consumers: VALID BUT EXCEPTION WARRANTED with improvement recommendation (strengthen, narrow, or document the contract)
- The test duplicates coverage provided by another test: VALID BUT EXCEPTION WARRANTED with consolidation recommendation (merge or de-duplicate)

**Intent Analysis Step (mandatory for deletion/modification recommendations):**

Before assigning a final verdict on any finding that recommends test deletion or modification:

1. **Git provenance**: Run `git log --follow -5 --oneline -- {test_file}` to find the introducing
   commit and its message. The introducing commit's message reveals whether the test was created
   as a coverage-for-its-own-sake artifact or as a response to a specific bug/failure.
2. **Co-creation context**: Check if the test was created in the same commit as the code it tests.
   Co-creation indicates the test documents a structural contract, not a coverage artifact.
3. **Naming signals**: Evaluate the test file name and function name for intent keywords
   (e.g., `test_split_`, `test_structure_`, `test_smoke_`, `test_existence_`, `test_importable_`,
   `test_exports_`). These names indicate the test was written to verify a structural property,
   not ad-hoc behavior — deletion may break a documented contract.
4. **Redundancy check**: Verify whether the test's assertion is covered by another test in the
   same or an adjacent test file. If full coverage exists elsewhere, deletion is appropriate
   (VALID). If partial coverage exists, prefer consolidation (VALID BUT EXCEPTION WARRANTED).

5. **Verdict**: If intent analysis reveals a legitimate structural purpose, classify as
   VALID BUT EXCEPTION WARRANTED with an improvement recommendation rather than deletion.

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
Path: `{{AUTOSKILLIT_TEMP}}/validate-audit/validated_report_tests_{YYYY-MM-DD_HHMMSS}.md`

Structure:

```markdown
validated: true

# Validated Audit Report — tests ({YYYY-MM-DD})

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

*{N_contested} finding(s) contested and excluded — see contested_findings_tests_{ts}.md*
```

**File 2 — Contested findings** (write only when `N_contested > 0`)
Path: `{{AUTOSKILLIT_TEMP}}/validate-audit/contested_findings_tests_{YYYY-MM-DD_HHMMSS}.md`

Structure:

```markdown
# Contested Findings — tests ({YYYY-MM-DD})

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

Path: `{{AUTOSKILLIT_TEMP}}/validate-audit/validation_summary_tests_{YYYY-MM-DD_HHMMSS}.md`

Structure:

```markdown
# Validation Summary — tests ({YYYY-MM-DD})

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
2. Validated report (`validated_report_tests_{ts}.md`)
3. Validation summary (`validation_summary_tests_{ts}.md`)

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
> - Finding IDs included (e.g., CAT-1, CAT-2, CAT-3)
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
   - A footer: `*Part of validated tests audit — see full report for remaining tickets.*`
3. Write to: `{{AUTOSKILLIT_TEMP}}/validate-audit/ticket_body_tests_{N}_{YYYY-MM-DD_HHMMSS}.md`
   where `{N}` is 1-indexed from the grouping manifest.

Also write the grouping manifest itself to:
`{{AUTOSKILLIT_TEMP}}/validate-audit/grouping_manifest_tests_{YYYY-MM-DD_HHMMSS}.md`

The grouping manifest file is the structured text returned by the ticket grouper subagent,
prefixed with:
```markdown
# Ticket Grouping Manifest — tests ({YYYY-MM-DD})

**Validated report:** {validated_report_path}
**Total groups:** {N}

---
```

### Step 9 — Interactive vs Headless Approval

Detect headless mode: run `echo "${AUTOSKILLIT_HEADLESS:-0}"` via Bash. Output `1` means
headless.

**Headless mode:** Write all output files immediately without prompting. Print to terminal:

```
[validate-test-audit] Done.
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
├── validated_report_tests_{YYYY-MM-DD_HHMMSS}.md      (always written; VALID findings only)
├── contested_findings_tests_{YYYY-MM-DD_HHMMSS}.md       (when N_contested > 0)
├── validation_summary_tests_{YYYY-MM-DD_HHMMSS}.md       (always written; audit trail)
├── grouping_manifest_tests_{YYYY-MM-DD_HHMMSS}.md        (always written; ticket grouping)
└── ticket_body_tests_{N}_{YYYY-MM-DD_HHMMSS}.md          (one per ticket group, N ≥ 1)
```

## Related Skills

- `/autoskillit:audit-tests` — produces reports this skill validates
- `/autoskillit:validate-audit` — validates all other audit types (arch, cohesion, feature_gates, docs, review_decisions)
- `/autoskillit:prepare-issue` — offered interactively for contested findings