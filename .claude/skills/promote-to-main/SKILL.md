---
name: promote-to-main
description: >
  Promote integration to main with comprehensive analysis and PR creation. Use when
  user says "promote to main", "open promotion PR", "integration to main", or
  "create release PR to main". Runs pre-flight checks, multi-dimensional change
  analysis, quality assessment, and creates a rich PR with release notes.
---

# Promote to Main

Orchestrate the full integration-to-main promotion workflow. This skill discovers
everything that changed on integration since it diverged from main, runs pre-flight
quality checks, performs deep parallel analysis across multiple dimensions, generates
structured release notes, and creates a comprehensive promotion PR suitable for
final review before landing on main.

## Arguments

```
/promote-to-main [integration_branch] [base_branch] [--dry-run]
```

- `integration_branch` (optional) — source branch to promote. Defaults to `integration`.
- `base_branch` (optional) — target branch. Defaults to `main`.
- `--dry-run` — generate the full promotion report without creating a PR.

## When to Use

- When the integration branch is stable and ready to be promoted to main
- After feature PRs, bug fixes, and cleanup work have been collected and tested on integration
- This is the final gate before changes land on main

## Critical Constraints

**NEVER:**
- Create files outside `.autoskillit/temp/promote-to-main/`
- Modify any source code — this skill is read-only analysis + PR creation
- Fail silently if `gh` is unavailable — output `pr_url = ` (empty) and exit successfully
- Push or merge anything — this skill only creates the PR
- Skip pre-flight checks — a failing pre-flight must block PR creation
- Use the Bash tool for file reads — use Read, Grep, Glob for all codebase inspection

**ALWAYS:**
- Run ALL pre-flight checks before any analysis work
- Check `gh auth status` before any GitHub operations
- Output `pr_url = <url>` as a structured token (empty string when GitHub unavailable or dry-run)
- Output `verdict = <value>` as a structured token
- Carry forward ALL `Closes #N`, `Fixes #N`, and `Resolves #N` references from merged PR bodies
- Use `gh pr create --body-file` (never inline body via `--body`)
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
- `dry_run` — `true` if `--dry-run` present in ARGUMENTS

Validate that both branches exist locally:
```bash
git rev-parse --verify {integration_branch} 2>/dev/null
git rev-parse --verify {base_branch} 2>/dev/null
```
If either fails, try fetching:
```bash
git fetch origin {branch}:{branch} 2>/dev/null
```
If still missing, print error to stderr and exit 1.

#### Step 0.2: Compute Divergence Point

```bash
git merge-base {base_branch} {integration_branch}
```

Store as `merge_base_sha`.

Get commit count and timestamp:
```bash
git rev-list --count {merge_base_sha}..{integration_branch}
git show -s --format=%cI {merge_base_sha}
```
Store as `commit_count` and `merge_base_date`.

#### Step 0.3: Retrieve Token Summary from Session Logs

Determine the pipeline working directory and self-retrieve token telemetry from disk.
This aggregates token usage across all constituent PR sessions that ran in this pipeline
working directory.

```bash
export PIPELINE_CWD="$(pwd)"
mkdir -p .autoskillit/temp/promote-to-main
python3 - <<'EOF' > .autoskillit/temp/promote-to-main/token_summary.md 2>/dev/null || true
import sys, os
from autoskillit.pipeline.tokens import DefaultTokenLog
from autoskillit.pipeline.telemetry_fmt import TelemetryFormatter
from autoskillit.execution.session_log import resolve_log_dir

log_root = resolve_log_dir("")
tl = DefaultTokenLog()
n = tl.load_from_log_dir(log_root, cwd_filter=os.environ.get("PIPELINE_CWD", ""))
if n == 0:
    sys.exit(0)
steps = tl.get_report()
total = tl.compute_total()
print(TelemetryFormatter.format_token_table(steps, total))
EOF
```

- If `.autoskillit/temp/promote-to-main/token_summary.md` is non-empty, set `TOKEN_SUMMARY_CONTENT` to its
  contents and embed it in the PR body under `## Token Usage Summary`.
