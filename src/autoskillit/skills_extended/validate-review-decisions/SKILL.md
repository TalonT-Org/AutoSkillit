---
name: validate-review-decisions
categories: [audit]
uses_capabilities: [agent_model, cross_skill_ref]
description: >-
  Validate review-decisions audit findings with mandatory intent analysis
  and seven evidence-gathering rules. Adds docstring-as-contract recognition,
  deliberate-change detection, test-as-intent-signal, consumer-impact
  verification, architectural feasibility checks, behavioral simulation,
  and symmetry-as-design recognition to the standard validation workflow.
  Use when validating reports from audit-review-decisions specifically.
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo '[SKILL: validate-review-decisions] Validating review-decisions audit findings with intent analysis...'"
          once: true
---

# Validate Review-Decisions Audit Findings Skill

Validate review-decisions audit findings with mandatory intent analysis and seven
evidence-gathering rules. Removes contested findings, documents exceptions, adjusts
severities. The validated report carries a `validated: true` marker to signal downstream processing.

## When to Use

- After running `audit-review-decisions` to filter noise before acting
- When user says "validate review decisions", "validate review-decisions", or "check review-decisions audit"

## Arguments

```
{audit_report_path}
```

- `audit_report_path` — absolute path to an audit report produced by `audit-review-decisions`.
  If omitted, use the most recent file under `{{AUTOSKILLIT_TEMP}}/audit-review-decisions/`
  (most recent mtime wins).
  If no files exist, print an error message and exit with a non-zero status.

- `AUTOSKILLIT_AUDIT_RUN_DIR` — optional environment variable. When set, all output
  files are written directly under `$AUTOSKILLIT_AUDIT_RUN_DIR/` instead of
  `{{AUTOSKILLIT_TEMP}}/validate-audit-{YYYY-MM-DD_HHMMSS}/`. The recipe sets this to
  the per-run directory created by `init_audit_run` to prevent cross-run file accumulation.

## Critical Constraints

**NEVER:**
- Fabricate, invent, or embellish information not supported by the available evidence or code.

- Modify any source code files
- Create files outside `$AUDIT_BASE_DIR/` (the per-run directory set in Step 5)
- Issue subagent Task calls sequentially — ALL must be in a single parallel message
- Write output files before synthesizing ALL subagent results
- Subagents must NOT create their own files — they return findings in response text only
- Do NOT include VALID BUT EXCEPTION WARRANTED findings in the validated report body — they belong in the validation summary only
- Run subagents in the background (`run_in_background: true` is prohibited)

**ALWAYS:**
- Spawn all subagents via `Agent(model="sonnet")`
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

Verify `$AUTOSKILLIT_TEMP` is set. If empty or unset, print an error and exit non-zero.

### Step 1 — Detect Audit Format and Parse Findings

Read the audit report file. Confirm it is a review-decisions audit by checking for
`"Review Decisions Audit"` or `"PR Review Decisions Audit"` in the title. If neither
is present, print:
`"Error: unrecognized audit report format — expected title 'Review Decisions Audit' or 'PR Review Decisions Audit'. Use /autoskillit:validate-audit for other audit types. Aborting."`
and exit non-zero.

Set `source = review_decisions`.

For each finding, extract:
- **ID** — the finding identifier
- **Text** — the full finding description
- **Severity** — CRITICAL / HIGH / MEDIUM / LOW
- **Location** — `file:line` references, if present
- **Category** — the finding category

