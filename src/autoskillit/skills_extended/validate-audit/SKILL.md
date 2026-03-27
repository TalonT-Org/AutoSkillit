---
name: validate-audit
categories: [audit]
description: Validate audit findings from audit-arch, audit-tests, or audit-cohesion against actual code, git history, and design intent using 9–10 parallel subagents. Removes contested findings, documents exceptions, adjusts severities. Use when user says "validate audit", "validate findings", "validate report", or "check audit results".
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo '[SKILL: validate-audit] Validating audit findings against code...'"
          once: true
---

# Validate Audit Findings Skill

Validate audit findings from `audit-arch`, `audit-tests`, or `audit-cohesion` against actual
code, git history, and design intent using 9–10 parallel subagents. Contested findings are
separated into their own file. The validated report carries a `validated: true` marker to
signal downstream processing.

## When to Use

- User says "validate audit", "validate findings", "validate report", "check audit results"
- After running `audit-arch`, `audit-tests`, or `audit-cohesion` to filter noise before acting

## Arguments

```
{audit_report_path}
```

- `audit_report_path` — absolute path to an audit report produced by `audit-arch`,
  `audit-tests`, or `audit-cohesion`. If omitted, use the most recent file under
  `.autoskillit/temp/audit-arch/`, `.autoskillit/temp/audit-tests/`, or
  `.autoskillit/temp/audit-cohesion/` (most recent mtime wins across all three).
  If no files exist under any of these directories, print an error message and exit
  with a non-zero status.

## Critical Constraints

**NEVER:**
- Modify any source code files
- Create files outside `.autoskillit/temp/validate-audit/`
- Issue subagent Task calls sequentially — ALL must be in a single parallel message
- Write output files before synthesizing ALL subagent results
- Subagents must NOT create their own files — they return findings in response text only

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

### Step 0 — Code-Index Initialization

Call `set_project_path` with the repo root:

```
mcp__code-index__set_project_path(path="{PROJECT_ROOT}")
```

Use project-relative paths in all code-index queries (e.g., `src/autoskillit/pipeline/`).
Fall back to native Grep/Glob if the code-index server is unavailable.

### Step 1 — Detect Audit Format and Parse Findings

Read the audit report file. Detect its source by examining the document title or preamble:

- **audit-arch**: Title contains "Architectural Audit" or findings reference "Principle P{N}"
- **audit-tests**: Title contains "Test Suite Audit" or findings reference issue categories
- **audit-cohesion**: Title contains "Cohesion Audit" or findings reference "Dimension C{N}"

If none of the three patterns match, print:
`"Error: unrecognized audit report format — expected title 'Architectural Audit', 'Test Suite Audit', or 'Cohesion Audit'. Aborting."`
and exit with a non-zero status.

For each finding, extract:
- **ID** — principle/category/dimension label (e.g., P3, Category 1, C5) or a short slug
- **Text** — the full finding description
- **Severity** — CRITICAL / HIGH / MEDIUM / LOW (arch, tests) or
  STRONG/ADEQUATE/WEAK/FRACTURED (cohesion)
- **Location** — `file:line` references, if present
- **Category** — the principle, issue category, or dimension label

Collect all findings into a flat list. Record the source audit skill (`arch`, `tests`, or
`cohesion`) for use in output filenames.

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

**Issue ALL Task calls in a single message.** Do not output any prose between tool calls.

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

Ensure `.autoskillit/temp/validate-audit/` exists (`mkdir -p`).

**File 1 — Validated report**
Path: `.autoskillit/temp/validate-audit/validated_report_{source}_{YYYY-MM-DD_HHMMSS}.md`

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

{Each VALID finding: original text, verdict badge, severity adjustment if any.}

## Findings with Exceptions

{Each VALID BUT EXCEPTION WARRANTED finding: original text + exception note.}

---

*{N_contested} finding(s) contested and excluded — see contested_findings_{source}_{ts}.md*
```

**File 2 — Contested findings** (write only when `N_contested > 0`)
Path: `.autoskillit/temp/validate-audit/contested_findings_{source}_{YYYY-MM-DD_HHMMSS}.md`

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

### Step 6 — Interactive vs Headless Approval

Detect headless mode: run `echo "${AUTOSKILLIT_HEADLESS:-0}"` via Bash. Output `1` means
headless.

**Headless mode:** Write both output files immediately without prompting. Print to terminal:

```
[validate-audit] Done.
  Valid: {N_valid} | Exceptions: {N_exception} | Contested: {N_contested}
  Report:    .autoskillit/temp/validate-audit/validated_report_{source}_{ts}.md
  Contested: .autoskillit/temp/validate-audit/contested_findings_{source}_{ts}.md
```

(Omit the "Contested:" line if `N_contested == 0`.)

**Interactive mode:** Display the validation status table (verdict counts), then ask:

> Write validated report and contested findings files? [Y/n]

On Y or empty input, write both files. After writing, if `N_contested > 0`, offer:

> Run `/autoskillit:prepare-issue` for contested findings? [y/N]

If the user confirms, pass the contested findings file path to `prepare-issue`.

---

## Output Location

```
.autoskillit/temp/validate-audit/
├── validated_report_{source}_{YYYY-MM-DD_HHMMSS}.md    (always written)
└── contested_findings_{source}_{YYYY-MM-DD_HHMMSS}.md  (when N_contested > 0)
```

`{source}` is `arch`, `tests`, or `cohesion` based on the input report.

## Related Skills

- `/autoskillit:audit-arch` — produces reports this skill validates
- `/autoskillit:audit-tests` — produces reports this skill validates
- `/autoskillit:audit-cohesion` — produces reports this skill validates
- `/autoskillit:prepare-issue` — offered interactively for contested findings