- If empty or absent (standalone invocation, no pipeline sessions in this cwd), omit the
  section — graceful degradation with no error.

### Phase 1: Pre-flight Checks (parallel, blocking)

Spawn three parallel Task subagents (model: sonnet) to validate promotion readiness.
All three must pass before analysis proceeds. If any fails, report the failure clearly
and exit 1. Do NOT create a PR when pre-flight fails.

**Include the Subagent Autonomy Grant in each prompt.**

#### Subagent 1A: CI and Branch Status

Check:
1. CI is green on the integration branch — run `gh pr checks` for any open PR from
   integration, or `gh run list --branch {integration_branch} --workflow tests.yml --limit 1 --json conclusion`
2. The integration branch is not behind base — run `git rev-list --count {integration_branch}..{base_branch}`
   to check if base has commits not in integration (if > 0, warn that a rebase may be needed)
3. No open PRs targeting integration with failing CI — `gh pr list --base {integration_branch} --state open --json number,title,statusCheckRollup`

Return JSON:
```json
{
  "ci_status": "pass|fail|unknown",
  "ci_details": "description of CI state",
  "behind_base_by": 0,
  "open_prs_with_failing_ci": [],
  "pass": true
}
```

#### Subagent 1B: Version Consistency

Check:
1. `pyproject.toml` version matches `src/autoskillit/.claude-plugin/plugin.json` version
2. `uv lock --check` passes (lockfile consistent)
3. The integration branch version is ahead of the base branch version

Return JSON:
```json
{
  "pyproject_version": "X.Y.Z",
  "plugin_version": "X.Y.Z",
  "versions_match": true,
  "lockfile_consistent": true,
  "version_ahead_of_base": true,
  "pass": true
}
```

#### Subagent 1C: Outstanding Review Items

Check:
1. No open PRs targeting integration with `CHANGES_REQUESTED` reviews —
   `gh pr list --base {integration_branch} --state open --json number,title,reviews`
2. No `in-progress` labeled issues that might indicate incomplete work —
   `gh issue list --label in-progress --state open --json number,title`

Return JSON:
```json
{
  "prs_with_changes_requested": [],
  "in_progress_issues": [],
  "pass": true
}
```

**Pre-flight gate:** If any subagent returns `"pass": false`, report all failures in a
clear summary table and exit 1 without proceeding to analysis. If `ci_status` is `unknown`,
treat as a warning (non-blocking) and note it in the report.

### Phase 2: Change Inventory (parallel subagents)

Spawn four parallel Task subagents (model: sonnet). **Include the Subagent Autonomy Grant
in each prompt.**

#### Subagent 2A: Commit Categorization

Receive the output of:
```bash
git log {merge_base_sha}..{integration_branch} --format="%H %s"
```

Categorize each commit into exactly one category based on its subject line:
- `rectify` — subject contains "Rectify:" or "fix:" or "bugfix" (case-insensitive)
- `feature` — subject contains "Implementation Plan:", "feat:", "Add ", or introduces new capability
- `infra` — subject contains CI, workflow, config, build, or infrastructure changes
- `test` — subject only touches test files (infer from "test" in subject or `tests/` paths)
- `docs` — subject contains "docs:", "README", or documentation-only changes

Extract PR numbers from patterns like `(#123)` in commit subjects.

Return JSON:
```json
{
  "categories": {
    "rectify": [{"sha": "abc123", "title": "...", "pr_number": 495}],
    "feature": [],
    "infra": [],
    "test": [],
    "docs": []
  },
  "totals": {"rectify": 14, "feature": 13, "infra": 3, "test": 1, "docs": 1},
  "category_summary": "14 fixes, 13 features, 3 infra"
}
```

#### Subagent 2B: PR Discovery and Issue Linkage

Run:
```bash
gh pr list --base {integration_branch} --state merged --limit 200 --json number,title,author,mergedAt,body,headRefName,additions,deletions,labels,url
```

Filter to PRs merged after `merge_base_date`. If empty, fall back to commit-subject
discovery:
```bash
git log {merge_base_sha}..{integration_branch} --oneline --grep="(#" --format="%s"
```

