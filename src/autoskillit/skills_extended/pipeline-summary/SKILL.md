---
name: pipeline-summary
categories: [github]
description: Create a GitHub issue and PR summarizing pipeline bugs and fixes. Use when a pipeline run completes with accumulated bug fixes on a feature branch.
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo '[SKILL: pipeline-summary] Creating pipeline run summary (issue + PR)...'"
          once: true
---

# Pipeline Summary

Create a GitHub issue documenting bugs encountered during a pipeline run
and a PR from the feature branch into the target branch.

## Arguments

`/autoskillit:pipeline-summary {bug_report_path} {feature_branch} {target_branch} {workspace} [{closing_issue}]`

- **bug_report_path** — Path to the JSON file containing bug metadata
- **feature_branch** — Name of the branch containing all accumulated fixes
- **target_branch** — Branch to create the PR against (e.g., "main")
- **workspace** — Path to the git repository workspace
- **closing_issue** — (Optional) GitHub issue number whose `## Requirements` section should be extracted and embedded in the PR body. When absent or empty, requirements extraction is skipped.

## When to Use

- End of a pipeline run with `collect_on_branch` enabled
- Any pipeline that accumulates fixes on a feature branch and needs a summary

## Critical Constraints

**NEVER:**
- Fail the pipeline if `gh` is not available or not authenticated — write a local summary instead
- Create empty issues or PRs (skip if no bugs to report)
- Modify any source code — this skill only creates GitHub artifacts and a summary file

**ALWAYS:**
- Check `gh auth status` before attempting GitHub operations
- Push the feature branch before creating the PR
- Write a local summary markdown file regardless of GitHub availability
- Output `summary_path=<path>` for capture by the orchestrator
- If GitHub operations succeed, also output `issue_url=<url>` and `pr_url=<url>`

## Workflow

### Step 1: Parse Arguments
Parse up to five positional arguments from the prompt. The fifth (`closing_issue`) is optional.

### Step 2: Read Bug Report
Read the JSON file at `{bug_report_path}`. Expected structure:
```json
[
  {
    "step": "string — pipeline step where failure occurred",
    "error": "string — error description",
    "fix": "string — what was done to fix it",
    "iteration": "number — which bugfix iteration"
  }
]
```
If the file is empty, contains `[]`, or doesn't exist, write a clean-run summary and exit successfully.

### Step 3: Write Local Summary
Write a markdown summary to `{workspace}/run-summary.md`:
- Title: "Pipeline Run Summary — {date}"
- Bug count and fix count
- Table of all bugs with step, error, fix, iteration
- Branch info: feature branch name, target branch

Output: `summary_path={workspace}/run-summary.md`

### Step 4: Check GitHub Availability
Run `gh auth status 2>/dev/null`. If exit code is non-zero or `gh` is not found:
- Log "GitHub CLI not available or not authenticated — skipping issue/PR creation"
- Exit successfully (the local summary is sufficient)

### Step 5: Push Feature Branch
```bash
cd {workspace}
REMOTE=$(git remote get-url upstream >/dev/null 2>&1 && echo upstream || echo origin)
git push -u "$REMOTE" {feature_branch}
```
If push fails (no remote, network issue), log the error and exit successfully.

### Step 5b: Fetch Requirements from Closing Issue (if closing_issue known)

- If `closing_issue` was provided as the sixth argument:
  ```bash
  gh issue view {closing_issue} --json body -q .body
  ```
  Extract the `## Requirements` section: `requirements_section` = everything from `## Requirements` to the next `## ` heading or end of body, whichever comes first.
- If gh auth is unavailable or `closing_issue` is not provided: skip gracefully — `requirements_section = ""`.

### Step 6: Create GitHub Issue
Write the issue body to a temp file, then:
```bash
TEMP_ISSUE_BODY="{{AUTOSKILLIT_TEMP}}/pipeline-summary/issue_body_$(date +%Y%m%d-%H%M%S).md"
mkdir -p "$(dirname "${TEMP_ISSUE_BODY}")"
# [write the issue body content to ${TEMP_ISSUE_BODY} here]
gh issue create \
  --title "Pipeline Run Summary — {date}: {bug_count} bug(s) fixed" \
  --body-file "${TEMP_ISSUE_BODY}" \
  --label "pipeline-summary"
```
Capture the issue URL from stdout. If the label doesn't exist, retry without `--label`.

Output: `issue_url={url}`

### Step 7: Create Pull Request
Write the PR body to a temp file (reference the issue), then:
```bash
TEMP_PR_BODY="{{AUTOSKILLIT_TEMP}}/pipeline-summary/pr_body_$(date +%Y%m%d-%H%M%S).md"
mkdir -p "$(dirname "${TEMP_PR_BODY}")"
# [write the PR body content to ${TEMP_PR_BODY} here]
gh pr create \
  --title "Pipeline fixes — {date}" \
  --body-file "${TEMP_PR_BODY}" \
  --base {target_branch} \
  --head {feature_branch}
```
The PR body (`temp_pr_body`) contains:
- `## Summary` — bug count and branch info
- `## Requirements` (if `requirements_section` is non-empty from Step 5b)
- `Closes #{closing_issue}` (if closing_issue was provided)
- Bug table from Step 3

Capture the PR URL from stdout.

Output: `pr_url={url}`

## Output
- Always: `summary_path={workspace}/run-summary.md`
- If GitHub available: `issue_url={url}` and `pr_url={url}`