For `review_decisions` findings, also extract PR provenance metadata:
- **PR Number** — the source PR number (e.g., #1234)
- **PR Link** — full URL to the PR review thread
- **Reviewer Quote** — verbatim text of the original reviewer comment
- **Deferral Signal** — the keyword phrase or marker type that triggered the match
  (e.g., "REVIEW-FLAG", "KEYWORD: out of scope for this fix cycle")
- **REVIEW-FLAG metadata** — severity and dimension from the structured tag, when present

These additional fields must be preserved through validation and into ticket body files
so that the PR provenance is traceable in the resulting GitHub issues.

Collect all findings into a flat list.

### Step 2 — Group into Thematic Batches

Cluster findings by **code area**: inspect `file:line` references in each finding and group
by the top-level package touched (e.g., `pipeline/`, `execution/`, `server/`, `core/`,
`recipe/`, `cli/`, `workspace/`).

- Target **8–9 code-area batches** for code validation agents.
- Findings without file references: place in a "cross-cutting" batch.
- Fewer than 8 distinct areas: assign each area its own batch; use however many batches are available.
- More than 9 distinct areas: merge smallest clusters until ≤ 9 groups remain.
- The 10th slot is reserved for the history research agent (runs against ALL findings).

### Step 3 — Launch Parallel Subagents (SINGLE MESSAGE)

**Issue ALL Task calls in a single message.**

Spawn the following agents simultaneously using `model: "sonnet"`:

**Code Validation Agents (8–9 agents)**

Each agent receives its assigned finding batch plus mandatory intent analysis instructions
and the seven evidence-gathering rules (generalized — no specific finding IDs):

> You are validating audit findings against the actual codebase. For each finding in your
> batch, you MUST perform the mandatory intent analysis below BEFORE assigning a verdict.

**Mandatory Intent Analysis — perform ALL of the following before assigning a verdict:**

> 1. **Docstring and inline documentation**: Read the function/class docstring. If the behavior the finding targets is explicitly documented as intentional, the finding is CONTESTED unless the documentation itself is wrong.
>
> 2. **Git provenance**: Run `git log --follow -5 --oneline -- {file}` to identify the introducing commit. Read the commit message. If the commit message explicitly describes the behavior as a deliberate change, the finding is CONTESTED.
>
> 3. **Test coverage**: Search for tests that assert the behavior the finding targets. If a dedicated test exists that verifies the "buggy" behavior, the finding is CONTESTED — the behavior is tested, not accidental.
>
> 4. **Contract analysis**: For serialization/schema findings, trace all consumers. If all consumers handle both the "before" and "after" states identically, the finding's claimed impact is overstated.
>
> 5. **Architectural constraint check**: For "move code to layer X" findings, verify the move is actually possible under the IL import contracts in `pyproject.toml`. If the suggested destination layer cannot import the required dependencies, the finding is CONTESTED.
>
> 6. **Simulation**: For behavioral claims, simulate the behavior. Trace through the code with concrete inputs. If the claimed behavior does not manifest, the finding is CONTESTED.

**Evidence-Gathering Rules:**

> **Rule 1 — Docstring-as-contract recognition:** When a finding claims behavior X is a bug, check the containing function's docstring first. If the docstring explicitly describes behavior X as the intended design, the finding is CONTESTED.
>
> **Rule 2 — Deliberate-change detection:** When a finding claims an unconditional operation should be conditional (or vice versa), check git history for the change that made it unconditional. If the commit message explicitly describes the change as intentional, the finding is CONTESTED.
>
> **Rule 3 — Test-as-intent-signal:** A dedicated test asserting the "buggy" behavior is strong evidence of intent. Search for test names containing the finding's subject. If found, the behavior is tested and intentional — classify as CONTESTED.
>
> **Rule 4 — Consumer-impact verification:** Before accepting a serialization/schema finding, trace ALL consumers of the affected output. If every consumer handles the current behavior correctly, the finding's claimed impact is unsubstantiated. Downgrade or CONTEST.
>
> **Rule 5 — Architectural feasibility check:** For "move X to layer Y" findings, verify the move is possible under IL import constraints. If the target layer cannot access required dependencies, the finding's prescription is infeasible — CONTEST.
>
> **Rule 6 — Behavioral simulation:** For findings claiming specific runtime behavior, trace through the code with concrete inputs. If the claimed behavior does not reproduce, the finding is CONTESTED.
>
> **Rule 7 — Symmetry-as-design recognition:** When a finding claims inconsistency between how two similar fields are handled, investigate whether the asymmetry is intentional. Different sentinel values encode different semantics. Fields may be deliberately treated differently based on their domain meaning.

After completing intent analysis for all findings in your batch, assign verdicts.

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
- **Intent analysis**: {summary of which techniques were applied and what they found}
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

Set the output base directory. When `AUTOSKILLIT_AUDIT_RUN_DIR` is set (by the
recipe's `init_audit_run` step), files are written to the per-run directory to
prevent cross-run accumulation:

```bash
if [ -n "$AUTOSKILLIT_AUDIT_RUN_DIR" ]; then
    AUDIT_BASE_DIR="$AUTOSKILLIT_AUDIT_RUN_DIR"
else
    AUDIT_BASE_DIR="{{AUTOSKILLIT_TEMP}}/validate-audit-$(date -u +%Y-%m-%d_%H%M%S)"
fi
mkdir -p "$AUDIT_BASE_DIR"
```

**File 1 — Validated report**
Path: `$AUDIT_BASE_DIR/validated_report_review_decisions.md`

Structure:

```markdown
validated: true

# Validated Audit Report — review_decisions ({YYYY-MM-DD})

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

*{N_contested} finding(s) contested and excluded — see contested_findings_review_decisions.md*
```

**File 2 — Contested findings** (write only when `N_contested > 0`)
Path: `$AUDIT_BASE_DIR/contested_findings_review_decisions.md`

Structure:

```markdown
# Contested Findings — review_decisions ({YYYY-MM-DD})

{For each CONTESTED finding:}

## [{ID}] {short description}

**Original severity:** {severity}
**Contest rationale:** {why it is factually wrong or counterproductive}
**Code evidence:** {specific file:line + what the code actually shows}
**Historical context:** {from history agent, if relevant; else omit}
```

### Step 5b — Write Validation Summary

Write the full audit trail to a separate file. This file is NOT part of the issue body —
it is a pipeline audit artifact stored in the run directory for traceability. The
file-audit-issues skill must not append this content to issue bodies.

Path: `$AUDIT_BASE_DIR/validation_summary_review_decisions.md`

Structure:

```markdown
# Validation Summary — review_decisions ({YYYY-MM-DD})

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
2. Validated report (`validated_report_review_decisions.md`)
3. Validation summary (`validation_summary_review_decisions.md`)

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
> - **Minimum group floor**: when the number of VALID findings exceeds 4, you must produce at least 2 ticket groups. When findings exceed 8, produce at least 3 groups. Never collapse all findings into a single group when grouping by scope, file overlap, or category would produce a natural partition.
> - **Effort-based splitting for multi-file findings**: When a single finding enumerates multiple files for modification (splitting, refactoring, restructuring), do not treat the finding as one atomic unit. Estimate implementation effort per file using line count as a proxy:
>   - **High effort** (above ~900 lines): Assign to a standalone ticket, or pair with at most one other file.
>   - **Medium effort** (~750–900 lines): Group in small batches, preferring files in the same directory or package area.
>   - **Cross-package separation**: Files in different top-level packages should generally land in separate tickets.
>   - These thresholds are guidelines for judgment — assess the specific files rather than applying rigid per-file caps.
>
> Return a grouping manifest listing each proposed ticket with:
> - Ticket title (descriptive, scoped)
> - Finding IDs included (e.g., F1, F2, F3)
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
   - PR provenance metadata (PR Number, PR Link, Reviewer Quote) preserved for each finding
   - A footer: `*Part of validated review_decisions audit — see full report for remaining tickets.*`
3. Write to: `$AUDIT_BASE_DIR/ticket_body_review_decisions_{N}.md`
   where `{N}` is 1-indexed from the grouping manifest.

Also write the grouping manifest itself to:
`$AUDIT_BASE_DIR/grouping_manifest_review_decisions.md`

The grouping manifest file is the structured text returned by the ticket grouper subagent,
prefixed with:
```markdown
# Ticket Grouping Manifest — review_decisions ({YYYY-MM-DD})

**Validated report:** {validated_report_path}
**Total groups:** {N}

---
```

### Step 9 — Interactive vs Headless Approval

Detect headless mode: run `echo "${AUTOSKILLIT_HEADLESS:-0}"` via Bash. Output `1` means
headless.

**Headless mode:** Write all output files immediately without prompting. Print to terminal:

```
[validate-review-decisions] Done.
  Valid: {N_valid} | Exceptions: {N_exception} | Contested: {N_contested}
  Summary:   {validation_summary_path}
  Manifest:  {grouping_manifest_path}
  Tickets:   {ticket_body_1_path}
             {ticket_body_2_path}  (one line per ticket group)
  Contested: {contested_findings_path}  (omit if N_contested == 0)
  Report:    {validated_report_path}
validated_report_path = {validated_report_path}
verdict = validated
```

**Interactive mode:** Display the validation status table (verdict counts), then ask:

> Write validated report and contested findings files? [Y/n]

On Y or empty input, write all files and print the same terminal summary as headless mode
(excluding the structured output tokens). Issue filing is handled separately by the
`file-audit-issues` skill — direct the user there if they want to create GitHub issues
from the ticket body files.

---

## Output Location

All output files are written under `$AUDIT_BASE_DIR/` where `AUDIT_BASE_DIR` is determined as follows:
- If `AUTOSKILLIT_AUDIT_RUN_DIR` is set: `$AUDIT_BASE_DIR = $AUTOSKILLIT_AUDIT_RUN_DIR`
- Otherwise: `$AUDIT_BASE_DIR = {{AUTOSKILLIT_TEMP}}/validate-audit-{YYYY-MM-DD_HHMMSS}/`:

```
$AUDIT_BASE_DIR/
├── validated_report_review_decisions.md   (always written; VALID findings only)
├── contested_findings_review_decisions.md (when N_contested > 0)
├── validation_summary_review_decisions.md (always written; audit trail)
├── grouping_manifest_review_decisions.md  (always written; ticket grouping)
└── ticket_body_review_decisions_{N}.md    (one per ticket group, N >= 1)
```

## Related Skills

- `/autoskillit:audit-review-decisions` — produces reports this skill validates
- `/autoskillit:validate-audit` — generic audit validator for other audit types
- `/autoskillit:validate-test-audit` — specialized validator for test audit reports
- `/autoskillit:file-audit-issues` — batch-creates issues from ticket body files (pipeline-only, run via full-audit recipe)
