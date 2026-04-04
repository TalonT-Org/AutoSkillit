---
name: open-research-pr
categories: [research]
description: Open a GitHub PR for a completed research worktree with experiment design
  diagrams and structured PR body composition. Implements the open-pr pattern for research
  pipelines. See issue #593.
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo '[SKILL: open-research-pr] Opening research pull request...'"
          once: true
---

# Open Research PR

Read a completed experiment report and plan, synthesize a directional recommendation,
generate experiment design diagrams using exp-lens skills, and open a GitHub Pull Request
with a structured research PR body.

## Arguments

`/autoskillit:open-research-pr {report_path} {experiment_plan_path} {worktree_path} {base_branch} [closing_issue]`

- **report_path** — Absolute path to `research/{slug}/report.md`
- **experiment_plan_path** — Absolute path to the experiment plan (YAML or Markdown)
- **worktree_path** — Worktree directory; skill derives `feature_branch` via
  `git -C {worktree_path} rev-parse --abbrev-ref HEAD`
- **base_branch** — PR target branch
- **closing_issue** (optional) — GitHub issue number. When provided, inserts `Closes #{closing_issue}`
  into the PR body so GitHub auto-closes the issue on merge.

## When to Use

Called by the research recipe after `push_branch` to open the research PR. Embeds
experiment design diagrams and structures the PR body for research quality review.

## Critical Constraints

**NEVER:**
- Auto-merge or approve the PR — research PRs are for human review only
- Create files outside `.autoskillit/temp/open-research-pr/` (except temp files for `gh pr create --body-file`)
- Fail the pipeline if `gh` is not available or not authenticated — output `pr_url = ` (empty) and exit 0

**ALWAYS:**
- Check `gh auth status` before attempting GitHub operations
- Assume the feature branch is already on the remote (the recipe pushes before invoking this skill)
- Always embed at least one experiment design diagram when validated_diagrams is non-empty
- Always link to the report and experiment plan files in the PR body
- Emit `pr_url` as an absolute GitHub PR URL or empty string on graceful degradation

## Lens Selection Table

| experiment_type        | Primary Lens                   | Secondary Lens (optional)         |
|------------------------|--------------------------------|-----------------------------------|
| benchmark              | exp-lens-fair-comparison       | exp-lens-estimand-clarity         |
| configuration_study    | exp-lens-iterative-learning    | exp-lens-sensitivity-robustness   |
| causal_inference       | exp-lens-causal-assumptions    | exp-lens-estimand-clarity         |
| robustness_audit       | exp-lens-severity-testing      | exp-lens-validity-threats         |
| exploratory            | exp-lens-estimand-clarity      | exp-lens-exploratory-confirmatory |

## Experiment Status Badges

The PR body includes one of these status badges from the report:

- `CONCLUSIVE_POSITIVE` — hypothesis supported; results meet success criteria
- `CONCLUSIVE_NEGATIVE` — hypothesis refuted; results clearly below threshold
- `INCONCLUSIVE` — results ambiguous; insufficient signal or mixed evidence
- `FAILED` — experiment could not be completed; results invalid

## Workflow

### Step 1: Parse arguments and read report + plan

Parse all positional args:
- arg[1] = report_path
- arg[2] = experiment_plan_path
- arg[3] = worktree_path
- arg[4] = base_branch
- arg[5] = closing_issue (optional)

Derive `feature_branch`:

    FEATURE_BRANCH=$(git -C "{worktree_path}" rev-parse --abbrev-ref HEAD)

Create temp directory (relative to the current working directory):

    mkdir -p .autoskillit/temp/open-research-pr/

Read from `{report_path}`:
- Executive Summary section
- Conclusions section
- Recommendations section
- Results section (findings, measurements)
- Experiment status (CONCLUSIVE_POSITIVE / CONCLUSIVE_NEGATIVE / INCONCLUSIVE / FAILED)
  — look for status header or badge in the report
- Title from the first H1 heading or `title:` field

Read from `{experiment_plan_path}`:
- Hypothesis (H0 and H1 if present)
- Independent Variables table
- Dependent Variables / Metrics table
- Controlled Variables list
- Success Criteria
- Methodology section
- `experiment_type` field (from YAML frontmatter or prose header):
  benchmark / configuration_study / causal_inference / robustness_audit / exploratory


### Step 2: Synthesize recommendation

