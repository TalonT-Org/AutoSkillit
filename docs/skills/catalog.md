# Skill catalog

The complete list of bundled skills (130 total: 3 in `src/autoskillit/skills/`,
127 in `src/autoskillit/skills_extended/`). Filesystem walk this directory if
you need an exhaustive listing; this catalog groups by purpose.

## Tier 1 — free range (3)

Plugin-scanned at `src/autoskillit/skills/`:

- `open-kitchen` — reveals the 40 kitchen MCP tools
- `close-kitchen` — re-hides them
- `sous-chef` — internal injection by `open_kitchen`; never appears as a slash command

## Tier 2 — interactive cook + headless

Located under `src/autoskillit/skills_extended/`. Grouped by purpose:

### Plan and implementation
`investigate`, `make-plan`, `dry-walkthrough`, `review-approach`,
`implement-worktree`, `rectify`, `make-groups`, `mermaid`, `make-arch-diag`,
`make-experiment-diag`, `build-execution-map`, `plan-visualization`

### Audit suite
`audit-arch`, `audit-cohesion`, `audit-tests`, `audit-defense-standards`,
`audit-bugs`, `audit-friction`, `validate-audit`,
`audit-claims`, `audit-docs`, `audit-feature-gates`, `audit-review-decisions`

### Requirements and planning
`make-req`, `elaborate-phase`, `write-recipe`, `migrate-recipes`,
`setup-project`, `design-guards`, `triage-issues`,
`collapse-issues`, `issue-splitter`, `enrich-issues`, `prepare-issue`,
`process-issues`, `make-campaign`

### Experiment family
`scope`, `plan-experiment`, `implement-experiment`, `run-experiment`,
`generate-report`, `troubleshoot-experiment`

### Research and review
`review-design`, `stage-data`, `bundle-local-report`, `reload-session`

## Tier 3 — pipeline / automation

Also under `src/autoskillit/skills_extended/`. Used by recipes for unattended
runs:

`prepare-pr`, `compose-pr`, `open-integration-pr`, `merge-pr`, `analyze-prs`, `review-pr`,
`resolve-review`, `implement-worktree-no-merge`, `resolve-failures`,
`retry-worktree`, `resolve-merge-conflicts`, `audit-impl`, `smoke-task`,
`report-bug`, `pipeline-summary`, `diagnose-ci`, `verify-diag`,
`resolve-claims-review`, `resolve-design-review`, `resolve-research-review`,
`compose-research-pr`, `prepare-research-pr`, `review-research-pr`,
`promote-to-main`

## arch-lens family (13)

13 architectural-diagram skills under `skills_extended/arch-lens-*/`. Each
answers a specific question about the system:

| Skill | Lens | Question |
|-------|------|----------|
| `arch-lens-c4-container` | C4 container | How is it built? |
| `arch-lens-module-dependency` | Module dependency | How are modules coupled? |
| `arch-lens-process-flow` | Process flow | How does it behave? |
| `arch-lens-data-lineage` | Data lineage | Where is the data? |
| `arch-lens-repository-access` | Repository access | How is data accessed? |
| `arch-lens-state-lifecycle` | State lifecycle | How is state corruption prevented? |
| `arch-lens-deployment` | Deployment | Where does it run? |
| `arch-lens-development` | Development | How is it built and tested? |
| `arch-lens-operational` | Operational | How is it run and monitored? |
| `arch-lens-concurrency` | Concurrency | How does parallelism work? |
| `arch-lens-error-resilience` | Error / resilience | How are failures handled? |
| `arch-lens-scenarios` | Scenarios | Do the components work together? |
| `arch-lens-security` | Security | Where are the trust boundaries? |

## exp-lens family (18)

18 experiment-related diagram skills under `skills_extended/exp-lens-*/`:

