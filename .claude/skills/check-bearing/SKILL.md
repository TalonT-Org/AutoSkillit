---
name: check-bearing
description: Assess a branch or PR's alignment with the strategic compass. Evaluates whether changes advance, drift from, or close off strategic directions. Produces an alignment dashboard with per-direction impact analysis and a verdict.
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo '[SKILL: check-bearing] Checking strategic alignment...'"
          once: true
---

# Check Bearing — Strategic Alignment Review

## When to Use

- Before merging a branch — verify it does not close off strategic paths
- During PR review — understand how changes affect long-term options
- After a sprint — assess cumulative alignment of recent work
- When evaluating whether to pursue a particular implementation approach
- As a gate in the implementation or merge-prs recipe

## Arguments

```
/check-bearing {target} compass_path=<path>
```

- `{target}` — Required. One of:
  - A branch name (e.g., `feature/add-provenance`)
  - A PR number (e.g., `#142`)
  - A PR URL (e.g., `https://github.com/owner/repo/pull/142`)
  - `HEAD` — assess uncommitted + staged changes on the current branch
  - `--since <branch>` — assess all changes since divergence from that branch

- `compass_path=<path>` — Required. Absolute or relative path to a compass
  document produced by `/chart-course`. The file must contain a
  `---compass-data---` ... `---end-compass-data---` block.

### Target Resolution

**Branch name:**
- Detect base branch via `git merge-base` against `main` and `develop`
- Use the closer base (fewer commits ahead)
- Diff: `git diff {merge_base}...{branch}`

**PR number or URL:**
- Extract PR metadata via `gh pr view {number} --json baseRefName,title,body,files`
- Diff: `gh pr diff {number}`

**HEAD:**
- Diff: `git diff` (unstaged) + `git diff --cached` (staged)

**--since <branch>:**
- Diff: `git diff {branch}...HEAD`
- Log: `git log --oneline {branch}...HEAD`

## Critical Constraints

**NEVER:**
- Modify any source code files — this is a read-only analysis skill
- Create files outside `{{AUTOSKILLIT_TEMP}}/check-bearing/`
- Assess implementation quality — that is audit-impl's domain
- Recommend specific code changes — report alignment facts, not fixes
- Skip directions because they seem irrelevant — assess ALL directions in the compass
- Mark a direction as CLOSES without specific architectural evidence showing
  why the change creates a barrier that would require significant rework to undo
- Use the compass priority field to suppress findings — all closures matter
  regardless of priority
- Conflate "this change doesn't advance direction X" with "this change harms
  direction X" — most changes are genuinely NEUTRAL to most directions

**ALWAYS:**
- Use `model: "sonnet"` for all Task tool subagent calls
- Initialize code-index via `set_project_path` before exploration (Step 0.5)
- Parse the `---compass-data---` block before launching any analysis subagents
- Provide specific file-level evidence for every non-NEUTRAL assessment
- Flag stale compass data (>30 days old) with a prominent warning
- Distinguish between intentional advancement and incidental impact
- Emit output tokens as literal plain text (no markdown bold/italic formatting on token names)
- Include a confidence qualifier (HIGH / MEDIUM / LOW) on each direction assessment
- Default to NEUTRAL — the burden of proof is on non-neutral classification

## Workflow

### Step 0: Parse Arguments

Extract `target` and `compass_path` from ARGUMENTS.

For `compass_path`: scan tokens for `compass_path=<value>`.
For `target`: everything in ARGUMENTS that is not the `compass_path=` parameter.

Verify `compass_path` exists using Glob. If not found:
```
verdict = ERROR
%%ORDER_UP%%
```
Exit immediately.

Determine target type:
- Starts with `#` or contains `/pull/` → PR mode
- Equals `HEAD` → working tree mode
- Starts with `--since` → since mode
- Otherwise → branch mode

### Step 0.5: Code-Index Initialization

```
mcp__code-index__set_project_path(path="{PROJECT_ROOT}")
```

If the code-index MCP server is unavailable, fall back to native Glob/Grep.

### Step 1: Load and Parse Compass

1. Read the compass document at `compass_path`
2. Locate the `---compass-data---` block
3. Parse the YAML content between `---compass-data---` and `---end-compass-data---`
4. Extract all directions into a structured catalog
5. Check the `generated` timestamp:
   - If >30 days old, set `stale_warning = true`
   - If >90 days old, set `stale_warning = critical`
