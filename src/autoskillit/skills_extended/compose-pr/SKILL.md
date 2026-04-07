---
name: compose-pr
categories: [github]
description: >
  Reads the PR prep file and validated arch-lens diagrams, composes the PR body,
  and creates the GitHub PR. Does NOT invoke sub-skills. Part 3 of 3 in the
  decomposed PR flow.
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo '[SKILL: compose-pr] Composing and opening PR...'"
          once: true
---

# Compose PR

Read the PR prep file from `prepare-pr`, validate arch-lens diagrams, compose the PR body,
and create the GitHub PR via `gh pr create`.

This skill Does NOT invoke any sub-skills or slash commands. It is Part 3 of 3 in the
decomposed PR flow (prepare → run_arch_lenses → compose).

## Arguments

`/autoskillit:compose-pr {prep_path} {all_diagram_paths} {work_dir} {base_branch} [closing_issue]`

- **prep_path** — Absolute path to the PR prep file from `prepare-pr`
- **all_diagram_paths** — Quoted comma-separated absolute paths to diagram `.md` files
  (may be empty string if no lenses succeeded)
- **work_dir** — Worktree root (for deriving `feature_branch` via `git rev-parse`)
- **base_branch** — PR target branch
- **closing_issue** (optional) — GitHub issue number for `Closes #N`

## Critical Constraints

**NEVER:**
- Invoke any sub-skills or slash commands
- Fail the pipeline if `gh` is unavailable — emit `pr_url = ` (empty) and exit successfully
- Create files outside `.autoskillit/temp/compose-pr/`

**ALWAYS:**
- Check `gh auth status` before attempting GitHub operations
- Emit `pr_url` token (empty string when GitHub unavailable)
- Validate diagrams with ★ or ● markers before including them
- Degrade gracefully when `all_diagram_paths` is empty or all diagrams fail validation
  (omit Architecture Impact section; do not include a placeholder)
- Handle `no diagrams` case: when `all_diagram_paths is empty` or every path fails
  marker check, set `validated_diagrams = []` and omit the Architecture Impact section

## Workflow

### Step 0: Parse Arguments and Initialize

Parse positional arguments:
- arg[1] = `prep_path`
- arg[2] = `all_diagram_paths` (may be empty string)
- arg[3] = `work_dir`
- arg[4] = `base_branch`
- arg[5] = `closing_issue` (optional — overrides value in prep file if set)

Derive `feature_branch`:
```bash
git -C {work_dir} rev-parse --abbrev-ref HEAD
```

Create temp dir:
```bash
mkdir -p .autoskillit/temp/compose-pr/
```
Generate timestamp `ts` = `$(date +%Y-%m-%d_%H%M%S)`.

### Step 1: Read PR Prep File

Read the file at `prep_path`. Extract:
- `task_title` (from `## Title` section)
- Plan summaries (from `## Plan Summary` — detect single vs multi-plan by presence of group headings)
- `requirements_section` (from `## Requirements` — empty if section is blank)
- `conflict_resolution_table` (from `## Conflict Resolution Table` — empty if section is blank)
- `new_files` (from `## Changed Files > ### New (★):`)
- `modified_files` (from `## Changed Files > ### Modified (●):`)
- `feature_branch` from `## Metadata` (use the git-derived value from Step 0 if set)
- `closing_issue` from `## Metadata` (overridden by arg[5] if arg[5] is non-empty)

### Step 2: Read and Validate Diagrams

Split `all_diagram_paths` by comma; trim whitespace from each path.

For each path:
1. Read the file
2. Extract mermaid block(s)
3. Check for `★` or `●` character in the mermaid block
   - If found → add to `validated_diagrams`
   - If not found → discard silently

If `all_diagram_paths` is empty or all diagrams fail → `validated_diagrams = []`.

### Step 3: Compose PR Body

Write PR body to `.autoskillit/temp/compose-pr/pr_body_{ts}.md`.

#### Single plan format:

```markdown
## Summary

{First paragraph of the plan's ## Summary section, or first 5 lines after the heading}

{If requirements_section is non-empty:}
## Requirements

{requirements_section}

{If conflict_resolution_table is non-empty:}
## Conflict Resolution Decisions

The following files had merge conflicts that were automatically resolved.

{conflict_resolution_table}

{## Architecture Impact — include ONLY if validated_diagrams is non-empty:}
## Architecture Impact

### {Lens Name} Diagram

```mermaid
{diagram content}
```

{If closing_issue is non-empty:}
Closes #{closing_issue}

## Implementation Plan

Plan file: `{plan_path}`

🤖 Generated with [Claude Code](https://claude.com/claude-code) via AutoSkillit
```

#### Multiple plans format:

```markdown
## Summary

{Synthesized overall summary — 2-3 sentences. Spawn a sonnet subagent if needed.}

<details>
<summary>Individual Group Plans</summary>

### Group 1: {heading from plan 1}
{Summary from plan 1}

### Group 2: {heading from plan 2}
{Summary from plan 2}

</details>

{If requirements_section is non-empty:}
## Requirements

{requirements_section}

{If conflict_resolution_table is non-empty:}
## Conflict Resolution Decisions

The following files had merge conflicts that were automatically resolved.

{conflict_resolution_table}

{## Architecture Impact — include ONLY if validated_diagrams is non-empty:}
## Architecture Impact

### {Lens Name} Diagram

```mermaid
{diagram content}
```

{If closing_issue is non-empty:}
Closes #{closing_issue}

## Implementation Plan

Plan files:
- `{plan_path_1}`
- `{plan_path_2}`

🤖 Generated with [Claude Code](https://claude.com/claude-code) via AutoSkillit
```

### Step 4: Check GitHub Availability

Run:
```bash
gh auth status 2>/dev/null
```

If exit code is non-zero:
- Log: "GitHub CLI not available or not authenticated — skipping PR creation"
- Output: `pr_url = ` (empty)
- Exit successfully

### Step 5: Create Pull Request

```bash
gh pr create \
  --base {base_branch} \
  --head {feature_branch} \
  --title "{task_title}" \
  --body-file .autoskillit/temp/compose-pr/pr_body_{ts}.md
```

Capture PR URL from stdout.

## Output

Emit these structured output tokens (literal plain text, no markdown decoration):

On success:
```
pr_url = https://github.com/owner/repo/pull/N
%%ORDER_UP%%
```

On graceful degradation (no `gh` or not authenticated):
```
pr_url = 
%%ORDER_UP%%
```
