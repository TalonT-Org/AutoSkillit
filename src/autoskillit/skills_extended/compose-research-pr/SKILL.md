---
name: compose-research-pr
categories: [research]
description: >
  Reads a PR prep file and validated experiment diagrams, composes the PR body,
  and creates the GitHub PR. Part 3 of 3 in the decomposed research-PR flow.
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo '[SKILL: compose-research-pr] Composing and opening research PR...'"
          once: true
---

# Compose Research PR

Reads the PR prep file produced by `prepare-research-pr`, validates experiment design
diagrams, composes a structured PR body, and creates the GitHub PR using `gh pr create`.
Does NOT invoke lens skills or other sub-skills.

## Arguments

`/autoskillit:compose-research-pr {prep_path} {all_diagram_paths} {worktree_path} {base_branch} [closing_issue]`

- **prep_path** — Absolute path to the PR prep file from `prepare-research-pr`
- **all_diagram_paths** — Quoted comma-separated absolute paths to diagram `.md` files
  (may be empty string if no lenses succeeded)
- **worktree_path** — Worktree root directory (derives `feature_branch`)
- **base_branch** — PR target branch
- **closing_issue** (optional) — GitHub issue number for auto-close on merge

## Critical Constraints

**NEVER:**
- Invoke any sub-skills or slash commands during execution
- Auto-merge or approve the PR — research PRs are for human review only
- Fail the pipeline when `gh` is not accessible — emit `pr_url = ` (empty string) and return
- Create files outside `{{AUTOSKILLIT_TEMP}}/compose-research-pr/` (relative to the current working directory)
- Invent mermaid classDef colors — when embedding validated diagrams, include them verbatim.
  Using ONLY classDef styles from the mermaid skill when composing the PR body.
- Run subagents in the background (`run_in_background: true` is prohibited)

**ALWAYS:**
- Check `gh auth status` before attempting GitHub operations
- Emit `pr_url` token as your final output (even if empty)
- Use Agent subagents to read the prep file

## Diagram Validation Keywords

A diagram passes validation if its content contains 2 or more of:
`treatment`, `outcome`, `hypothesis`, `H0`, `H1`, `IV`, `DV`, `causal`, `confound`,
`mechanism`, `effect`, `comparison`, `baseline`, `threshold`

---

## Workflow

### Step 0: Parse arguments and setup

Parse positional args:
- arg[1] = prep_path
- arg[2] = all_diagram_paths (comma-separated, may be empty)
- arg[3] = worktree_path
- arg[4] = base_branch
- arg[5] = closing_issue (optional)

Derive `feature_branch`:

    FEATURE_BRANCH=$(git -C "{worktree_path}" rev-parse --abbrev-ref HEAD)

Create temp directory:

    mkdir -p {{AUTOSKILLIT_TEMP}}/compose-research-pr/

Generate a timestamp `ts` for unique file naming.

### Step 1: Read prep file via Agent subagent

Spawn an **Explore** subagent to read `{prep_path}` and extract:
- `report_path` (from Metadata section)
- `experiment_plan_path` (from Metadata section)
- `feature_branch` (from Metadata section)
- `base_branch` (from Metadata section)
- `experiment_type`
- `status_badge`
- `recommendation` (Recommendation section)
- `results_summary` (Results Summary section)
- Hypothesis table (H0, H1)
- Metrics table
- `methodology` (Methodology section)
- Report title (from `# PR Prep: {title}` heading)
- `selected_lenses` (Selected Lenses section)

### Step 2: Read and validate diagrams

Split `all_diagram_paths` by comma; trim whitespace from each path.
For each path:
1. Skip if the path is empty or the file does not exist
2. Read the file content
3. Count how many validation keywords appear: `treatment`, `outcome`, `hypothesis`,
   `H0`, `H1`, `IV`, `DV`, `causal`, `confound`, `mechanism`, `effect`, `comparison`,
   `baseline`, `threshold`
4. If count >= 2, add the mermaid block(s) from the file to `validated_diagrams`
5. Otherwise, discard silently

If `all_diagram_paths` is empty or all diagrams fail validation, set `validated_diagrams = []`
and continue (the Experiment Design section will be omitted).

### Step 3: Compose PR body

Write to `{{AUTOSKILLIT_TEMP}}/compose-research-pr/pr_body_{ts}.md`:

```markdown
## Recommendation

{recommendation}

## Experiment Design

{if validated_diagrams non-empty: include each mermaid block with a caption; omit this section otherwise}

| Hypothesis | |
|---|---|
| H0 | {H0} |
| H1 | {H1} |

| Metric | Unit | Threshold |
|--------|------|-----------|
{metrics rows}

## Key Results

{status_badge}

{results_summary}

## Methodology

{methodology}

## What We Learned

{from results_summary or prep file if a "What We Learned" section exists}

## Full Report & Artifacts

- Report: `{report_path relative to repo root}`
- Experiment plan: `{experiment_plan_path relative to repo root}`

{if closing_issue is set}
Closes #{closing_issue}
{/if}

🤖 Generated with [Claude Code](https://claude.com/claude-code) via AutoSkillit
```

Store the body file path as `pr_body_path`.

### Step 4: Check GitHub availability

```bash
gh auth status 2>/dev/null
```

If **not available or not authenticated**: emit `pr_url = ` (empty) then stop.

### Step 5: Create PR

```bash
gh pr create \
  --base "{base_branch}" \
  --head "{feature_branch}" \
  --title "Research: {title}" \
  --body-file "{pr_body_path}"
```

Capture the PR URL from the output.

---

## Output

Emit these tokens as **literal plain text** (no markdown formatting on the token names)
as your final output:

```
pr_url = https://github.com/owner/repo/pull/N
```

Where `pr_url` is the absolute GitHub PR URL, or an empty string when GitHub is not accessible:

```
pr_url = 
```
