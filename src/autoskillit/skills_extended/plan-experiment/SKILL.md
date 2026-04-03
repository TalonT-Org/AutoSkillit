---
name: plan-experiment
categories: [research]
description: Convert a scope report into a structured experiment plan with hypothesis, variables, phases, and success criteria.
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo '[SKILL: plan-experiment] Planning experiment...'"
          once: true
---

# Plan Experiment Skill

Transform a scope report into an experiment plan. The output is both a research
design AND an implementation plan — it describes what is being tested, how to
build the experiment infrastructure, and what to run. The plan is posted as a
GitHub issue for human review before any compute is spent.

The plan must be specific and actionable: an implementer should be able to read
it and know exactly what files to create, what environment to set up, what
commands to run, and what results to collect. Everything is planned to live in
one self-contained folder under `research/`.

## When to Use

- As the second step of the `research` recipe (phase 1)
- When you have a scope report and need to plan an experiment

## Arguments

```
/autoskillit:plan-experiment {scope_report_path}
```

`{scope_report_path}` — Absolute path to the scope report produced by `/autoskillit:scope`
(required). Scan tokens after the skill name for the first path-like token
(starts with `/`, `./`, or `.autoskillit/`).

## Critical Constraints

**NEVER:**
- Modify any source code files
- Create files outside `.autoskillit/temp/plan-experiment/` directory
- Write implementation code — this skill produces a plan only
- Skip the threats-to-validity section
- Leave success criteria vague — every criterion must be measurable
- Omit the environment assessment — always explicitly state whether a custom
  environment is needed or not, and why

**ALWAYS:**
- Use `model: "sonnet"` when spawning all subagents via the Task tool
- Write output to `.autoskillit/temp/plan-experiment/` directory
- State hypotheses as falsifiable claims with measurable outcomes
- Define metrics before describing the method
- Reference specific files, functions, and test fixtures from the scope report
- Plan all artifacts into one self-contained `research/YYYY-MM-DD-{slug}/` folder
- Include implementation phases that an implementer can follow step by step

## Workflow

### Step 1 — Read Scope Report

Read the scope report at `{scope_report_path}`. Extract:
- The research question
- Known/unknown matrix
- Proposed investigation directions
- Success criteria hints
- External research findings

### Step 2 — Explore Feasibility

Launch subagents (model: "sonnet") to assess feasibility. The following are
**minimum required** — launch as many additional subagents as needed to fill
information gaps and produce the best possible experiment plan.

**Minimum subagents:**

**Subagent A — Measurement Feasibility:**
> Search the project for any existing metrics infrastructure — files that define
> canonical metric names, measurement dimensions, quality thresholds, or
> standardized assessment tooling (e.g., files named `metrics.*`, `benchmark.*`,
> `evaluation.*`, or any assessment/scoring module). If no dedicated metrics
> infrastructure exists, treat all dependent variables as "NEW".
> Cross-reference against the scope report's Metric Context section if present;
> if absent, proceed without it and note the gap.
> For each dependent variable in the research question: verify it has an existing
> measurement mechanism, or flag it as "NEW" requiring formula, unit, and
> threshold definition. Report what measurement infrastructure already exists
> vs. what needs to be built.

**Subagent B — Data & Input Feasibility:**
> Assess what data the experiment needs to operate on. Can it be generated
> synthetically? Does it need to be constructed with specific properties?
> Are there existing datasets or fixtures that can be reused? What
> generators or construction scripts would need to be written?

**Subagent C — Environment Assessment:**
> Determine whether the experiment can run with the project's existing
> toolchain, or whether it requires additional tools, libraries, or
> runtimes. If external tools are needed, research the correct package
> names and versions for a micromamba/conda environment.yml.

**Additional subagents (launch as many as needed):**
- Web searches for relevant tools, libraries, measurement techniques,
  established methodologies, or documentation for specific technologies
- Deeper exploration of specific code areas
- Research into how similar experiments have been designed elsewhere
- Investigation of specific technical constraints or requirements
- Any other research that improves the experiment plan

### Step 3 — Write Experiment Plan

Produce a structured experiment plan. The plan has two halves: the **research
design** (what and why) and the **implementation plan** (how to build it).

Choose a date-stamped slug for the experiment folder:
`research/YYYY-MM-DD-{slug}/` where `{slug}` is a kebab-case summary of the
research topic (max 40 chars).

