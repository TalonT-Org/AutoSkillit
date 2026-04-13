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

/autoskillit:plan-experiment {scope_report_path} [{revision_guidance}]

`{scope_report_path}` — Absolute path to the scope report produced by `/autoskillit:scope`
(required). Scan tokens after the skill name for the first path-like token
(starts with `/`, `./`, or `.autoskillit/`).

`{revision_guidance}` — Optional. Absolute path to revision guidance produced by
`/autoskillit:review-design` when verdict=REVISE. Scan for the second path-like token.
When absent or empty (first pass), proceed normally. When present, read it and
incorporate the feedback before writing the plan.

## Critical Constraints

**NEVER:**
- Modify any source code files
- Create files outside `{{AUTOSKILLIT_TEMP}}/plan-experiment/` directory
- Write implementation code — this skill produces a plan only
- Skip the threats-to-validity section
- Leave success criteria vague — every criterion must be measurable
- Omit the environment assessment — always explicitly state whether a custom
  environment is needed or not, and why
- Omit YAML frontmatter unless a V1–V4 ERROR is triggered — every plan must have frontmatter
- Write frontmatter after the `# Experiment Plan:` heading — it always goes BEFORE

**ALWAYS:**
- Use `model: "sonnet"` when spawning all subagents via the Task tool
- Write output to `{{AUTOSKILLIT_TEMP}}/plan-experiment/` directory
- State hypotheses as falsifiable claims with measurable outcomes
- Define metrics before describing the method
- Reference specific files, functions, and test fixtures from the scope report
- Plan all artifacts into one self-contained `research/YYYY-MM-DD-{slug}/` folder
- Include implementation phases that an implementer can follow step by step
- Write YAML frontmatter between --- delimiters BEFORE the # Experiment Plan: heading
- Apply all 9 validation rules before writing the frontmatter block
- Log V1–V4, V9 ERRORs in a ## Frontmatter Validation Errors section instead of writing frontmatter
- Log V5–V8 WARNINGs as # WARNING: ... YAML comments on the relevant field lines

## Workflow

### Step 1 — Read Scope and Revision Guidance

Detect and read inputs:
1. Read the scope report at `{scope_report_path}`. Extract:
   - The research question
   - Known/unknown matrix
   - Proposed investigation directions
   - Success criteria hints
   - External research findings
