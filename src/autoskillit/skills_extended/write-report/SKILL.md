---
name: write-report
categories: [research]
description: Synthesize experiment results into a structured research report in the research/ folder. Supports --inconclusive flag.
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo '[SKILL: write-report] Writing research report...'"
          once: true
---

# Write Report Skill

Synthesize scope findings, experiment design, and experiment results into a
structured research report. The report is committed to the `research/` directory
in the worktree and becomes the primary deliverable of the research recipe.

This skill handles both conclusive and inconclusive outcomes — inconclusive
results are valid findings, not failures.

## When to Use

- As the reporting step of the `research` recipe (phase 2)
- After `/autoskillit:run-experiment` has produced results (or after retry exhaustion)

## Arguments

```
/autoskillit:generate-report {worktree_path} {results_path} [--inconclusive]
```

- `{worktree_path}` — Absolute path to the worktree (required). First path-like
  token after the skill name.
- `{results_path}` — Absolute path to the experiment results file (required).
  Second path-like token.
- `--inconclusive` — Optional flag indicating experiments were inconclusive
  (retry exhaustion or insufficient evidence). When present, the report
  emphasizes what was learned and why evidence was insufficient, rather than
  framing as a failure.

## Inputs

In addition to the arguments above, this skill reads from the worktree:
- `${RESEARCH_DIR}/visualization-plan.md` — figure inventory and `yaml:figure-spec`
  blocks produced by `plan-visualization`. Read in Step 2.5 to drive plot generation.
- `${RESEARCH_DIR}/report-plan.md` — section outline mapping figure IDs to report
  sections. Read in Step 3 to place figure references correctly.

## Critical Constraints

**NEVER:**
- Modify source code files outside the `research/` directory
- Fabricate or embellish results — report exactly what was measured
- Omit the methodology section — reproducibility requires it
- Frame inconclusive results as failures — they are valid findings
- Create the report outside the worktree's `research/` directory

**ALWAYS:**
- Use `model: "sonnet"` when spawning all subagents via the Task tool
- Write the report to `research/` in the worktree root
- Include experiment scripts inline as fenced code blocks for reproducibility
- Commit the report to the worktree before returning
- Include a "What We Learned" section regardless of outcome
- Link back to the originating GitHub issue if an issue number is available

## Workflow

### Step 1 — Gather All Artifacts

Read all available artifacts from the worktree:
1. Experiment plan: `{{AUTOSKILLIT_TEMP}}/experiment-plan.md`
2. Scope report: `{{AUTOSKILLIT_TEMP}}/scope/` (if available in worktree)
3. Experiment results: `{results_path}`
4. Any raw data files in `{{AUTOSKILLIT_TEMP}}/run-experiment/`
5. Standardized metrics: scan `{{AUTOSKILLIT_TEMP}}/run-experiment/` for
   `*_metrics.json` files (e.g., `accuracy_metrics.json`, `parity_metrics.json`).
   If present, read them — they will populate the Standardized Metrics Assessment
   section of the report.
6. Experiment code: scan the worktree for scripts, fixtures, and tools
   added during implementation

### Step 2 — Determine Report Type

Based on the `--inconclusive` flag and the experiment results status:

**Conclusive (no --inconclusive flag):**
- Full report with definitive findings
- Clear answer to the research question
- Recommendations based on evidence

**Inconclusive (--inconclusive flag or status = INCONCLUSIVE/FAILED):**
- Read `visualization-plan.md` if it exists.
- For each figure-spec: if `data_source.path` exists and has data, generate
  the plot (run Step 2.5 normally for that figure).
- If data is absent: emit the placeholder block instead of the figure:
  ```
  > **[Figure {id} not produced]** — experiment concluded inconclusively;
  > data required for this figure was not produced.
  ```
  Preserve the original `yaml:figure-spec` YAML block in the report for
  reproducibility, indented under a `<details>` collapsible.
- Emphasize what was learned despite lack of definitive answer
- Document boundary conditions established
- Clearly state what additional work would produce a conclusive result
- Distinguish between "negative result" (evidence against hypothesis) and
  "inconclusive" (insufficient evidence either way)

### Step 2.5 — Produce Visualizations

