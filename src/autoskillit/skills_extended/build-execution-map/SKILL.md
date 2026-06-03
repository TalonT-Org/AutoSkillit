---
name: build-execution-map
categories: [github]
backend_requirements: [claude-code]
uses_capabilities: [agent_model]
description: Analyze issue dependencies and produce a dispatch execution map for parallel orchestration
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo '⚙️ [SKILL: build-execution-map] Analyzing issue dependencies...'"
          once: true
---

# build-execution-map

Analyze a set of GitHub issues for dependency relationships using AI-driven pairwise
assessment, then produce a structured execution map JSON artifact that partitions issues
into dependency-ordered dispatch groups.

## When to Use

- Before parallel dispatch of multiple issues to prevent merge conflicts
- When sous-chef receives a `parallel` request for N ≥ 2 issues
- As a standalone pre-analysis step before running an implementation campaign

## Arguments

Space-separated issue numbers (required, minimum 2), plus optional flags:
- `--base-ref <branch>` — base branch to compare against (default: `main`)
- `--assess-review-approach` — assess whether each issue would benefit from a review-approach research pass before implementation (default: inactive)
- `--max-parallel <N>` — maximum number of issues in any single parallel group (default: `6`). Groups exceeding this cap are split into sequential sub-groups of at most N issues each.

**Example:** `101 103 102 --base-ref main --max-parallel 4 --assess-review-approach`

## Critical Constraints

**NEVER:**
- Fabricate, invent, or embellish information not supported by the available evidence or code.

- Modify any source code files
- Create files outside `{{AUTOSKILLIT_TEMP}}/build-execution-map/` directory (relative to the current working directory)
- Assume issues are independent without analysis
- Launch implementation pipelines — this skill only produces the map
- Use the `execution_map` or `execution_map_report` token names with unspaced `=` (always use `key = value` format)
- Override the AI's parallelism judgment with mechanical rules
- Assume issues conflict based solely on file-name overlap without reading the issue descriptions
- Run subagents in the background (`run_in_background: true` is prohibited)
- Treat a medium-severity cross-assessment as grounds for deferral — only critical severity defers
- Emit has_deferred / deferred_count / dispatched_count with markdown decorators
- Issue subagent Task calls sequentially — ALL must be in a single parallel message

**ALWAYS:**
- Use parallel subagents (up to 8) for issue fetching in Step 1
- Spawn all subagents via `Agent(model="sonnet")`
- Write both JSON and markdown report outputs to `{{AUTOSKILLIT_TEMP}}/build-execution-map/`
- Emit `execution_map` and `execution_map_report` tokens with absolute paths (use `$(pwd)` to resolve the working directory prefix)
- Emit structured output tokens as the final lines of text output (plain text, no markdown decorators)
- Check the actual codebase when uncertain whether two issues' changes overlap
- Capture per-pair reasoning in `pairwise_assessments` for auditability
- Anchor all output paths to the current working directory
- Issue all Task calls in a single message to maximize parallelism

## Workflow

### Step 0 — Parse Arguments

Accept issue numbers as space-separated or comma-separated values. Parse `--base-ref`
if present (default: `main`). Parse `--assess-review-approach` if present (default:
inactive). When this flag is active, Step 2 will additionally assess each issue for
review-approach benefit. Parse `--max-parallel` if present (default: `6`). Validate that
it is a positive integer ≥ 1; if a non-positive or non-integer value is provided, abort
with `"Error: --max-parallel must be a positive integer"`. Validate the issue count:
- **Zero issues**: abort immediately with `"Error: build-execution-map requires at least 1 issue number"` and exit non-zero.
- **One issue**: emit a warning and write a trivial single-group map (single issue always gets `parallel: false`).
- **Two or more issues**: proceed to Step 1.

### Step 1 — Fetch Issue Data (parallel subagents) (SINGLE MESSAGE)

**Issue ALL Task tool calls in a single message — one per item — so they execute in parallel. Do NOT iterate across multiple turns.**

Do not output any prose between subagent dispatches. Immediately proceed to the next tool call.