2. If a second path-like token is present and resolves to an existing file, read
   `{revision_guidance}`. Extract all revision instructions — these take priority over
   your initial analysis in Step 2. Note which sections of the plan need rework.
   When absent or empty, skip this sub-step and proceed normally (first pass).

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
>
> **Research Task Directive Compliance:** When the research task directive or issue
> specifies using particular data (e.g., "use MERFISH data", "benchmark on real-world
> datasets"), the Data Manifest MUST include acquisition steps for that data. The plan
> must NOT assume the data will already be present — especially in worktrees where
> gitignored directories are empty. If the directive specifies data that requires
> download or generation, include the exact commands in the `acquisition` field.

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
└── report.md                 # Final report (written by generate-report)
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

### Step 3a — Extract YAML Frontmatter

After writing the prose plan, extract structured metadata and write the
complete experiment plan file with YAML frontmatter prepended before the
`# Experiment Plan:` heading. The final file layout is:

```
---
experiment_type: {one of: benchmark, configuration_study, causal_inference, robustness_audit, exploratory}

estimand:
  treatment: "{the intervention}"     # RECOMMENDED; required when causal_inference
  outcome: "{the measured effect}"
  population: "{scope of units}"
  contrast: "{A vs B vs C}"           # REQUIRED for causal_inference

hypothesis_h0: "{null hypothesis with measurable threshold}"   # REQUIRED
hypothesis_h1: "{alt hypothesis with measurable threshold}"    # REQUIRED

metrics:                              # REQUIRED, min 1
  - name: "{metric_name}"
    unit: "{unit}"
    canonical_name: "{src/metrics.rs entry or NEW}"
    collection_method: "{exact command or code path}"
    threshold: "{success threshold}"
    direction: "higher_is_better"     # higher_is_better | lower_is_better | target_value
    primary: true                     # mark exactly one when len(metrics) >= 2

baselines:                            # REQUIRED for benchmark/causal_inference
  - name: "{comparator name}"
    version: "{package==version or git SHA}"
    tuning_budget: "{what tuning was done, or 'default'}"

statistical_plan:                     # REQUIRED unless exploratory
  test: "{primary statistical test}"
  alpha: 0.05
  power_target: 0.80
  correction_method: "Holm-Bonferroni"   # null | Bonferroni | Holm-Bonferroni | BH
  sample_size_justification: "{why N is sufficient}"
  min_detectable_effect: "{MDE in metric units}"

environment:                          # REQUIRED
  type: "custom"                      # standard | custom
  spec_path: "research/{slug}/environment.yml"   # required when type=custom

success_criteria:                     # REQUIRED
  conclusive_positive: "{conditions supporting H1, referencing metrics}"
  conclusive_negative: "{conditions supporting H0}"
  inconclusive: "{conditions where no conclusion can be drawn}"

data_manifest:                        # REQUIRED — one entry per hypothesis (or shared)
  - hypothesis: [H1, H2]             # which hypotheses consume this data
    source_type: synthetic            # synthetic | fixture | external | gitignored
    description: "Gaussian blobs, 10K-100K points"
    acquisition: "generate in-script via sklearn.datasets"
    location: null                    # null for in-script generation
    verification: "non-empty ndarray with shape[0] >= 1000"
  # - hypothesis: [H5]
  #   source_type: external
  #   description: "MERFISH spatial transcriptomics subset"
  #   acquisition: "python tests/visual_eval/generate_merfish_subset.py --n 100000"
  #   location: "temp/merfish_100k/"
  #   verification: "directory exists with >= 1 .parquet file, total size > 10MB"
  #   depends_on: "python tests/visual_eval/download_merfish.py"

experiment_slug: "{YYYY-MM-DD-slug}"  # optional, derived from directory layout
---

# Experiment Plan: {title}
...prose sections unchanged...
```

Use this prose section ↔ frontmatter mapping to extract fields:

| Prose Section | Frontmatter Field(s) |
|---------------|---------------------|
| `## Hypothesis` (H0/H1 bold labels) | `hypothesis_h0`, `hypothesis_h1`, `estimand` |
| `## Independent Variables` table | `estimand.contrast`, `baselines[]` |
| `## Dependent Variables (Metrics)` table | `metrics[]` |
| `## Environment` | `environment` |
| `## Analysis Plan` | `statistical_plan` |
| `## Success Criteria` | `success_criteria` |
| `## Experiment Directory Layout` | `experiment_slug` |
| `## Inputs and Data` | `data_manifest[]` |

### data_manifest (required)

A list of data source entries, one per hypothesis (or shared across hypotheses). Each entry:

**Field definitions:**
| Field | Required | Description |
|-------|----------|-------------|
| `hypothesis` | yes | List of hypothesis IDs that consume this data |
| `source_type` | yes | One of: `synthetic`, `fixture`, `external`, `gitignored` |
| `description` | yes | Human-readable description of the data |
| `acquisition` | yes | Exact command or method to produce/retrieve the data |
| `location` | no | Filesystem path where data will reside (null for in-script) |
| `verification` | yes | How to confirm the data is present and valid |
| `depends_on` | no | Prerequisite acquisition step (e.g., download before subset) |

Apply these validation rules in order before writing the frontmatter:

```
V1: benchmark/causal_inference → len(baselines) >= 1 AND each baseline.version not empty
    ERROR: "Benchmark/causal_inference experiments require at least one named baseline with a version"

V2: causal_inference → estimand.contrast is not null
    ERROR: "causal_inference requires estimand with treatment, outcome, and contrast fields"

V3: !exploratory → statistical_plan present AND test not null
    ERROR: "Non-exploratory experiments require a statistical_plan; use {test: 'none'} to waive"

V4: environment.type=custom → spec_path not null
    ERROR: "Custom environment requires spec_path pointing to environment.yml"

V5: len(metrics) >= 2 → exactly one metric has primary: true
    WARNING: "Multiple metrics but no primary designated; H1 threshold ambiguous"

V6: any metric.canonical_name = "NEW"
    WARNING: "Plan includes NEW metrics not yet in src/metrics.rs"

V7: hypothesis_h1 has no numeric threshold
    WARNING: "H1 should include a measurable numeric threshold"

V8: success_criteria.conclusive_positive should reference at least one metric.name
    WARNING: "Success criteria does not reference any declared metric"

V9: data_manifest completeness
    ERROR if:
    - Any hypothesis referenced in `success_criteria` has no entry in `data_manifest`
    - Any entry with `source_type: external` or `source_type: gitignored` lacks a non-null `location`
    - Any entry with `source_type: external` lacks a `depends_on` or explicit download command in `acquisition`
    ERROR: "Data Manifest incomplete: {specific missing field or hypothesis}"
```

- ERRORs (V1–V4, V9): Stop frontmatter generation, append the error message to the plan
  prose under a `## Frontmatter Validation Errors` section, and save the plan WITHOUT
  a frontmatter block. Emit the `experiment_plan` token as usual.
- WARNINGs (V5–V8): Continue; log each as a `# WARNING: ...` YAML comment on the
  relevant field line.

Field requirements by experiment type:

| Field | benchmark | config_study | causal_inference | robustness_audit | exploratory |
|-------|-----------|-------------|-----------------|-----------------|-------------|
| experiment_type | required | required | required | required | required |
| estimand | recommended | recommended | **required** | recommended | optional |
| hypothesis_h0/h1 | required | required | required | required | required |
| metrics | required | required | required | required | required |
| baselines | **required** | optional | **required** | optional | optional |
| statistical_plan | required | required | required | required | **waived** |
| environment | required | required | required | required | required |
| success_criteria | required | required | required | required | required |
| data_manifest | required | required | required | required | required |

### Step 4 — Write Output

Save the experiment plan to:
`{{AUTOSKILLIT_TEMP}}/plan-experiment/experiment_plan_{topic}_{YYYY-MM-DD_HHMMSS}.md` (relative to the current working directory)

After saving, emit the structured output token as the very last line of your
text output:

> **IMPORTANT:** Emit the structured output tokens as **literal plain text with no
> markdown formatting on the token names**. Do not wrap token names in `**bold**`,
> `*italic*`, or any other markdown. The adjudicator performs a regex match on the
> exact token name — decorators cause match failure.

```
experiment_plan = {absolute_path_to_experiment_plan}
```
