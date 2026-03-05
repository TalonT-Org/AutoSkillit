---
name: triage-issues
description: Analyze open GitHub issues and produce a sequenced implementation plan — grouping issues into parallel batches, ordering those batches, and tagging each issue with its recipe route. Use when user says "triage issues", "prioritize issues", or "plan issue order".
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo 'Triaging issues...'"
          once: true
---

# Issue Triage Skill

Analyze open GitHub issues, classify each into a recipe route, group them into parallel implementation batches, and produce a structured triage report.

## When to Use

- User says "triage issues", "prioritize issues", or "plan issue order"
- Before starting a multi-issue implementation sprint
- When a backlog needs sequencing into implementable batches
- As input to a pipeline that processes issues in batch order

## Critical Constraints

**NEVER:**
- Modify any source code files
- Create or edit Python, YAML, or config files
- Guess recipe classification when confidence is low — escalate to user
- Apply GitHub labels without the `--label` flag
- Skip human escalation for ambiguous issues
- Add useless comments to the codebase — do not use the codebase as a notepad
- Create files outside `temp/triage-issues/` directory

**ALWAYS:**
- Use `model: "sonnet"` when spawning all subagents via the Task tool
- Pause for human input on ambiguous classifications
- Write the triage report and manifest to `temp/triage-issues/`
- Use `gh` CLI for all GitHub operations (not raw API calls)
- Include rationale for every recipe classification
- Record human decisions in the final report

## Workflow

### Step 0: Parse Arguments

Parse optional arguments from the user's invocation:

- `--batch-size N` — maximum issues per batch (default: 4)
- `--label` — apply recipe and batch labels to GitHub issues after triage
- `--dry-run` — run analysis but skip label application even if `--label` is set

### Step 1: Authenticate and Fetch Issues

```bash
# Verify GitHub authentication
gh auth status

# Fetch all open issues with required fields
gh issue list --state open --json number,title,body,labels,url,assignees --limit 200
```

If `gh auth status` fails, abort with a clear error message.

If there are zero open issues, skip to Step 7 and output an empty report.

### Step 2: Parallel Issue Analysis

Launch parallel subagents (up to 8) to analyze each issue. Each subagent receives one issue and must identify:

- **Affected systems** — which parts of the codebase the issue touches (e.g., `recipe/`, `server/`, `execution/`)
- **Components** — specific modules, classes, or functions referenced or implied
- **File paths** — explicitly mentioned files or files inferred from the description
- **Dependencies** — explicit issue references (`#N`) or inferred dependencies from content overlap

Use `model: "sonnet"` for all subagents.

### Step 3: Recipe Classification

Classify each issue into a recipe route using this heuristic:

| Signal | Route | Rationale |
|--------|-------|-----------|
| Bug with unclear root cause | `remediation` | Needs investigation before implementation |
| Feature with clear acceptance criteria | `implementation` | Well-defined scope, can proceed directly |
| Large/ambiguous enhancement | `remediation` | Needs decomposition and analysis first |
| Well-scoped task with known files | `implementation` | Known changes, predictable scope |

For each issue, record:
- The assigned route (`implementation` or `remediation`)
- Confidence level (`high` or `low`)
- Rationale (1 sentence explaining why)

### Step 3b: Human Escalation for Ambiguous Issues

For each issue where you cannot confidently assign a recipe route, you MUST pause
and present it to the user. Do NOT silently guess.

Present each ambiguous issue as follows:

---
**Ambiguous Issue: #{number} — "{title}"**

**Summary:** {1-2 sentence summary of what the issue asks for}

**Conflicting Signals:**
- Signal A suggests `implementation`: {reason}
- Signal B suggests `remediation`: {reason}

**Decision needed:** Route to `implementation` or `remediation`? (Or type "skip" to exclude)

**Open questions that would change routing:**
- {question that, if answered, would make the route clear}
---