6. Count total directions, group by category and priority

If the compass-data block is missing or unparseable:
```
verdict = ERROR
%%ORDER_UP%%
```
Exit immediately.

### Step 2: Analyze Branch Changes

Execute the appropriate commands based on target type determined in Step 0.

Capture these artifacts for subagent distribution:
- `changed_files` — list of all modified, added, and deleted file paths
- `full_diff` — complete unified diff output
- `diff_stats` — `git diff --stat` summary (files changed, insertions, deletions)
- `commit_messages` — `git log --oneline` output (empty for HEAD mode)
- `pr_metadata` — title, body, labels (PR mode only)

If the diff is empty (no changes), emit:
```
verdict = ALIGNED
alignment_score = 0.0
path_openness = 1.0
dashboard_path = (none — no changes to assess)
%%ORDER_UP%%
```
Exit immediately.

### Step 3: Pre-Filter Directions by Signal Overlap

Before launching expensive per-direction subagents, perform a quick signal match
to avoid spawning 30+ subagents for a 10-line change.

For each direction in the compass catalog:

1. **File signal match:** Do any `signals.files` entries appear as prefixes of
   paths in `changed_files`? (Use path prefix matching, not exact match —
   `src/autoskillit/execution/` matches `src/autoskillit/execution/headless.py`)

2. **Pattern signal match:** Do any `signals.patterns` appear anywhere in `full_diff`?
   (Plain substring search, case-sensitive)

3. **Module signal match:** Do any `signals.modules` appear as path components in
   `changed_files`? (e.g., module `execution` matches path `src/autoskillit/execution/foo.py`)

Classify each direction:
- **SIGNAL_HIT** — at least one signal matched. Requires full subagent analysis.
- **NO_SIGNAL** — zero signals matched. Gets lightweight bulk assessment only.

### Step 4: Launch Parallel Direction Assessment Subagents

#### 4a: Full Assessment (SIGNAL_HIT directions)

Launch one subagent per SIGNAL_HIT direction. Cap at 8 concurrent subagents;
if more than 8 SIGNAL_HIT directions, batch into waves of 8.

Each subagent receives:
- The direction's full metadata (id, name, category, description, readiness,
  readiness_evidence, dependencies, enables, conflicts, signals)
- The portions of `full_diff` for files matching the direction's signals
- The full `diff_stats` for overall context
- The `commit_messages` for intent signals

Each subagent evaluates this rubric and returns structured JSON:

```json
{
  "direction_id": "D001",
  "impact": "ADVANCES | SUPPORTS | NEUTRAL | DRIFT | CLOSES",
  "confidence": "HIGH | MEDIUM | LOW",
  "score": 0,
  "evidence": [
    "src/path/file.py:42 — adds FooProtocol that direction D001 requires",
    "src/path/other.py:deleted — removes BarAdapter that D001 depends on"
  ],
  "explanation": "One sentence explaining why this impact was chosen",
  "reversibility": "easy | moderate | hard",
  "intentional": true
}
```

**Impact definitions (subagents must follow these strictly):**

| Impact | Score | Meaning | Evidence Required |
|--------|-------|---------|-------------------|
| ADVANCES | +2 | Changes directly implement or build toward this direction | Specific new code, protocols, or infrastructure that the direction needs |
| SUPPORTS | +1 | Changes create favorable conditions without targeting this direction | Refactoring, abstractions, or cleanup that happens to help |
| NEUTRAL | 0 | No meaningful positive or negative impact | Default — absence of evidence for other categories |
| DRIFT | -1 | Changes make this direction slightly harder or less natural | New coupling, hardcoded assumptions, or patterns that work against the direction |
| CLOSES | -2 | Changes create barriers requiring significant rework to pursue this direction | Concrete architectural decisions that conflict (not just "didn't advance it") |

**Confidence definitions:**
- HIGH — clear, direct evidence in the diff for the classification
- MEDIUM — indirect evidence or reasonable inference from the changes
- LOW — assessment based on broader implications, not specific diff lines

#### 4b: Bulk Assessment (NO_SIGNAL directions)

Group NO_SIGNAL directions into batches of 5. Launch one subagent per batch.

Each bulk subagent receives:
- The 5 directions' metadata (id, name, description, signals)
- The `diff_stats` summary (NOT the full diff — these directions had no signal overlap)
- The `commit_messages`

