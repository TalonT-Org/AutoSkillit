---
name: open-pr
description: Create a GitHub PR for a completed implementation, with auto-selected arch-lens
  diagrams embedded in the PR body. Use after pipeline implementation when open_pr mode is
  enabled.
---

# Open PR

Read a completed plan, analyze changed files, generate architecture diagrams using the most
relevant arch-lens lenses, and open a GitHub Pull Request.

## Arguments

`/autoskillit:open-pr {plan_path} {feature_branch} {base_branch}`

- **plan_path** — Absolute path to the implementation plan markdown file
- **feature_branch** — Branch containing all merged implementation changes
- **base_branch** — Branch to open the PR against (e.g., "main")

## When to Use

- Called by `implementation-pipeline` when `open_pr=true`, after all groups/parts are merged
- Can be invoked standalone after any implementation that used a feature branch

## Critical Constraints

**NEVER:**
- Create files outside `temp/open-pr/` (except temp files used for `gh pr create --body-file`)
- Fail the pipeline if `gh` is not available or not authenticated — output `pr_url=` (empty) and exit successfully
- Modify any source code

**ALWAYS:**
- Check `gh auth status` before attempting GitHub operations
- ALWAYS assume the feature branch is already on the remote (the recipe pushes before invoking this skill)
- Output `pr_url=<url>` on the last output line (empty string if GitHub unavailable)
- Select 2–3 arch-lens lenses, no more

## Workflow

**Precondition:** The feature branch is already published to the remote by the `push_to_remote` recipe step that precedes this skill invocation. Do NOT push the branch yourself.

### Step 1: Parse Arguments

Parse three positional arguments: `plan_path`, `feature_branch`, `base_branch`.

### Step 2: Extract PR Title from Plan

Read the plan file at `{plan_path}`. Extract the title from the first `# ` heading line.
Strip the `# ` prefix. Use as `{task_title}`.

### Step 3: Get Changed Files

Run:
```bash
git diff --name-only {base_branch}..{feature_branch}
```

Collect the list of changed file paths. If the command fails or returns empty, proceed with
an empty file list (the PR body will note that no diff was available).

### Step 4: Select Arch-Lens Lenses

Spawn a subagent (Task tool, model: haiku) with the list of changed file paths and the
following lens menu:

```
c4-container, concurrency, data-lineage, deployment, development,
error-resilience, module-dependency, operational, process-flow,
repository-access, scenarios, security, state-lifecycle
```

Instruct the subagent to return exactly 2–3 lens names most relevant to the changed paths.
Selection criteria:
- `module-dependency` → changes span multiple packages or add new dependencies
- `process-flow` → changes affect workflow routing, state transitions, or control flow
- `development` → changes affect tests, build config, or quality gates
- `operational` → changes affect CLI, config, or observability
- `c4-container` → changes add new services, tools, or integrations
- `security` → changes affect trust boundaries or validation layers
- `repository-access` → changes affect data access or repository patterns
- `state-lifecycle` → changes affect field contracts or resume safety

### Step 5: Generate Arch-Lens Diagrams

For each selected lens (e.g., `module-dependency`), load the corresponding skill:

```python
# Use the Skill tool to load each arch-lens skill
/arch-lens-module-dependency
/arch-lens-process-flow
# etc.
```

The arch-lens skills write their output to `temp/arch-lens-{lens-name}/`. After each skill
runs, read the generated markdown file and extract the mermaid code block(s).

### Step 6: Compose PR Body

Write the PR body to `temp/open-pr/pr_body_{timestamp}.md`:

```markdown
## Summary

{First paragraph of the plan's ## Summary section, or first 5 lines after the heading}

## Architecture Impact

{For each lens diagram: embed the mermaid block with a heading for the lens name}

### {Lens Name} Diagram

` ` `mermaid
{diagram content}
` ` `

## Implementation Plan

Plan file: `{plan_path}`

🤖 Generated with [Claude Code](https://claude.com/claude-code) via AutoSkillit
```

### Step 7: Check GitHub Availability

Run `gh auth status 2>/dev/null`. If exit code is non-zero:
- Log "GitHub CLI not available or not authenticated — skipping PR creation"
- Output: `pr_url=`
- Exit successfully

### Step 8: Create Pull Request

```bash
gh pr create \
  --base {base_branch} \
  --head {feature_branch} \
  --title "{task_title}" \
  --body-file temp/open-pr/pr_body_{timestamp}.md
```

Capture the PR URL from stdout.

Output: `pr_url={url}`

## Output

- Always: `pr_url=<url>` (empty string when GitHub unavailable)
- PR body written to: `temp/open-pr/pr_body_{timestamp}.md`