Wait for the user's response before continuing to the next ambiguous issue.
Record all human decisions for inclusion in the triage report.

### Step 4: Build Conflict Graph

Build a conflict graph where:
- Each issue is a node
- Two issues share an edge if they touch overlapping systems, components, or file paths
- Edge weight reflects the degree of overlap (number of shared components)

Issues that touch completely independent systems have no edges and can safely run in parallel.

### Step 5: Greedy Batch Partitioning

Partition issues into batches of at most `batch_size` using a greedy graph-coloring approach:

1. Sort issues by edge count (most conflicts first)
2. For each issue, assign it to the earliest batch where it has no conflicts with existing members
3. If all existing batches conflict, create a new batch
4. Issues with no conflicts go into the earliest batch with remaining capacity

### Step 6: Order Batches

Order the batches for sequential execution:

1. **Dependencies first** — if issue B depends on issue A, A's batch must come before B's batch
2. **Foundation first** — batches touching infrastructure/core come before batches touching consumers
3. **Priority labels** — batches containing `priority:high` issues come earlier (tiebreaker)
4. **Age** — batches containing older issues come earlier (final tiebreaker)

### Step 7: Write Outputs

Compute timestamp: `YYYY-MM-DD_HHMMSS`.
Ensure `temp/triage-issues/` exists.

**7a. Triage report:** `temp/triage-issues/triage_report_{ts}.md`

The report contains:
- Ordered list of batches with issues, recipe assignments, and rationale
- Dependency/conflict notes explaining batch separation
- Summary statistics (total issues, batch count, recipe distribution)
- Human decisions section (which issues were escalated, what was decided)

**7b. Machine-readable manifest:** `temp/triage-issues/triage_manifest_{ts}.json`

```json
{
    "generated_at": "{ISO timestamp}",
    "batch_size": 4,
    "total_issues": 12,
    "batch_count": 3,
    "recipe_distribution": {"implementation": 8, "remediation": 4},
    "batches": [
        {
            "batch": 1,
            "priority": "first",
            "issues": [
                {
                    "number": 42,
                    "title": "Add user auth",
                    "recipe": "implementation",
                    "confidence": "high",
                    "systems": ["auth", "api"],
                    "rationale": "Well-defined feature with clear acceptance criteria"
                }
            ]
        }
    ],
    "skipped_issues": [],
    "human_decisions": [
        {"number": 55, "decision": "remediation", "reason": "User chose investigation-first"}
    ]
}
```

**7c. Optional label application (if `--label` flag):**

For each recipe label that doesn't exist yet:

```bash
gh label create "recipe:implementation" --description "Route through implementation recipe" --color "0E8A16"
gh label create "recipe:remediation" --description "Route through remediation recipe" --color "D93F0B"
```

For each batch label:

```bash
gh label create "batch:1" --description "Implementation batch 1" --color "1D76DB"
```

For each triaged issue:

```bash
gh issue edit {number} --add-label "recipe:{recipe}"
gh issue edit {number} --add-label "batch:{batch_number}"
```

## Output Location

```
temp/triage-issues/
  triage_report_{ts}.md       # Human-readable triage report
  triage_manifest_{ts}.json   # Machine-readable manifest for downstream pipelines
```

## Output Fields (for recipe capture)

The skill prints a final JSON result block for recipe capture:

```json
{
    "triage_report": "temp/triage-issues/triage_report_{ts}.md",
    "triage_manifest": "temp/triage-issues/triage_manifest_{ts}.json",
    "total_issues": 12,
    "batch_count": 3,
    "recipe_distribution": {"implementation": 8, "remediation": 4}
}
```

## Related Skills

- **`/autoskillit:analyze-prs`** — Similar batch analysis pattern, but for PRs instead of issues
- **`/autoskillit:make-groups`** — Groups requirements for planning; triage-issues groups issues for execution
- **`/autoskillit:investigate`** — Can be used to deep-dive individual issues before triage