```markdown
# Experiment Plan: {title}

## Motivation
{Why this experiment matters. What decision will its results inform?}

## Hypothesis

**Null hypothesis (H0):** {The default assumption — no effect, no difference}
**Alternative hypothesis (H1):** {The claim being tested — stated with a
measurable outcome}

## Independent Variables
{What is being varied}

| Variable | Values | Rationale |
|----------|--------|-----------|
| {var1} | {value_a, value_b} | {why these values} |

## Dependent Variables (Metrics)
{What is being measured}

| Metric | Unit | Collection Method | Canonical Name |
|--------|------|-------------------|----------------|
| {metric1} | {unit} | {how collected} | {name in src/metrics.rs, or "NEW"} |

Canonical names must match entries in `src/metrics.rs`. For any metric marked
"NEW", include: formula, unit, threshold value, and a note that it must be added
to the catalog before the experiment is finalized.

## Controlled Variables
{What is held constant and how}

| Variable | Fixed Value | Rationale |
|----------|-------------|-----------|
| {var1} | {value} | {why fixed} |

## Inputs and Data

{What data the experiment operates on. The inputs determine what the
experiment can prove.}

- {What datasets are needed — existing, synthetic, or constructed?}
- {How will datasets be generated or obtained?}
- {What properties must the data have to be a valid test of the hypothesis?}
- {What range and diversity of inputs avoids narrow conclusions?}

| Dataset | Source | Properties | Purpose |
|---------|--------|------------|---------|
| {dataset1} | {generated/existing/external} | {key characteristics} | {what it tests} |

## Experiment Directory Layout

All experiment artifacts live in one self-contained folder:

```
research/YYYY-MM-DD-{slug}/
├── environment.yml           # Micromamba/conda env (if needed)
├── scripts/
│   ├── {script_1}            # {description}
│   ├── {script_2}            # {description}
│   └── ...
├── data/                     # Generated/input data
├── results/                  # Experiment output (metrics, logs)
└── report.md                 # Final report (written by write-report)
```

{Describe each planned file and its purpose.}

## Environment

{Explicitly state one of:}

**Option A — No custom environment needed:**
{The project's existing toolchain is sufficient because {reason}. No
environment.yml will be created.}

**Option B — Custom environment required:**
{The experiment requires {tools/libraries} that are not part of the project.
An environment.yml will be created with the following specification:}

```yaml
name: {experiment-slug}
channels:
  - conda-forge
dependencies:
  - {package1}={version}
  - {package2}={version}
```

{Rationale for each dependency.}

## Implementation Phases

### Phase 1: Directory Structure and Environment
- Create `research/YYYY-MM-DD-{slug}/` and subdirectories
- Create `environment.yml` (if needed) and build the environment
- Verify environment is functional

### Phase 2: Data Generation
- Create data generation scripts in `scripts/`
- Generate datasets into `data/`
- Verify data has the required properties

### Phase 3: Experiment Scripts
- Create measurement/benchmark scripts in `scripts/`
- Create any analysis or post-processing scripts
- Verify scripts run correctly with small inputs

### Phase 4: Dry Run
- Execute the full experiment procedure with minimal inputs
- Verify metrics are collected correctly
- Confirm end-to-end pipeline works before committing to full runs

{Adapt phases as needed — not all experiments require all phases. Add or
remove phases to match the specific experiment. Each phase should list the
specific files to create and commands to run.}

## Execution Protocol

{Step-by-step procedure for running the actual experiment after
implementation is complete. Be specific about what commands to run,
what data to collect, and in what order.}

## Analysis Plan
{How to interpret the results. Include statistical analysis if relevant
to the experiment type — not all experiments require it. Describe what
patterns or outcomes would support or refute the hypothesis.}

## Success Criteria
{Explicit, measurable conditions that answer the research question}

- **Conclusive positive:** {specific condition that supports H1}
- **Conclusive negative:** {specific condition that supports H0}
- **Inconclusive:** {conditions under which no conclusion can be drawn}

## Threats to Validity

### Internal
{Confounds that could invalidate results within this experiment}

### External
{Limits on generalizability beyond the test conditions}

## Estimated Resource Requirements
{Approximate compute time, disk space, dependencies needed}
```

### Step 4 — Write Output

Save the experiment plan to:
`.autoskillit/temp/plan-experiment/experiment_plan_{topic}_{YYYY-MM-DD_HHMMSS}.md` (relative to the current working directory)

After saving, emit the structured output token as the very last line of your
text output:

> **IMPORTANT:** Emit the structured output tokens as **literal plain text with no
> markdown formatting on the token names**. Do not wrap token names in `**bold**`,
> `*italic*`, or any other markdown. The adjudicator performs a regex match on the
> exact token name — decorators cause match failure.

```
experiment_plan = {absolute_path_to_experiment_plan}
```