Launch up to 8 parallel `sonnet` subagents, one per issue. Each subagent:
1. Calls `gh issue view {N} --json number,title,body,labels`. If the call fails (non-zero
   exit — issue not found, auth error, network failure), the subagent must abort and surface
   the error; do not return a partial or empty result.
2. Returns raw issue data: `{"number": N, "title": "..."}` — no structured extraction of
   `affected_files` or `depends_on`. The issue body is read directly by the parent in Step 2.

In the same parallel wave, launch **one additional task** to fetch the ambient in-progress
and queued context:

```bash
gh issue list --state open --label "{{github.in_progress_label}}" \
  --json number,title,body,labels,updatedAt --limit 50

gh issue list --state open --label "{{github.queued_label}}" \
  --json number,title,body,labels,updatedAt --limit 50
```

Use the config value `github.in_progress_label` (default: `"in-progress"`) and
`github.queued_label` (default: `"queued"`) as the label names. Merge both result sets
(deduplicate by issue number) into the ambient context. From the combined list:
- **Exclude** any issue whose number appears in the current target set — those are being
  handled by this session and are already represented in the pairwise assessment.
- **Exclude** any issue whose labels contain the staged label (`github.staged_label`,
  default: `"staged"`) — staged issues have already landed on the integration branch.
- Treat queued issues with lower default conflict severity than in-progress issues
  since they haven't started file modifications yet.
- The remaining issues form the **ambient context** set.

If the `gh issue list` call fails (auth error, network failure), log the error and set the
in-progress context to an empty list — do not abort the skill. An empty in-progress context
is always safe: the skill behaves identically to today.

Do not output any prose between subagent launches — immediately collect results when all
subagents complete. **All subagents must succeed** before advancing to Step 2. If any
subagent fails or returns no JSON block, abort the skill with the subagent's error message
and the failing issue number.

### Step 2 — Assess Parallelism (AI-driven)

Read all issue descriptions holistically. For each pair with potential overlap (shared
domain, cross-references, related concerns), determine whether the issues can be safely
implemented in parallel.

When uncertain, check the actual codebase: read files, inspect structure, use code-index
tools (`get_file_summary`, `get_symbol_body`, `search_code_advanced`) or native Read/Grep.

Produce a per-pair assessment for each pair where a decision is needed:
```json
{
  "pair": [1155, 1156],
  "parallel_safe": true,
  "confidence": "high",
  "reasoning": "#1156 modifies _build_l3_orchestrator_prompt() at lines 70-106. #1155 adds new function at end of file. Different symbols, non-adjacent."
}
```

Constraints:
- `confidence: "low"` requires `parallel_safe: false` (structural, not advisory)
- Pairs that are obviously independent (completely different areas) don't need an assessment
  entry — they default to `parallel_safe: true`; when every pair is obviously independent,
  `pairwise_assessments` is an empty array `[]` (this is valid output)
- Natural language signals ("depends on #X", "can be parallel with #Y") are understood from
  issue context, not parsed by regex
- Cross-references in "Files NOT to Change", code blocks, or diagnostic sections are context,
  not dependency signals

#### Step 2b — Cross-Assessment (in-progress context)

When the in-progress context set is non-empty, perform a second assessment pass: for each
(target issue, in-progress issue) pair, evaluate conflict potential.

Apply a **higher tolerance threshold** than pairwise assessment — simple file-level overlap
is not sufficient to flag. Target: semantic conflicts, undeclared dependencies, or
architectural tensions where implementing the target issue against the pre-in-progress
codebase will produce incorrect results or wasted effort. Factor `updatedAt` recency: issues
with no activity in 30+ days carry lower conflict weight — their in-progress label may be
stale.

For each pair that requires assessment, produce:

```json
{
  "target_issue": 1155,
  "in_progress_issue": 887,
  "conflict_severity": "low" | "medium" | "critical",
  "conflict_type": "file_overlap" | "semantic_dependency" | "undeclared_dependency" | "architectural_tension",
  "reasoning": "one-sentence explanation",
  "recommendation": "proceed" | "defer" | "escalate"
}
```