| Skill | Lens |
|-------|------|
| `exp-lens-benchmark-representativeness` | Benchmark representativeness |
| `exp-lens-causal-assumptions` | Causal assumptions |
| `exp-lens-comparator-construction` | Comparator construction |
| `exp-lens-error-budget` | Error budget |
| `exp-lens-estimand-clarity` | Estimand clarity |
| `exp-lens-exploratory-confirmatory` | Exploratory vs. confirmatory |
| `exp-lens-fair-comparison` | Fair comparison |
| `exp-lens-governance-risk` | Governance and risk |
| `exp-lens-iterative-learning` | Iterative learning |
| `exp-lens-measurement-validity` | Measurement validity |
| `exp-lens-pipeline-integrity` | Pipeline integrity |
| `exp-lens-randomization-blocking` | Randomization blocking |
| `exp-lens-reproducibility-artifacts` | Reproducibility artifacts |
| `exp-lens-sensitivity-robustness` | Sensitivity and robustness |
| `exp-lens-severity-testing` | Severity testing |
| `exp-lens-unit-interference` | Unit interference |
| `exp-lens-validity-threats` | Validity threats |
| `exp-lens-variance-stability` | Variance stability |

## vis-lens family (12)

12 visualization-planning lenses orchestrated by `plan-visualization`, under
`skills_extended/vis-lens-*/`. Each answers a specific question about a figure or the
figure set:

| # | Skill | Cognitive Mode | Primary Question | Priority |
|---|-------|---------------|------------------|----------|
| 1 | `vis-lens-always-on` | Composite | Is everything correct by default? | P0 |
| 2 | `vis-lens-antipattern` | Diagnostic | What visualization antipatterns are present? | P0 |
| 3 | `vis-lens-chart-select` | Typological | What chart type fits this data shape? | P0 |
| 4 | `vis-lens-domain-norms` | Normative | Does the figure follow domain conventions? | P0 |
| 5 | `vis-lens-uncertainty` | Probabilistic | Is uncertainty properly communicated? | P0 |
| 6 | `vis-lens-color-access` | Chromatic | Is the color encoding accessible and perceptually uniform? | P1 |
| 7 | `vis-lens-figure-table` | Decisional | Should this result be a figure or a table? | P1 |
| 8 | `vis-lens-multi-compare` | Comparative | Are multi-condition comparisons statistically sound? | P1 |
| 9 | `vis-lens-temporal` | Sequential | Is temporal/sequential data displayed correctly? | P1 |
| 10 | `vis-lens-caption-annot` | Annotative | Are figure captions and axis labels fully self-contained? | P2 |
| 11 | `vis-lens-story-arc` | Narrative | Do the figures tell a coherent story across the report? | P2 |
| 12 | `vis-lens-reproducibility` | Replicative | Can the figures be reproduced from the data and code? | P2 |

## Planner family (14)

14 progressive-decomposition sub-skills under `skills_extended/planner-*/`. Invoked
internally by the `planner` recipe to break a roadmap into GitHub milestones and issues:

`planner-analyze`, `planner-assess-review-approach`, `planner-consolidate-wps`,
`planner-elaborate-assignments`, `planner-elaborate-phase`,
`planner-elaborate-wps`, `planner-extract-domain`, `planner-generate-phases`,
`planner-reconcile-deps`, `planner-refine`, `planner-refine-assignments`,
`planner-refine-phases`, `planner-refine-wps`, `planner-validate-task-alignment`

## Rectify doctrine

Several Tier 2 skills (`rectify`, `audit-bugs`, `design-guards`,
`audit-defense-standards`) form the **Rectify doctrine** — when a bug is
investigated, the fix lands at the architectural root rather than the surface
symptom, and the audit suite is updated so the same class of bug cannot
recur. Commit messages prefix with `Rectify:` for traceability; the count of
`Rectify:` commits is reported in `docs/developer/contributing.md`.

## Total: 129

3 (Tier 1) + 126 (`skills_extended/`) = 129 bundled skills. The total is
verified by `tests/docs/test_doc_counts.py` against a filesystem walk so any
addition or removal is caught immediately.
