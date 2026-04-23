---
name: build-execution-map
categories: [github]
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

Analyze a set of GitHub issues for file-overlap and dependency relationships, then
produce a structured execution map JSON artifact that partitions issues into
topologically ordered dispatch groups.

## When to Use

- Before parallel dispatch of multiple issues to prevent merge conflicts
- When sous-chef receives a `parallel` request for N ≥ 2 issues
- As a standalone pre-analysis step before running an implementation campaign

## Arguments

Space-separated issue numbers (required, minimum 2), plus optional flags:
- `--base-ref <branch>` — base branch to compare against (default: `main`)

**Example:** `101 103 102 --base-ref main`

## Critical Constraints

**NEVER:**
- Modify any source code files
- Create files outside `{{AUTOSKILLIT_TEMP}}/build-execution-map/` directory (relative to the current working directory)
- Skip the overlap matrix computation — every issue pair must be checked
- Assume issues are independent without analysis
- Launch implementation pipelines — this skill only produces the map
- Use the `execution_map` or `execution_map_report` token names with unspaced `=` (always use `key = value` format)

**ALWAYS:**
- Use parallel subagents (up to 8) for issue analysis in Step 1
- Use `model: "sonnet"` for all subagents
- Write both JSON and markdown report outputs
- Emit `execution_map` and `execution_map_report` tokens with absolute paths (use `$(pwd)` to resolve the working directory prefix)
- Emit structured output tokens as the final lines of text output (plain text, no markdown decorators)
- Include the full `overlap_matrix` in the JSON output for auditability
- Validate that `depends_on` references only issue numbers present in the input set; silently drop any out-of-set reference and emit a warning line in the markdown report
- Anchor all output paths to the current working directory

## Workflow

### Step 0 — Parse Arguments

Accept issue numbers as space-separated or comma-separated values. Parse `--base-ref`
if present (default: `main`). Validate the issue count:
- **Zero issues**: abort immediately with `"Error: build-execution-map requires at least 1 issue number"` and exit non-zero.
- **One issue**: emit a warning and write a trivial single-group map (single issue always gets `parallel: false`).
- **Two or more issues**: proceed to Step 0.5.

### Step 0.5 — Code-Index Initialization

Call `mcp__code-index__set_project_path` with the project root (current working directory).
Fall back to native Glob/Grep if the code-index MCP is unavailable.

### Step 1 — Fetch Issue Data (parallel subagents)

Launch up to 8 parallel `sonnet` subagents, one per issue. Each subagent:
1. Calls `gh issue view {N} --json number,title,body,labels`. If the call fails (non-zero
   exit — issue not found, auth error, network failure), the subagent must abort and surface
   the error; do not return a partial or empty result.
2. Analyzes the issue body to determine:
   - **`affected_files`**: File-level paths predicted to be modified (use code-index
     exploration of the codebase + issue body analysis — search for module names, function
     names, and component names mentioned in the issue)
   - **`depends_on`**: Explicit `#N` cross-references found in the issue body, filtered
     to only those present in the current input issue set
   - **`recipe`**: Route classification — `"implementation"` for new features/additions,
     `"remediation"` for bug fixes/regressions
3. Returns a structured JSON block:
   ```json
   { "number": 101, "title": "...", "recipe": "implementation", "affected_files": ["src/foo.py"], "depends_on": [] }
   ```

Do not output any prose between subagent launches — immediately collect results when all
subagents complete. **All subagents must succeed** before advancing to Step 2. If any
subagent fails or returns no JSON block, abort the skill with the subagent's error message
and the failing issue number; do not compute a partial overlap matrix.

### Step 2 — Build Overlap Matrix

For every pair of issues `(i, j)` where `i < j`:
- Compute `shared_files = intersection(i.affected_files, j.affected_files)` using
  pairwise file intersection (set intersection of the two `affected_files` lists)
- Record pair as conflicting if `shared_files` is non-empty

This pairwise file intersection is the same set-intersection algorithm used by
`analyze-prs` Step 2, applied to predicted file sets instead of actual PR diffs
(REQ-MAP-003).

### Step 3 — Build Conflict Graph

- Each issue is a node
- Add an undirected edge between issues that share files (from Step 2 overlap matrix)
- Add a directed edge for each explicit `depends_on` relationship
- An issue pair has a conflict edge if: they share files OR one depends on the other

### Step 4 — Greedy Group Partitioning (Graph Coloring)

1. Sort issues by edge count descending (most-conflicted first)
2. For each issue in sorted order:
   - Assign to the earliest group where the issue has no conflict edges with existing
     group members
   - If all existing groups conflict, create a new group
3. Mark each group as `parallel: true` if it contains more than one issue,
   `parallel: false` if it contains exactly one issue

### Step 5 — Topological Group Ordering

1. Build a group-level DAG: group A has a directed edge to group B if any issue in A
   is a `depends_on` target of any issue in B
2. Topologically sort the group DAG using Kahn's algorithm (REQ-MAP-004)
3. Assign group numbers 1, 2, 3, ... in topological order
4. Guard: if the group DAG has a cycle (should not happen given per-issue acyclicity),
   flatten all issues into sequential single-issue groups and emit a warning in the
   report

### Step 6 — Compute Merge Order

1. Flatten groups in topological order
2. Within each parallel group, order issues by: smallest `affected_files` count first
   (merge simpler changes first to reduce conflict surface for later merges)
3. The `merge_order` list in the output is the flattened sequence of issue numbers in
   this order

### Step 7 — Write Output

Compute timestamp `{YYYY-MM-DD_HHMMSS}` (current local time, second precision).

Create the output directory before writing:
```bash
mkdir -p "$(pwd)/.autoskillit/temp/build-execution-map"
```
If directory creation fails (permission error, missing parent), abort with an explicit
error message identifying the failed path — do not silently proceed.

Write two files to `{{AUTOSKILLIT_TEMP}}/build-execution-map/` (relative to the current
working directory):

1. **`execution_map_{YYYY-MM-DD_HHMMSS}.json`** — full structured artifact (schema below)
2. **`execution_map_report_{YYYY-MM-DD_HHMMSS}.md`** — human-readable summary including:
   - Overlap matrix table (pair | shared files | conflict: yes/no)
   - Group assignments table (group | parallel | issues)
   - Merge order list
   - Any warnings (cycle detection, single-issue shortcut, etc.)

Emit structured output tokens as the last lines of text output:
```
execution_map = {absolute_path_to_json}
execution_map_report = {absolute_path_to_report}
group_count = {int}
total_issues = {int}
```

## Output JSON Schema

```json
{
  "generated_at": "ISO-8601",
  "base_ref": "main",
  "total_issues": 5,
  "group_count": 3,
  "groups": [
    {
      "group": 1,
      "parallel": true,
      "issues": [
        {
          "number": 101,
          "title": "...",
          "recipe": "implementation",
          "affected_files": ["src/foo.py", "tests/test_foo.py"],
          "depends_on": []
        },
        {
          "number": 103,
          "title": "...",
          "recipe": "implementation",
          "affected_files": ["src/bar.py"],
          "depends_on": []
        }
      ]
    },
    {
      "group": 2,
      "parallel": false,
      "issues": [
        {
          "number": 102,
          "title": "...",
          "recipe": "remediation",
          "affected_files": ["src/foo.py", "src/baz.py"],
          "depends_on": [101]
        }
      ]
    }
  ],
  "merge_order": [101, 103, 102],
  "overlap_matrix": [
    {"pair": [101, 102], "shared_files": ["src/foo.py"]},
    {"pair": [101, 103], "shared_files": []}
  ]
}
```