For each PR, extract `Closes|Fixes|Resolves #N` references (case-insensitive).
Deduplicate across all PRs. For each linked issue number, fetch details:
```bash
gh issue view {number} --json number,title,state,url,labels 2>/dev/null
```

Build a traceability matrix: for each issue, identify which PR(s) close it.

Return JSON:
```json
{
  "prs": [{"number": 1, "title": "...", "author": "...", "labels": [], "url": "...", "additions": 0, "deletions": 0}],
  "closing_refs": ["Closes #42", "Fixes #50"],
  "linked_issue_numbers": [42, 50],
  "issue_details": [{"number": 42, "title": "...", "state": "OPEN", "url": "...", "labels": ["recipe:implementation"]}],
  "traceability": [{"issue_number": 42, "issue_title": "...", "pr_numbers": [491], "recipe_route": "implementation"}]
}
```

#### Subagent 2C: File Lifecycle Tracking

Run:
```bash
git diff --name-only {base_branch}..{integration_branch}
git diff --diff-filter=A --name-only {base_branch}..{integration_branch}
git diff --diff-filter=M --name-only {base_branch}..{integration_branch}
git diff --diff-filter=D --name-only {base_branch}..{integration_branch}
git diff --diff-filter=R --name-only {base_branch}..{integration_branch}
git diff --stat {base_branch}..{integration_branch} | tail -1
```

Return JSON:
```json
{
  "changed_files": ["..."],
  "new_files": ["..."],
  "modified_files": ["..."],
  "deleted_files": ["..."],
  "renamed_files": ["..."],
  "diff_stat_summary": "185 files changed, 10013 insertions(+), 1164 deletions(-)"
}
```

#### Subagent 2D: Migration and Schema Detection

Scan the diff for changes that require manual attention on merge. Check:
1. Changes to `src/autoskillit/migrations/` — new migration YAML notes
2. Changes to `src/autoskillit/recipe/schema.py` — recipe schema modifications
3. Changes to `pyproject.toml` — dependency additions/removals/version bumps
4. Changes to `src/autoskillit/config/defaults.yaml` — config schema changes
5. Changes to `src/autoskillit/hooks/hooks.json` — hook registration changes
6. Changes to `src/autoskillit/.claude-plugin/plugin.json` — plugin metadata changes
7. Changes to `.github/workflows/` — CI workflow modifications

For each detected change, provide a brief description of what changed and why it
might need attention.

Return JSON:
```json
{
  "migration_changes": ["Added migration note for v0.3.2"],
  "schema_changes": ["New RecipeStep field: optional"],
  "dependency_changes": ["Added httpx>=0.27"],
  "config_changes": ["New config key: github.staged_label"],
  "hook_changes": ["New hook: pretty_output PostToolUse"],
  "ci_changes": ["Updated tests.yml matrix"],
  "attention_required": true,
  "attention_summary": "Brief description of what needs human review"
}
```

### Phase 3: Domain Analysis (parallel subagents)

#### Step 3.1: Partition Files by Domain

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

#### Step 3.2: Fetch Domain Diffs (parallel)

For each domain `D` in `domain_partitions` with a non-empty file list, run in parallel:

```bash
git diff {base_branch}..{integration_branch} -- {space-separated files in domain D}
```

Truncate diffs exceeding 12,000 characters. Drop domains with empty diffs.

#### Step 3.3: Fetch Domain Commits (parallel)

For each domain in `domain_diffs`, run in parallel:

```bash
git log {base_branch}..{integration_branch} --oneline -- {space-separated files in domain D}
```

#### Step 3.4: Identify PRs per Domain

For each domain, cross-reference the PR list from Subagent 2B. For each PR, fetch its
files if not already available:

```bash
gh pr view {number} --json files -q '.files[].path' 2>/dev/null
```

Store as `domain_pr_numbers`.

#### Step 3.5: Run Parallel Domain Analysis Subagents

For each domain `D` in `domain_diffs`, spawn a Task subagent (model: sonnet) in a
single parallel message. **Include the Subagent Autonomy Grant in each prompt.**

