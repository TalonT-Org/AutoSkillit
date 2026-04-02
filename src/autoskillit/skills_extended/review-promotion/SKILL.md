---
name: review-promotion
categories: [github]
description: >
  Reviewer-facing deep analysis of an integration-to-main promotion. Performs domain
  risk scoring, breaking change audit, regression risk assessment, test coverage delta,
  and cross-domain dependency analysis. Use when you want a reviewer's guide before
  approving a promotion PR.
---

# Review Promotion

Perform deep reviewer-facing analysis of an integration-to-main promotion. This skill
partitions all changed files by domain, runs parallel domain risk analysis, assesses
test coverage and breaking changes, synthesizes a reviewer verdict, and optionally
posts the review report as a PR comment.

## Arguments

```
/autoskillit:review-promotion [integration_branch] [base_branch] [--post-to-pr]
```

- `integration_branch` (optional) — source branch to analyze. Defaults to `integration`.
- `base_branch` (optional) — target branch. Defaults to `main`.
- `--post-to-pr` — if present, post the review report as a comment on the open promotion PR.

## When to Use

- Before approving a promotion PR
- When you need a structured risk assessment across all changed domains
- When you want an automated reviewer's guide with a go/no-go verdict

## Critical Constraints

**NEVER:**
- Create files outside `.autoskillit/temp/review-promotion/`
- Modify any source code — this skill is read-only analysis
- Use `gh pr comment --body` inline — always use `--body-file`
- Fail silently if `gh` is unavailable when `--post-to-pr` — output `verdict = review_ready` and exit 0

**ALWAYS:**
- Output `report_path = <absolute path>` as a structured token (absolute path, prepend CWD)
- Output `verdict = <value>` as a structured token
- Include Subagent Autonomy Grant in every Task tool prompt
- Grant every subagent explicit permission to spawn their own sub-subagents

## Subagent Autonomy Grant

Every subagent spawned by this skill receives this standing instruction:

> You may spawn additional subagents (Task tool, model: sonnet) at your discretion
> to parallelize your research, fill gaps you discover during analysis, or decompose
> large tasks into focused sub-investigations. You do not need permission — use your
> judgment about when deeper exploration would improve the quality of your findings.

Include this grant verbatim in every Task tool prompt throughout this skill.

## Workflow

### Phase 0: Setup

#### Step 0.1: Parse Arguments

Parse optional positional arguments and flags:
- `integration_branch` — default `"integration"` if absent or empty
- `base_branch` — default `"main"` if absent or empty
- `post_to_pr` — `true` if `--post-to-pr` present in ARGUMENTS

#### Step 0.2: Compute Divergence Point

```bash
git merge-base {base_branch} {integration_branch}
git diff --name-only {base_branch}..{integration_branch}
git diff --name-only --diff-filter=A {base_branch}..{integration_branch}
git diff --name-only --diff-filter=M {base_branch}..{integration_branch}
```

Store as `merge_base_sha`, `changed_files`, `new_files` (added files), and
`modified_files` (modified files).

If the `git merge-base` or `git diff` command exits non-zero (e.g., unknown branch name),
emit a clear error and exit 1:

```
Error: could not compute divergence point between '{base_branch}' and '{integration_branch}'.
Check that both branches exist locally or are fetchable.
```

If `changed_files` is empty after a successful `git diff`, emit:

```
Error: no changed files found between '{base_branch}' and '{integration_branch}'.
Verify the branches are not identical and that the correct branch names were supplied.
```

Then exit 1.

#### Step 0.3: Find PR (only if --post-to-pr)

```bash
gh pr list --base {base_branch} --head {integration_branch} --state open --json number,url --limit 1
```

Store `pr_number` and `pr_url`. If none found, warn and continue — the review report will
still be written.

### Phase 1: Domain Analysis

#### Step 1.1: Partition Files by Domain

```bash
python3 -c "
from autoskillit.execution.pr_analysis import partition_files_by_domain
import json, sys
files = json.loads(sys.argv[1])
result = partition_files_by_domain(files)
print(json.dumps(result))
" '{changed_files_as_json_array}'
```

Store as `domain_partitions`. Skip if `changed_files` is empty.

#### Step 1.2: Fetch Domain Diffs (parallel)

For each domain `D` in `domain_partitions` with a non-empty file list, run in parallel:

```bash
git diff {base_branch}..{integration_branch} -- {space-separated files in domain D}
```

Truncate diffs exceeding 12,000 characters. Drop domains with empty diffs.

#### Step 1.3: Fetch Domain Commits (parallel)

For each domain in `domain_diffs`, run in parallel:

```bash
git log {base_branch}..{integration_branch} --oneline -- {space-separated files in domain D}
```

#### Step 1.4: Identify PRs per Domain

For each domain, cross-reference the PR list. For each PR, fetch its files if needed:

```bash
gh pr view {number} --json files -q '.files[].path' 2>/dev/null
```

Store as `domain_pr_numbers`.

#### Step 1.5: Parallel Domain Analysis Subagents

For each domain `D` in `domain_diffs`, spawn a Task subagent (model: sonnet) in a
single parallel message. **Include the Subagent Autonomy Grant in each prompt.**

Each subagent receives:
- Domain name and file list
- Diff content (truncated to 12k chars)
- PR numbers and titles touching this domain
- Commit one-liners for the domain

Each subagent returns ONLY a JSON object:

```json
{
  "domain": "Server/MCP Tools",
  "summary": "3-5 sentence description of what changed and why it matters",
  "key_changes": ["concise change 1", "concise change 2"],
  "breaking_changes": ["description of breaking change, or empty array"],
  "risk_score": "low|medium|high",
  "risk_rationale": "Why this risk level — what could go wrong",
  "review_guidance": "What a reviewer should focus on when reviewing this domain",
  "pr_numbers": [491, 493],
  "commit_count": 5
}
```

#### Step 1.6: Cross-Domain Dependency Analysis

Spawn one Task subagent (model: sonnet) with ALL domain summaries from Step 1.5.
**Include the Subagent Autonomy Grant.**

Analyze cross-domain dependencies:
- Do recipe schema changes require corresponding server tool updates?
- Do core type changes propagate correctly to all consumers?
- Are test changes aligned with source changes in the same domain?
- Do skill changes reflect new tools/features added in other domains?

Return JSON:
```json
{
  "cross_domain_risks": ["Recipe schema added field X but server tools don't validate it"],
  "alignment_notes": ["Tests cover all new server tools"],
  "integration_confidence": "high|medium|low"
}
```

### Phase 2: Quality Assessment (parallel subagents)

Spawn three parallel Task subagents (model: sonnet). **Include the Subagent Autonomy
Grant in each prompt.**

#### Subagent 2A: Test Coverage Delta