Most NO_SIGNAL directions should classify as NEUTRAL. The subagent's job is to
catch cases where the signal list was incomplete — if the diff clearly impacts
a direction despite no signal match, flag it with confidence: LOW.

Returns the same JSON structure for each direction in the batch.

Launch additional subagents as needed for any other aspects the diff
warrants — security implications, API surface changes, configuration
coupling, etc. The above are the mandatory minimum.

### Step 5: Cross-Cutting Path Closure Analysis

After all Step 4 subagents complete, launch one additional subagent that
reviews ALL non-NEUTRAL findings holistically. This subagent looks for:

**Cascade effects:**
Does DRIFT on direction A combined with DRIFT on direction B effectively
CLOSE direction C (which depends on both A and B)?

For each potential cascade:
```json
{
  "direction_id": "D007",
  "caused_by": ["D001", "D003"],
  "explanation": "D007 depends on both D001 and D003; drift on both compounds"
}
```

**Irreversibility assessment:**
Are any changes in the diff particularly hard to undo?
- Public API surface changes (consumers may depend on them)
- Database schema or data format changes
- Removed abstractions that were protecting flexibility
- New concrete dependencies that replace protocol-based injection

```json
{
  "file": "src/path/file.py",
  "line": 42,
  "concern": "Replaces SubprocessRunner protocol with direct subprocess call",
  "affects_directions": ["D005", "D011"]
}
```

**New coupling introduced:**
Does the diff create new import relationships or dependencies between
modules that were previously independent?

```json
{
  "from_module": "recipe",
  "to_module": "execution",
  "concern": "Direct import bypasses pipeline layer",
  "affects_directions": ["D008"]
}
```

### Step 6: Compute Alignment Scores

Aggregate all subagent results into project-level metrics.

**Per-direction impact scores** — from Step 4 subagent assessments:
- ADVANCES = +2, SUPPORTS = +1, NEUTRAL = 0, DRIFT = -1, CLOSES = -2

**Priority weights:**
- high = 3, medium = 2, low = 1, exploratory = 0.5

**Weighted alignment score:**
```
alignment_score = sum(score_i * weight_i) / sum(2 * weight_i)
```
Range: -1.0 (every direction at max negative impact) to +1.0 (every direction
maximally advanced). Most real changes will be near 0.0.

**Path openness:**
```
path_openness = count(directions NOT scored CLOSES) / total_directions
```
Range: 0.0 to 1.0. Measures what fraction of strategic paths remain unblocked.

**Critical closures:**
- Any direction with priority = high AND impact = CLOSES
- Any cascade closure from Step 5 affecting a high-priority direction

### Step 7: Determine Verdict

Apply this logic strictly:

```
IF any critical_closures exist:
    verdict = CONFLICT

ELSE IF alignment_score < -0.3
     OR path_openness < 0.70:
    verdict = DRIFT

ELSE IF alignment_score >= 0.0
     AND path_openness >= 0.90:
    verdict = ALIGNED

ELSE:
    verdict = DRIFT
```

**Verdict definitions:**
- **ALIGNED** — Changes advance or are neutral to strategic directions. No paths
  closed. The project maintains or improves its strategic optionality.
- **DRIFT** — Changes push away from some directions without closing them off
  entirely. Not blocking, but worth awareness. May indicate a need for
  compensating work in a future branch.
- **CONFLICT** — Changes close off high-priority strategic paths. Merging would
  reduce strategic optionality in ways that are hard to reverse. Review and
  discuss before proceeding.

**CONFLICT does not mean "do not merge."** It means "merge with eyes open."
Some path closures are intentional strategic decisions. The skill reports facts;
humans decide.

### Step 8: Write Alignment Dashboard

Write to:
`{{AUTOSKILLIT_TEMP}}/check-bearing/alignment_{target_slug}_{YYYY-MM-DD_HHMMSS}.md`

where `{target_slug}` is a snake_case slug derived from the target
(branch name, PR number, or "head"). Max 40 chars.

**Required sections in order:**

#### 1. Target Summary
Branch/PR name, base branch, commit count, files changed, lines added/removed.
If PR mode, include PR title and labels.

#### 2. Compass Reference
Path to compass document, generated date, total directions in compass.
If `stale_warning` is set, include a prominent warning:
- `>30 days`: "Compass is N days old. Consider running chart-course to refresh."
- `>90 days`: "STALE COMPASS: N days old. Results may not reflect current architecture."