Each subagent receives:
- Domain name and file list
- Diff content (truncated)
- PR numbers and titles touching this domain
- Commit one-liners for the domain
- The commit categorization from Subagent 2A (to contextualize whether changes are
  fixes, features, or infrastructure)

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

#### Step 3.6: Cross-Domain Dependency Analysis (single subagent)

Spawn one Task subagent (model: sonnet) with ALL domain summaries from Step 3.5.
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

### Phase 4: Quality Assessment (parallel subagents)

Spawn three parallel Task subagents (model: sonnet). **Include the Subagent Autonomy
Grant in each prompt.**

#### Subagent 4A: Test Coverage Delta

Receive the full file lists from Subagent 2C and the domain partitions.

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

#### Subagent 4B: Breaking Change Audit

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

#### Subagent 4C: Regression Risk Assessment

Receive the commit categorization from Subagent 2A and the domain summaries from Phase 3.

Analyze:
1. **Conflict hotspots** — files modified by multiple PRs (cross-reference PR file lists)
2. **Rectify chains** — commits that fix issues introduced by earlier commits in this
   same batch (pattern: PR #N introduces something, PR #M rectifies it)
3. **High-risk domains** — domains scored "high" risk in Step 3.5
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

### Phase 5: Executive Summary and Release Notes (single subagent)

Spawn one Task subagent (model: sonnet) with ALL results from Phases 2-4.
**Include the Subagent Autonomy Grant.**

Synthesize:

1. **Executive Summary** — 3-5 sentence high-level narrative of what this promotion
   brings to main. Written for a project maintainer. Focus on themes and impact.

2. **Highlights** — Top 3-5 most significant changes.

3. **Risk Areas** — Areas requiring careful review, synthesized from domain risk scores,
   breaking change audit, and regression risk assessment.

4. **Release Notes** — Structured changelog grouped by category:

```markdown
### New Features
- Feature description (PR #N, closes #M)

### Bug Fixes
- Fix description (PR #N, closes #M)

### Infrastructure
- Change description (PR #N)

### Breaking Changes
- Change description — **Impact:** what breaks. **Migration:** what to do.

### Attention Required
- Item requiring manual review before or after merge
```

Return JSON:
```json
{
  "executive_summary": "...",
  "highlights": ["...", "..."],
  "risk_areas": ["...", "..."],
  "release_notes_md": "### New Features\n- ...\n### Bug Fixes\n- ..."
}
```

### Phase 6: Architecture Diagrams

#### Step 6.1: Select Arch-Lens Lenses

Spawn a Task subagent (model: sonnet) with the `changed_files` list and this lens menu:

```
c4-container, concurrency, data-lineage, deployment, development,
error-resilience, module-dependency, operational, process-flow,
repository-access, scenarios, security, state-lifecycle
```

Return 1-3 lens names. Apply the same selection criteria as open-pr:

**Development lens guard:** Only select `development` if at least one changed file matches:
`pyproject.toml`, `Taskfile*`, `conftest.py`, `.github/workflows/*`, `Makefile`,
`setup.cfg`, `setup.py`, `tox.ini`, `noxfile.py`, or files under `ci/`.

For a promotion PR, prefer lenses that show the broadest architectural impact:
- `module-dependency` if changes span multiple packages
- `process-flow` if workflow routing or state transitions changed
- `c4-container` if new services, tools, or integrations were added

#### Step 6.2: Generate Arch-Lens Diagrams

For each selected lens, follow this exact sequence:

**CRITICAL:** Do NOT output any prose status text between lens iterations.
After completing all sub-steps for one lens, immediately begin sub-step 1 for the
next lens.

**1. Write the PR context to a file using the Write tool:**

- **Path:** `.autoskillit/temp/promote-to-main/pr_arch_lens_context_{YYYY-MM-DD_HHMMSS}.md`
- **Content:**

```markdown
# PR Context — Integration to Main Promotion

This diagram is for a promotion PR merging the integration branch into main. Focus on the areas of the codebase affected by all accumulated changes. Do not create a generic whole-project diagram.

## New files (use star prefix on these nodes):
{list of new_files, or "None"}

## Modified files (use bullet prefix on these nodes):
{list of modified_files, or "None"}

## Deleted files:
{list of deleted_files, or "None"}

## Instructions:
- Focus exploration and the diagram on the architectural areas these files belong to
- Use star prefix on nodes representing new files/components
- Use bullet prefix on nodes representing modified files/components
- Mark deleted components with strikethrough or a X prefix
- Leave unchanged components unmarked (include only if needed for context/connectivity)
- This is a promotion PR — show the cumulative architectural impact of all changes
```

**2. Immediately call the Skill tool** to load the arch-lens skill (e.g.,
`/autoskillit:arch-lens-module-dependency`).

**3. Follow the loaded skill's instructions** to generate the diagram.

Read the output from `.autoskillit/temp/arch-lens-{lens-name}/` and extract the mermaid block(s).

Validate: if the block contains at least one star marker or bullet marker for
new/modified nodes, add to `validated_diagrams`. Otherwise discard.

### Phase 7: Compose Promotion Report

Write to `.autoskillit/temp/promote-to-main/promotion_report_{timestamp}.md`.

This is the comprehensive analysis report, always generated regardless of `dry_run`.

```markdown
# Promotion Report: {integration_branch} to {base_branch}

## Executive Summary

{executive_summary}

**Stats:** {diff_stat_summary} across {commit_count} commits from {len(prs)} PRs

## Pre-flight Status

| Check | Status | Details |
|-------|--------|---------|
| CI | PASS/WARN/FAIL | {ci_details} |
| Version | PASS/FAIL | {pyproject_version} (pyproject) = {plugin_version} (plugin) |
| Reviews | PASS/WARN | {count} PRs with outstanding reviews |
| Lock file | PASS/FAIL | uv lock consistent/inconsistent |

## Highlights

{For each item in highlights:}
- {item}

{If risk_areas is non-empty:}
## Areas Requiring Review

{For each item in risk_areas:}
- {item}

## Release Notes

{release_notes_md from Phase 5}

## Change Breakdown

| Category | Count | PRs |
|----------|-------|-----|
| Features | {N} | {comma-separated PR links} |
| Bug Fixes | {N} | {comma-separated PR links} |
| Infrastructure | {N} | {comma-separated PR links} |
| Tests | {N} | {comma-separated PR links} |
| Docs | {N} | {comma-separated PR links} |

## Merged PRs

| PR | Title | Author | Labels | Category |
|----|-------|--------|--------|----------|
{For each pr in prs:}
| [#{pr.number}]({pr.url}) | {pr.title} | @{pr.author.login} | {labels or "—"} | {category} |

## Linked Issues

{If linked_issue_numbers is non-empty:}
| Issue | Title | Status | Action | Route | Implementing PR |
|-------|-------|--------|--------|-------|-----------------|
{For each issue in issue_details:}
| [#{issue.number}]({issue.url}) | {issue.title} | {issue.state} | {"Will close on merge" if OPEN else "Already closed"} | {recipe_route from traceability or "—"} | {pr_numbers from traceability or "—"} |

{If linked_issue_numbers is empty:}
No linked issues found in PR descriptions.

{If domain_summaries is non-empty:}
## Domain Analysis

{For each entry in domain_summaries (ordered by risk_score desc, then domain name):}
### {entry.domain} (Risk: {entry.risk_score})

**Review focus:** {entry.review_guidance}

{entry.summary}

**Key changes:**
{For each item in entry.key_changes:}
- {item}

{If entry.breaking_changes is non-empty:}
**Breaking changes:**
{For each item in entry.breaking_changes:}
- {item}

**Contributing PRs:** {comma-separated [#{N}](url) for each N in entry.pr_numbers, or "—"}
**Commits:** {entry.commit_count} commit(s)

{If cross_domain_risks is non-empty:}
### Cross-Domain Dependencies

{For each risk in cross_domain_risks:}
- {risk}

**Integration confidence:** {integration_confidence}

## Quality Assessment

### Test Coverage
{test coverage assessment from Subagent 4A}

{If source_files_without_tests is non-empty:}
**Source files without test coverage:**
{For each file in source_files_without_tests:}
- {file}

### Breaking Changes
{If breaking_changes total > 0:}
| Description | File | Severity | Affected Domains | PR |
|-------------|------|----------|------------------|----|
{For each change in breaking_changes:}
| {description} | {file} | {severity} | {affected_domains} | #{pr_number} |

{If total == 0:}
No breaking changes detected.

### Regression Risk

**Overall risk:** {overall_risk}

{risk_narrative}

{If hotspot_files is non-empty:}
**Conflict hotspots:**
{For each hotspot in hotspot_files:}
- `{file}` — touched by PRs {pr_numbers}

{If rectify_chains is non-empty:}
**Rectify chains (fix-then-fix-again patterns):**
{For each chain in rectify_chains:}
- PR #{original_pr} then #{rectify_pr}: {description}

{If attention_required from Subagent 2D:}
## Attention Required

{attention_summary}

{For each category in migration/schema/dependency/config/hook/ci changes:}
{If category is non-empty:}
**{Category Name}:**
{For each item:}
- {item}

## Traceability Matrix

| Issue | Implementing PR | Domain | Category |
|-------|-----------------|--------|----------|
{For each entry in traceability:}
| #{issue_number} {issue_title} | {comma-separated PR links} | {primary domain} | {commit category} |

{If validated_diagrams is non-empty:}
## Architecture Impact

{For each validated diagram:}
### {Lens Name} Diagram

{mermaid block}

{For each item in closing_refs:}
{item}

{If TOKEN_SUMMARY_CONTENT is non-empty:}
## Token Usage Summary

{TOKEN_SUMMARY_CONTENT}

---

<sub>Generated with Claude Code via AutoSkillit</sub>
```

### Phase 8: PR Creation

If `dry_run` is true:
- Output `report_path = {absolute path to promotion_report}` and skip to Output.

#### Step 8.1: Check GitHub Availability

```bash
gh auth status 2>/dev/null
```

If exit code non-zero: output `pr_url = ` and exit successfully.

#### Step 8.2: Compose PR Body

Write the PR body to `.autoskillit/temp/promote-to-main/pr_body_{timestamp}.md`.

The PR body is a condensed version of the promotion report, optimized for GitHub rendering.
Include these sections (in order):

1. `## Promotion: {integration_branch} to {base_branch}` with executive summary and stats
2. `### Highlights` from Phase 5
3. `### Areas Requiring Review` (if non-empty) from Phase 5
4. `## Release Notes` from Phase 5
5. `## Merged PRs` table (PR, Title, Author, Labels)
6. `## Linked Issues` table (Issue, Title, Status, Action)
7. `## Domain Analysis` per-domain sections with risk scores and review guidance
8. `## Architecture Impact` with validated mermaid diagrams
9. Closing references (`Closes #N` lines, one per line)
10. `## Token Usage Summary` (if available)
11. Footer

#### Step 8.3: Create Promotion PR

Construct the PR title using actual branch names from arguments:

`Promote {integration_branch} to {base_branch} ({len(prs)} PRs, {len(linked_issue_numbers)} issues, {category_summary})`

where `category_summary` is from Subagent 2A (e.g., "14 fixes, 13 features").

```bash
gh pr create \
  --base {base_branch} \
  --head {integration_branch} \
  --title "{pr_title}" \
  --body-file .autoskillit/temp/promote-to-main/pr_body_{timestamp}.md
```

Capture the PR URL as `pr_url`.

#### Step 8.4: Add Labels (optional)

```bash
gh pr edit {pr_url} --add-label "promotion" 2>/dev/null
```

Continue if this fails.

### Output

Always emit these structured output tokens as the final lines:

```
report_path = {absolute path to .autoskillit/temp/promote-to-main/promotion_report_{timestamp}.md}
pr_url = {pr_url, empty if dry-run or gh unavailable}
verdict = {created|dry_run|preflight_failed}
category_summary = {e.g., "14 fixes, 13 features, 3 infra"}
```