**Severity definitions:**
- `low` — Minor file overlap. `resolve-merge-conflicts` will handle this. Recommendation: `proceed`. Only record in JSON; suppress from the report to reduce noise.
- `medium` — Significant overlap or potential indirect dependency, resolvable. Recommendation: `proceed` with a warning annotation in the report.
- `critical` — Semantic conflict, undeclared dependency, or architectural tension where implementing the target issue now will produce incorrect results or be discarded. Recommendation: `defer`.

When the in-progress context is empty, omit Step 2b entirely — `cross_assessments` is `[]`.

#### Investigation-Complete Detection (unconditional)

For each issue, check whether the issue body contains the `<!-- investigation_complete: true -->`
HTML comment marker. Set `investigation_complete: true` or `false` per issue. This is a simple
substring check — no LLM assessment required.

Emit a terminal token when at least one issue has the marker:
```
investigation_complete_issues = 1155,1160
```

#### Review-Approach Benefit Assessment (conditional)

When `--assess-review-approach` is active, perform an additional assessment for each issue
after the pairwise parallelism analysis. This assessment determines whether the issue would
meaningfully benefit from a `review-approach` research pass before implementation.

**First**, read the `review-approach` skill definition at
`src/autoskillit/skills_extended/review-approach/SKILL.md` to ground your understanding of
what that skill actually provides — external web research on modern solutions, approaches,
and trade-offs. Do not rely on a hardcoded heuristic list; use the skill definition as the
primary reference for what review-approach offers.

**Then**, for each issue, evaluate whether the problem domain would benefit from that research:

Signals that review-approach would benefit:
- Issue involves integrating an unfamiliar external library or API
- Issue proposes a design decision with multiple viable architectural approaches
- Issue references emerging patterns, standards, or technologies the codebase hasn't used
- Issue body contains open questions about *how* to approach the problem
- Issue requires understanding trade-offs between competing solutions

Signals that review-approach is NOT needed:
- Issue is a well-scoped bug fix with a clear root cause
- Issue is internal refactoring following established codebase patterns
- Issue adds a feature using patterns already present in the codebase
- Issue is a documentation update or configuration change
- Issue body already contains a fully specified implementation approach

These heuristics are illustrative. Use judgment informed by the actual `review-approach`
SKILL.md to decide each issue.

For each issue, produce:
- `review_approach_recommended`: `true` if the issue would benefit, `false` otherwise
- `review_approach_reasoning`: one-sentence explanation of why or why not

When `--assess-review-approach` is NOT active, omit these fields entirely from the output.

### Step 3 — Assemble Groups and Merge Order

Using the pairwise assessments from Step 2, partition issues into dispatch groups:
- Issues that are `parallel_safe` with all other group members go in the same group
  (`parallel: true`)
- Issues with conflicts or dependency order constraints go in separate groups, ordered by
  dependency direction (the issue that others depend on goes first)
- Groups with a single issue get `parallel: false`

Merge order within parallel groups: the issue whose changes are depended upon by other
issues in the group merges first; the issue whose changes build on top of others merges
last. When no dependency relationship exists between issues in the same parallel group,
order by estimated impact breadth (narrower-scope changes first).

The `merge_order` list is the flattened sequence of issue numbers across groups in dispatch
order.

#### Step 3a — Verify Group Ordering Invariants

Before proceeding to Step 3b, work through each check below. If any check fails, re-partition
the groups to fix the violation before continuing.

1. **Dependency direction**: For every `pairwise_assessment` with `parallel_safe: false`, the
   issue identified as the dependency is in a numerically lower-numbered group than the issue
   that depends on it.
2. **Transitivity**: If A must precede B (from one assessment) and B must precede C (from
   another assessment), then `group(A) < group(B) < group(C)`.
3. **Merge order respects dependencies**: For every `pairwise_assessment` with
   `parallel_safe: false` where both issues are in the same parallel group, the dependency
   issue appears earlier in the group's `merge_order` position.
4. **No duplicate assignments**: No issue appears in more than one group.
5. **Group count consistency**: `group_count` equals the actual number of entries in `groups[]`.
6. **Merge order completeness**: `merge_order` contains exactly the issue numbers from all
   `groups[]` entries, with no omissions and no extras.

