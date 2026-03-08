---
name: create-review-pr
description: >
  Create an integration PR for the pr-merge-pipeline. Reads pr_order_file JSON, generates
  a rich PR body with per-PR details, arch-lens diagrams, and carried-forward Closes #N
  references. Closes all collapsed PRs with a comment after creation. Use inside the
  pr-merge-pipeline after all PRs have been merged into the integration branch.
---

# Create Review PR

Read `pr_order_file` JSON produced by `analyze-prs`, generate a rich PR description
(per-PR details, complexity tags, merge outcomes, optional audit verdict), generate 1–3
arch-lens diagrams, carry forward `Closes #N`/`Fixes #N` references from original PR
bodies so linked issues auto-close on merge, create the integration PR via
`gh pr create --body-file`, close each collapsed PR with a comment referencing the new
PR, and output `pr_url=<url>`.

## Arguments

```
/autoskillit:create-review-pr {integration_branch} {base_branch} {pr_order_file} [audit_verdict]
```

- `integration_branch` — integration branch name (e.g. `integration/pr-merge-20250228-143052`)
- `base_branch` — PR target branch (e.g. `main`)
- `pr_order_file` — absolute path to JSON produced by `analyze-prs`
- `audit_verdict` (optional) — `GO`, `NO GO`, or empty string when audit was skipped

## When to Use

- Called by `pr-merge-pipeline` after all PRs have been merged into the integration branch
- Invoked via `run_skill` as the final step before CI watch

## Critical Constraints

**NEVER:**
- Create files outside `temp/create-review-pr/` (except the temp body file for `gh pr create --body-file`)
- Modify any source code
- Fail the pipeline if `gh` is unavailable or not authenticated — output `pr_url=` (empty) and exit successfully
- Close original PRs before the integration PR is successfully created

**ALWAYS:**
- Check `gh auth status` before attempting any GitHub operations
- Output `pr_url=<url>` on the last output line (empty string when GitHub unavailable)
- Carry forward ALL `Closes #N` and `Fixes #N` lines found in original PR bodies
- Use `gh pr create --body-file` (never inline body via `--body`)

## Workflow

### Step 1: Parse Arguments

Parse four positional args: `integration_branch`, `base_branch`, `pr_order_file`,
`audit_verdict` (last one may be absent or empty string).

### Step 2: Read pr_order_file

Read the JSON file. Extract: `prs` array (each: `number`, `title`, `branch`,
`complexity`, `additions`, `deletions`, `overlap_with_pr_numbers`). Also read
`base_branch` from JSON as confirmation. Store the PR list as `pr_list`.

### Step 3: Fetch Closes/Fixes References from Original PR Bodies

For each PR in `pr_list`:

```bash
gh pr view {number} --json body -q .body 2>/dev/null
```

Extract every line matching `(Closes|Fixes|Resolves)\s+#\d+` (case-insensitive).
Deduplicate across all PRs. Store as `closing_refs` (list of strings like `Closes #42`).
Skip gracefully if `gh` is unavailable — `closing_refs` remains empty.

### Step 4: Get Changed Files

```bash
git diff --name-only {base_branch}..{integration_branch}
git diff --diff-filter=A --name-only {base_branch}..{integration_branch}
git diff --diff-filter=M --name-only {base_branch}..{integration_branch}
```

Store as `changed_files`, `new_files`, `modified_files`.

### Step 5: Select Arch-Lens Lenses

Spawn a subagent (Task tool, model: sonnet) with `changed_files` and the same lens
menu as `open-pr`. Instruct it to return 1–3 lens names using the same `development`
lens guard.

### Step 6: Generate Arch-Lens Diagrams

For each selected lens, follow this exact sequence:

**1. Write the PR context to a file using the Write tool:**

- **Path:** `temp/pr-arch-lens-context.md`
- **Content:** The following PR context block, with placeholders filled in:

```markdown
# PR Context — Changed Files

This diagram is for a Pull Request. Focus the diagram on the areas of the codebase affected by these changes. Do not create a generic whole-project diagram.

## New files (use ★ prefix on these nodes):
{list of new_files from Step 4, or "None"}

## Modified files (use ● prefix on these nodes):
{list of modified_files from Step 4, or "None"}

## Instructions:
- Focus exploration and the diagram on the architectural areas these files belong to
- Use `★` prefix on nodes representing new files/components
- Use `●` prefix on nodes representing modified files/components
- Leave unchanged components unmarked (include them only if needed for context/connectivity)
- The diagram should help PR reviewers understand the architectural impact of these specific changes
```

**2. Immediately call the Skill tool to load the arch-lens skill** (e.g., `/arch-lens-module-dependency`).
The loaded skill will read `temp/pr-arch-lens-context.md` for PR context.

**3. Follow the loaded skill's instructions** to explore the codebase and generate the diagram.

The arch-lens skills write their output to `temp/arch-lens-{lens-name}/`. After each skill
runs, read the generated markdown file and extract the mermaid code block(s).

After extracting the mermaid block, inspect its content for `★` or `●` characters:
- If the block contains at least one `★` or `●` → add it to `validated_diagrams`.
- If the block contains neither → discard this diagram; do not add it to the list.

### Step 7: Compose PR Body

Write to `temp/create-review-pr/pr_body_{timestamp}.md`.

```markdown
## Integration Summary

Collapsed {N} PRs into `{integration_branch}` targeting `{base_branch}`.

## Merged PRs

| # | Title | Complexity | Additions | Deletions | Overlaps |
|---|-------|-----------|-----------|-----------|---------|
| #{number} | {title} | {complexity} | +{additions} | -{deletions} | {overlap_with_pr_numbers or "—"} |
...

{If audit_verdict is non-empty:}
## Audit

**Verdict:** {audit_verdict}

{If validated_diagrams non-empty:}
## Architecture Impact

{For each validated diagram:}
### {Lens Name} Diagram

```mermaid
{diagram content}
```

{For each item in closing_refs:}
{Closes #N}

🤖 Generated with [Claude Code](https://claude.com/claude-code) via AutoSkillit
```

### Step 8: Check GitHub Availability

```bash
gh auth status 2>/dev/null
```

If exit code non-zero: output `pr_url=` and exit successfully.

### Step 9: Create Integration PR

```bash
gh pr create \
  --base {base_branch} \
  --head {integration_branch} \
  --title "Integration: collapsed PRs #{numbers} into {base_branch}" \
  --body-file temp/create-review-pr/pr_body_{timestamp}.md
```

`{numbers}` = comma-separated PR numbers (e.g., `#42, #47, #51`).
Capture the new PR URL as `new_pr_url`. Extract the PR number from the URL as
`new_pr_number`.

### Step 10: Close Original PRs

For each PR in `pr_list`:

```bash
gh pr close {number} --comment "Collapsed into integration PR #{new_pr_number} ({new_pr_url})"
```

Continue even if individual close operations fail (log warning, do not exit).

### Output

```
pr_url={new_pr_url}
```