#### 3. Verdict
One of `ALIGNED` / `DRIFT` / `CONFLICT` with a one-sentence rationale
grounded in the specific findings.

#### 4. Alignment Scorecard

| Metric | Value |
|--------|-------|
| Alignment Score | {-1.0 to 1.0} |
| Path Openness | {0.0 to 1.0} ({X}/{Y} directions open) |
| Directions Advanced | {count} |
| Directions Drifted | {count} |
| Directions Closed | {count} |
| Critical Closures | {count} |

#### 5. Direction Impact Table
All directions sorted by absolute impact score (highest impact first):

| ID | Name | Priority | Impact | Score | Confidence | Evidence Summary |
|----|------|----------|--------|-------|------------|------------------|

NEUTRAL directions may be collapsed into a summary count rather than
listed individually if there are more than 15.

#### 6. Path Closure Warnings
Detailed analysis of any CLOSES findings or cascade closures.
For each closure:
- Direction name and priority
- Specific evidence from the diff
- Reversibility assessment
- Which other directions are affected

If no closures, state "No path closures detected."

#### 7. Advancement Highlights
Directions being actively advanced by these changes.
For each advancement:
- What specifically in the diff advances this direction
- Whether the advancement appears intentional or incidental

#### 8. Drift Concerns
Directions being drifted from. For each:
- What in the diff causes drift
- Reversibility (easy/moderate/hard)
- Whether compensating work in a future branch could restore alignment

#### 9. Cross-Cutting Analysis
From Step 5: cascade effects, irreversibility concerns, new coupling.
If none, state "No cross-cutting concerns identified."

#### 10. Recommendations
Strategic awareness items — NOT code change suggestions. Examples:
- "Direction D005 (distributed compute) is drifting. If still a priority,
  consider a follow-up branch that restores the SubprocessRunner protocol."
- "This branch intentionally advances D001 at the cost of D012. Ensure
  this trade-off is an explicit decision."

#### 11. Machine-Readable Alignment Block

This block MUST appear at the very end of the document:

```
---alignment-data---
version: 1
target: "{target}"
compass_path: "{compass_path}"
compass_generated: "{ISO-8601 from compass}"
generated: "{ISO-8601 now}"
verdict: ALIGNED
alignment_score: 0.42
path_openness: 0.95
direction_count: 28
impacts:
  - id: D001
    impact: ADVANCES
    score: 2
    confidence: HIGH
  - id: D002
    impact: NEUTRAL
    score: 0
    confidence: HIGH
  - id: D003
    impact: DRIFT
    score: -1
    confidence: MEDIUM
critical_closures: []
cascade_closures: []
---end-alignment-data---
```

### Step 9: Terminal Summary and Output Tokens

Print a brief terminal summary:

```
=== Strategic Alignment: {target} ===
Verdict: {ALIGNED|DRIFT|CONFLICT}
Alignment Score: {score}  |  Path Openness: {openness}

Advances: D001 (Provenance Layer), D003 (Metrics Contract)
Drift: D007 (Ghost Kitchens) — reversibility: easy
Closures: (none)

Dashboard: {path}
```

Then emit the output tokens as the absolute last lines:

```
verdict = {ALIGNED|DRIFT|CONFLICT}
alignment_score = {float}
path_openness = {float}
dashboard_path = {absolute_path}
%%ORDER_UP%%
```

**Token rules:** Plain text only. No markdown bold, italic, or backtick
formatting on token names. The pipeline adjudicator uses regex matching
and decoration breaks it.

## Output

| Token | Value | Recipe Routing |
|-------|-------|----------------|
| `verdict` | `ALIGNED`, `DRIFT`, or `CONFLICT` | `on_result: field: verdict` matches exact string |
| `alignment_score` | Float from -1.0 to 1.0 | Available via `${{ context.alignment_score }}` |
| `path_openness` | Float from 0.0 to 1.0 | Available via `${{ context.path_openness }}` |
| `dashboard_path` | Absolute path to alignment dashboard | Available via `${{ context.dashboard_path }}` |

## Related Skills

- `/chart-course` — Build or update the strategic compass this skill reads
- `/autoskillit:review-pr` — Code quality review (complementary — quality vs. alignment)
- `/autoskillit:audit-impl` — Plan compliance audit (complementary — plan vs. strategy)
- `/autoskillit:review-design` — Experiment design review (complementary — design validity vs. strategic fit)