#### Step 3b — Deferred Issue Partitioning

After assembling dispatch groups, partition them based on deferral status from Step 2b:

1. Identify deferred issues: any target issue with at least one `critical` cross-assessment.
2. Partition each assembled group:
   - If **all** issues in the group are deferred: move the entire group to `deferred_groups[]`.
     Set `gated_by` to the union of all blocker issue numbers from the group's issues' critical
     cross-assessments.
   - If **some** issues are deferred and others are not: split the group. Non-deferred issues
     remain in `groups[]` (adjusting `parallel` to `false` if the group shrinks to 1 issue).
     Deferred issues form a new entry in `deferred_groups[]` with the appropriate `gated_by`
     (and `parallel: false` if the deferred sub-group contains only 1 issue — same rule as `groups[]`).
   - If **no** issues are deferred: keep the entire group in `groups[]`.
3. Re-number groups sequentially in both `groups[]` (starting from 1) and `deferred_groups[]`
   (starting from 1, independent numbering).
4. Compute `merge_order` from `groups[]` and `deferred_merge_order` from `deferred_groups[]`,
   both following the same dependency-first ordering rule from Step 3.
5. Compute:
   - `deferred_count` = total issues across all `deferred_groups[]` entries
   - `dispatched_count` = `total_issues` − `deferred_count`
   - `has_deferred` = `true` if `deferred_count > 0`, else `false`

Each `deferred_groups[]` entry has the same structure as `groups[]` (`group`, `parallel`,
`issues[]`) plus a `gated_by` field listing the in-progress blocker issue numbers as an array.

When no cross-assessments produce `critical` severity, this step is a no-op: `deferred_groups`
is `[]`, `deferred_merge_order` is `[]`, `has_deferred` is `false`, `dispatched_count`
equals `total_issues`.

### Step 3.5 — Apply Parallel Cap

After groups are assembled in Step 3, enforce the `max_parallel` cap:

1. For each **parallel** group (`parallel: true`) where `len(issues) > max_parallel`:
   - Chunk the issue list into sub-lists of at most `max_parallel` issues each, preserving
     the existing order determined in Step 3.
   - Each sub-list becomes its own group. Set `parallel: true` if `len(sub-list) > 1`;
     set `parallel: false` if `len(sub-list) == 1` (single-issue groups are never parallel).
   - The original group's `parallel_safe` ordering is preserved — do not re-sort.
   - Sequential groups (`parallel: false`) are never split — they are passed through unchanged
     regardless of size.
   - When splitting, apply the dependency-first ordering rule from Step 3 to decide which
     issues land in the first sub-group vs. later sub-groups: issues that others depend on
     go in the first sub-group.

2. Renumber all groups sequentially (1, 2, 3, …) after splitting. If original Group 1
   splits into 2 sub-groups and original Group 2 remains intact, the result is:
   Group 1 (sub-group A of original Group 1), Group 2 (sub-group B of original Group 1),
   Group 3 (original Group 2).

3. Update `merge_order` to reflect the new group ordering: issues in Group 1 appear before
   Group 2, which appears before Group 3, etc. Within each sub-group, the relative order
   from Step 3 is preserved.

4. Update `group_count` to the total count of groups after splitting.

When no group exceeds `max_parallel`, this step is a no-op — groups pass through unchanged.

### Step 4 — Write Output

Compute timestamp `{YYYY-MM-DD_HHMMSS}` (current local time, second precision).

Create the output directory before writing:
```bash
mkdir -p "{{AUTOSKILLIT_TEMP}}/build-execution-map"
```
If directory creation fails (permission error, missing parent), abort with an explicit
error message identifying the failed path — do not silently proceed.

Write two files to `{{AUTOSKILLIT_TEMP}}/build-execution-map/` (relative to the current
working directory):