If `${RESEARCH_DIR}/visualization-plan.md` exists:

1. Read `visualization-plan.md`. If it contains zero figure specs (empty plan),
   skip all sub-steps and proceed to Step 3.

2. Create disposable plotting venv (once per run, reuse if already exists):
   ```bash
   python3 -m venv "${RESEARCH_DIR}/.plot-venv"
   "${RESEARCH_DIR}/.plot-venv/bin/pip" install --quiet matplotlib seaborn
   # If any figure-spec declares renderer: plotly:
   "${RESEARCH_DIR}/.plot-venv/bin/pip" install --quiet plotly kaleido
   ```

3. For each `yaml:figure-spec` block in `visualization-plan.md`:
   a. Write a Python plotting script to
      `${RESEARCH_DIR}/scripts/fig{N}_{slug}.py`
      that reads from `data_source.path` (or scans `results/` and `data/` if
      the path does not exist — treat `data_source.path` as a hint).
   b. Run the script:
      ```bash
      "${RESEARCH_DIR}/.plot-venv/bin/python" \
        "${RESEARCH_DIR}/scripts/fig${N}_${slug}.py"
      ```
   c. Confirm output exists at `${RESEARCH_DIR}/images/fig-${N}.{png,svg}`.
   d. On failure: emit `MISSING: fig-${N} — {error summary}` to stdout and
      continue with remaining figures. Do not abort the skill.

4. Commit scripts and images (if any were produced):
   ```bash
   git add research/ && git commit -m "Add visualization scripts and figures"
   ```

### Step 3 — Write Report

Create the report directory and file:
```
research/YYYY-MM-DD-{slug}/
  report.md       # The main research report
  scripts/        # Extracted experiment scripts (optional, if complex)
```

The `{slug}` is a kebab-case summary of the research topic (max 40 chars).

The report structure:

```markdown
# {Research Title}

> Research report for [Issue #{N}]({issue_url}) — {date}

## Executive Summary

### Data Scope Statement (mandatory — include at start of Executive Summary)

Every report must begin the Executive Summary with a Data Scope Statement:

> **Data Scope:** All benchmarks were conducted on {comma-separated list of data types
> used, e.g., "synthetic Gaussian blobs (10K–100K points)"}. {Domain target} data was
> {present and used | absent — all results derive from synthetic data | partial — only
> {subset} was available}.

**Rules:**
- If ALL benchmarks used ONLY synthetic data and the research task was domain-specific:
  state this explicitly. Do not claim domain-specific performance improvements derived
  from synthetic data without this qualifier.
- If some hypotheses were marked N/A or BLOCKED due to missing data: state which
  hypotheses were affected and why.
- Read the experiment plan's `data_manifest` (if available) to determine what data was
  planned vs. what was actually used.

{2-3 paragraph overview: what was investigated, key methodology, headline
finding, and recommendation. Written last, placed first.}

## Background and Research Question

{Context: why this investigation was initiated, what decision it informs,
what was known before this experiment.}

## Methodology

### Experimental Design
{From the experiment design: hypothesis, variables, controls. Include
enough detail for independent reproduction.}

### Environment
- **Repository commit:** {output of `git rev-parse HEAD` — the exact commit this experiment ran against}
- **Branch:** {current branch name}
- **Package versions:** {output of the project's package manager — e.g., `cargo tree`, `pip freeze`, `conda list`, or the contents of lock files. Include ALL relevant dependency versions, not just top-level.}
- **Hardware/OS:** {if relevant to the experiment}
- **Custom environment:** {if a micromamba/conda environment.yml was used, note it and its location}

### Procedure
{Step-by-step description of what was executed.}

## Results

{Present data from the experiment. Use tables, code blocks, or whatever
format best represents the measurements. No interpretation in this
section — just facts.}

### Figure References

Reference figures by ID and caption only. NEVER embed images with `![](...)` syntax.
The HTML report rendered by `bundle-local-report` reads `yaml:figure-spec` metadata
and inserts `<img>` tags at the correct sections. Markdown prose uses:
> "Figure 1 shows ..."  or  "(see Figure 1)"

### Metrics Provenance Check (mandatory before including any metrics)

Before including data from any `*_metrics.json` file:

1. **Check generation timestamp**: The file's modification time must be within the
   current experiment's execution window. If the file predates the experiment run,
   it is stale.
2. **Check content relevance**: Verify the metrics file's contents relate to the
   hypotheses under test. If a metrics file contains data from a different subsystem
   or experiment, it is irrelevant.
3. **Disposition:**
   - **Current and relevant**: Include normally.
   - **Stale**: Disclose in the report: "Note: {filename} predates the current
     experiment run and was not regenerated. Excluded from analysis."
   - **Irrelevant**: Disclose: "Note: {filename} contains {description of actual
     contents} which is unrelated to the hypotheses under test. Excluded."
   - **NEVER** silently drop a metrics file. Always disclose the reason for exclusion.

### Standardized Metrics

{Include this section when `*_metrics.json` files are present in
`{{AUTOSKILLIT_TEMP}}/run-experiment/`. Omit entirely if no metrics JSON was produced.}

| Metric | Dimension | Dataset | Value | Threshold | Status |
|--------|-----------|---------|-------|-----------|--------|
| {metric_name} | {Accuracy/Parity} | {dataset} | {value} | {threshold} | ✅ PASS / ❌ FAIL |

{If any metrics failed: note which solver level or dataset showed the failure
and whether it is within acceptable range for the experiment's scope.}

## Observations

{Notable patterns, anomalies, unexpected behaviors discovered during
the experiment.}

### Gate Enforcement (mandatory for all hypothesis results)

When reporting on pre-specified hypothesis gates:

1. **Use the gate threshold from the experiment plan**, not a different hypothesis's
   threshold. Each hypothesis has its own pre-specified success criterion — do not
   conflate them.
2. **When a gate is NOT met**: State this as a failure. Example: "H6 targeted ≥3×
   speedup at n=100K. Measured: 2.04× at n=50K (estimated ~1.95× at n=100K). **FAIL.**"
3. **When recommending GO**: The GO recommendation must reference the specific gate(s)
   that were met and their measured values. A GO on H1 (which has a ≥1.5× threshold)
   does not satisfy H6 (which has a ≥3× threshold).
4. **NEVER** silently substitute one hypothesis's threshold for another. If H6's gate
   is not met, H6 is a FAIL regardless of whether H1's lower threshold was met by the
   same measurement.

## Analysis

{Interpret the results. Compare against the hypothesis. Explain anomalies.
Connect findings to the original research question. Include statistical
analysis if relevant to the experiment type.}

## What We Learned

{Regardless of outcome, document:}
- {Key insight 1}
- {Key insight 2}
- {Boundary conditions established}
- {Methodology learnings for future experiments}

## Conclusions

{Direct answer to the research question.}

## Recommendations

{Actionable next steps based on findings — what to keep, revert, modify,
or investigate further. Include justification for each recommendation.}

## Appendix: Experiment Scripts

{Include key experiment scripts as fenced code blocks. These are preserved
for reproducibility even after the worktree is cleaned up.}

### {script_name.ext}
```{language}
{script content}
```

## Appendix: Visualization Scripts

{Enumerate each script in `${RESEARCH_DIR}/scripts/fig*.py` produced during
Step 2.5. Include the full script as a fenced Python code block. These are
preserved for figure reproducibility even after the worktree is cleaned up.}

## Appendix: Raw Data

{If raw data is small enough, include inline. Otherwise, reference the
files committed alongside this report.}
```

### Step 4 — Commit and Emit

1. Create the research directory in the worktree:
   `mkdir -p research/YYYY-MM-DD-{slug}/`
2. Write `report.md` to that directory.
3. If experiment scripts are complex (>50 lines), also save them as separate
   files in `research/YYYY-MM-DD-{slug}/scripts/`.
4. Commit to the worktree:
   ```
   git add research/
   git commit -m "Add research report: {brief title}"
   ```

After committing, emit the structured output token as the very last line of
your text output:

> **IMPORTANT:** Emit the structured output tokens as **literal plain text with no
> markdown formatting on the token names**. Do not wrap token names in `**bold**`,
> `*italic*`, or any other markdown. The adjudicator performs a regex match on the
> exact token name — decorators cause match failure.

```
report_path = {absolute_path_to_report.md}
```