Spawn a **sonnet subagent** with the report's Conclusions, Recommendations, and experiment
status. Produce a 1–3 sentence directional recommendation suitable for the top of the PR
body (inverted pyramid — most important finding first). Store as `recommendation`.


### Step 3: Select experiment lenses

Spawn a **sonnet subagent** with `experiment_type` and the plan's IV/DV structure.
Select 1–2 exp-lens skills using the Lens Selection Table above.

Store the selected lens slugs in `selected_lenses` list.


### Step 4: Generate experiment design diagrams

For each lens in `selected_lenses`:

**Do not output any prose between lens iterations — immediately proceed to sub-step 1 for the next lens.**

1. Write an experiment context file to
   `.autoskillit/temp/open-research-pr/exp_lens_context_{lens_slug}_{ts}.md`
   containing: IV/DV/controlled variables tables, hypothesis (H0/H1), success criteria

2. Load the exp-lens skill using the Skill tool:
   `/autoskillit:exp-lens-{lens_slug}`
   The lens skill reads the context, runs its analysis, and emits:
   `diagram_path = /absolute/path/to/.autoskillit/temp/exp-lens-{lens_slug}/exp_diag_{lens_slug}_{ts}.md`

3. Capture `diagram_path` from the skill's output token. Read the file at that path.
   Extract the mermaid block.

4. Validate the diagram has meaningful content: check for at least two of the following
   keywords in the mermaid node labels: treatment, outcome, hypothesis, H0, H1, IV, DV,
   causal, confound, mechanism, effect, comparison, baseline, threshold.
   If none present, discard silently (do not add to `validated_diagrams`).

5. Add validated mermaid block to `validated_diagrams` list.


### Step 5: Build results summary

From the report's Results section, extract:
- A findings table:
  ```
  | Finding | Result | Confidence |
  |---------|--------|------------|
  ```
- The experiment status badge (CONCLUSIVE_POSITIVE / CONCLUSIVE_NEGATIVE /
  INCONCLUSIVE / FAILED)
- If `*_metrics.json` files are referenced in a Standardized Metrics section,
  include their key metrics inline


### Step 6: Compose PR body

Write to `.autoskillit/temp/open-research-pr/pr_body_{ts}.md`:

```
## Recommendation
{recommendation from Step 2}

## Experiment Design
{if validated_diagrams is non-empty, include mermaid blocks; omit section otherwise}

| Hypothesis | |
|---|---|
| H0 | {null hypothesis} |
| H1 | {alternative hypothesis} |

| Metric | Unit | Threshold |
|--------|------|-----------|
{from plan's Dependent Variables table}

## Key Results
{status badge: CONCLUSIVE_POSITIVE / CONCLUSIVE_NEGATIVE / INCONCLUSIVE / FAILED}

{findings table from Step 5}

## Methodology
{condensed from report's Methodology section: setup, test procedure, parameters}

## What We Learned
{from report's What We Learned or Learnings section}

## Full Report & Artifacts
- Report: `{report_path relative to repo root}`
- Experiment plan: `{experiment_plan_path relative to repo root}`

{if closing_issue}
Closes #{closing_issue}
{/if}

🤖 Generated with [Claude Code](https://claude.com/claude-code) via AutoSkillit
```

Store the written path in `pr_body_path`.


### Step 7: Create PR

Check GitHub availability:
```bash
gh auth status 2>/dev/null
```

If **not available**: emit `pr_url = ` (empty) and exit 0 (graceful degradation).

If **available**:
```bash
PR_BODY_PATH=$(ls .autoskillit/temp/open-research-pr/pr_body_*.md 2>/dev/null | tail -1)
if [ -z "${PR_BODY_PATH}" ]; then
  echo "No PR body file found — emitting empty pr_url"
  echo "pr_url = "
  echo "%%ORDER_UP%%"
  exit 0
fi
gh pr create \
  --base "{base_branch}" \
  --head {feature_branch} \
  --title "Research: {title extracted from report's first H1 or title field}" \
  --body-file "${PR_BODY_PATH}"
```

Capture the URL output. Emit:
```
pr_url = {url}
%%ORDER_UP%%
```

## Output

Emit on the final line of output:

```
pr_url = {url}
%%ORDER_UP%%
```

Where `{url}` is the absolute GitHub PR URL, or an empty string when GitHub is unavailable.