1. **`execution_map_{YYYY-MM-DD_HHMMSS}.json`** — full structured artifact (schema below)
2. **`execution_map_report_{YYYY-MM-DD_HHMMSS}.md`** — human-readable summary including:
   - Pairwise assessment table (pair | parallel_safe | confidence | reasoning summary)
   - Group assignments table (group | parallel | issues)
   - Merge order list
   - Any warnings (single-issue shortcut, low-confidence overrides, etc.)
   - Review-approach recommendations table (issue | recommended | reasoning) — only when
     `--assess-review-approach` is active
   - **"## Deferred Issues — Awaiting In-Progress Resolution"** section (only when
     `has_deferred = true`) listing: deferred issue number and title, blocking in-progress
     issue number(s) and title(s), conflict type and reasoning, recommendation

Emit structured output tokens as the last lines of text output:
```
execution_map = {absolute_path_to_json}
execution_map_report = {absolute_path_to_report}
group_count = {int}
total_issues = {int}
dispatched_count = {int}
deferred_count = {int}
has_deferred = {true|false}
review_approach_candidates = {comma-separated issue numbers}
```

Emit `dispatched_count`, `deferred_count`, and `has_deferred` unconditionally (even when
`has_deferred = false` and `deferred_count = 0`).

The `review_approach_candidates` token is conditional: emit it only when
`--assess-review-approach` is active AND at least one issue has
`review_approach_recommended: true`. The value is a comma-separated list of issue numbers
(e.g., `1155,1158`). When no issues are recommended or the flag is inactive, omit this
token entirely.

## Context Limit Behavior

This skill writes output files to `{{AUTOSKILLIT_TEMP}}/build-execution-map/` and emits
structured output tokens. If context is exhausted mid-execution:

1. Before emitting any structured output tokens, verify that both output files
   (execution map JSON and report markdown) exist on disk.
2. If the files exist, emit the structured tokens and exit normally.
3. If context exhaustion interrupts before files are written, the caller's
   `on_context_limit` routing handles escalation — do not attempt partial output.

## Output JSON Schema

```json
{
  "generated_at": "ISO-8601",
  "base_ref": "main",
  "total_issues": 3,
  "dispatched_count": 2,
  "deferred_count": 1,
  "has_deferred": true,
  "max_parallel": 6,
  "group_count": 1,
  "groups": [
    {
      "group": 1,
      "parallel": true,
      "issues": [
        {"number": 1155, "title": "..."},
        {"number": 1156, "title": "..."}
      ]
    }
  ],
  "merge_order": [1155, 1156],
  "in_progress_context": [
    {"number": 887, "title": "franchise: per-recipe tool-surface test suite"}
  ],
  "pairwise_assessments": [
    {
      "pair": [1155, 1156],
      "parallel_safe": true,
      "confidence": "high",
      "reasoning": "#1156 modifies _build_l3_orchestrator_prompt() at lines 70-106. #1155 adds new function at end of file. Different symbols, non-adjacent."
    }
  ],
  "cross_assessments": [
    {
      "target_issue": 1158,
      "in_progress_issue": 887,
      "conflict_severity": "critical",
      "conflict_type": "undeclared_dependency",
      "reasoning": "...",
      "recommendation": "defer"
    }
  ],
  "deferred_groups": [
    {
      "group": 1,
      "parallel": false,
      "gated_by": [887],
      "issues": [
        {"number": 1158, "title": "..."}
      ]
    }
  ],
  "deferred_merge_order": [1158]
}
```

**Schema notes:**
- `total_issues` counts ALL input issues (backward-compatible — callers expect `total_issues == len(input_issues)`)
- `deferred_groups`, `deferred_merge_order`, `in_progress_context`, `cross_assessments` are JSON-body-only — NOT emitted as terminal output tokens
- `has_deferred`, `deferred_count`, `dispatched_count` ARE emitted as terminal output tokens

When `--assess-review-approach` is active, each issue object gains two additional fields:

```json
{
  "number": 1155,
  "title": "...",
  "review_approach_recommended": true,
  "review_approach_reasoning": "Issue proposes a new caching layer with multiple viable strategies. External research would surface current best practices and library maturity."
}
```

Note on `deferred_merge_order`: No downstream code path currently reads this field. It is
included for structural completeness and to support future supplementary BEM optimization
(Step 6e can reuse pre-computed ordering). The ordering follows the same dependency-first
rule as `merge_order` and is equally authoritative.

When the flag is inactive, these fields are omitted entirely (not set to defaults).