Analyze:
1. Count test files added, modified, and deleted
2. For each new source file in `new_files`, check if a corresponding test file exists
   in `new_files` or `modified_files` (using the project's `tests/` mirror convention)
3. Identify source files with significant changes but no test coverage
4. Compute a test-to-source ratio for new files

Return JSON:
```json
{
  "test_files_added": 5,
  "test_files_modified": 12,
  "test_files_deleted": 0,
  "source_files_without_tests": ["src/autoskillit/new_module.py"],
  "test_ratio": "17 test files for 23 source files",
  "coverage_assessment": "Good — most new modules have corresponding tests"
}
```

#### Subagent 2B: Breaking Change Audit

Receive the full diff content for each domain and the PR list.

Scan for:
1. Removed public functions or classes (check `git diff --diff-filter=M` for deleted
   `def ` and `class ` lines in non-test files)
2. Changed function signatures (parameter additions/removals/renames)
3. Modified Protocol or ABC definitions (interface contracts)
4. Changed config keys in `defaults.yaml`
5. Removed or renamed MCP tools
6. Changed recipe schema fields
7. Modified hook registrations

For each finding, assess severity and affected downstream consumers.

Return JSON:
```json
{
  "breaking_changes": [
    {
      "description": "Removed function X from module Y",
      "file": "src/autoskillit/core/types.py",
      "severity": "high|medium|low",
      "affected_domains": ["Server/MCP Tools", "Pipeline/Execution"],
      "pr_number": 485
    }
  ],
  "total": 2,
  "assessment": "Two medium-severity signature changes, both internal"
}
```

#### Subagent 2C: Regression Risk Assessment

Receive the domain summaries from Phase 1.

Analyze:
1. **Conflict hotspots** — files modified by multiple PRs (cross-reference PR file lists)
2. **Rectify chains** — commits that fix issues introduced by earlier commits in this
   same batch (pattern: PR #N introduces something, PR #M rectifies it)
3. **High-risk domains** — domains scored "high" risk in Step 1.5
4. **Churn indicators** — files with unusually high insertion+deletion counts relative
   to their size

Return JSON:
```json
{
  "hotspot_files": [{"file": "src/autoskillit/server/tools_execution.py", "touched_by_prs": [491, 493, 495]}],
  "rectify_chains": [{"original_pr": 465, "rectify_pr": 484, "description": "Structured output fix then hardening"}],
  "high_risk_domains": ["Pipeline/Execution"],
  "churn_indicators": [],
  "overall_risk": "medium",
  "risk_narrative": "Most risk concentrated in pipeline execution changes with multiple overlapping rectify commits"
}
```

### Phase 3: Review Summary Synthesis

Spawn one Task subagent (model: sonnet) with ALL results from Phases 1–2.
**Include the Subagent Autonomy Grant.**

The subagent synthesizes a reviewer-focused verdict based on:
- Domain risk scores from Phase 1
- Quality assessment results from Phase 2

Verdict rules:
- `blocking_issues` — any breaking_change with `severity: high`, or `overall_risk == "high"`
- `needs_attention` — any domain with `risk_score: medium`, or `total` breaking changes > 0
- `review_ready` — all domains low risk, no breaking changes, regression risk low

Return JSON:
```json
{
  "verdict": "review_ready|needs_attention|blocking_issues",
  "verdict_rationale": "1-2 sentence explanation",
  "priority_review_areas": [
    {"area": "Domain name", "risk": "high|medium", "focus": "What reviewer should check"}
  ],
  "review_checklist": [
    "Verify X before approving",
    "Confirm breaking change Y is intentional"
  ],
  "blocking_items": ["Must-fix before merge, if any"]
}
```

### Phase 4: Write Review Report

```bash
mkdir -p .autoskillit/temp/review-promotion
```

Write to `.autoskillit/temp/review-promotion/review_report_{YYYY-MM-DD_HHMMSS}.md`
(relative to the current working directory):

```markdown
# Promotion Review: {integration_branch} → {base_branch}

## Verdict: {verdict}

{verdict_rationale}

## Priority Review Areas

{For each item in priority_review_areas:}
### {area} (Risk: {risk})
**Focus:** {focus}

## Review Checklist

{For each item in review_checklist:}
- [ ] {item}

{If blocking_items is non-empty:}
## Blocking Items

{For each item in blocking_items:}
- **BLOCKING:** {item}

## Domain Analysis

{For each entry in domain_summaries ordered by risk_score desc:}
### {domain} (Risk: {risk_score})

{summary}

**Review focus:** {review_guidance}

**Key changes:**
{key_changes bullet list}

{If breaking_changes non-empty:}
**Breaking changes:**
{breaking_changes bullet list}

**Contributing PRs:** {comma-separated links}

{If cross_domain_risks non-empty:}
### Cross-Domain Dependencies

{For each risk:}
- {risk}

**Integration confidence:** {integration_confidence}

## Quality Assessment

### Test Coverage
{coverage_assessment}

{If source_files_without_tests non-empty:}
**Source files without test coverage:**
{bullet list}

### Breaking Changes
{If total > 0:}
| Description | File | Severity | Affected Domains |
|---|---|---|---|
{rows}

{If total == 0:}
No breaking changes detected.

### Regression Risk

**Overall:** {overall_risk}

{risk_narrative}

{If hotspot_files non-empty:}
**Conflict hotspots:**
{bullet list}

{If rectify_chains non-empty:}
**Rectify chains:**
{bullet list}

---
<sub>Generated with Claude Code via AutoSkillit | review-promotion</sub>
```

### Phase 5: Post to PR (only if --post-to-pr AND pr_number found)

```bash
gh pr comment {pr_number} --body-file .autoskillit/temp/review-promotion/review_report_{timestamp}.md
```

Continue if this fails — graceful degradation. Log the failure.

### Output

Always emit these structured output tokens as the final lines:

```
report_path = {absolute path to .autoskillit/temp/review-promotion/review_report_{timestamp}.md}
verdict = {review_ready|needs_attention|blocking_issues}
```
