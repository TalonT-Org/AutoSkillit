---
name: scope
categories:
- research
uses_capabilities: []
description: Survey codebase and web sources to build a known/unknown matrix for a research question. Phase 1 of the research
  recipe.
hooks:
  PreToolUse:
  - matcher: '*'
    hooks:
    - type: command
      command: 'echo ''[SKILL: scope] Scoping research question...'''
      once: true
semantic_version: 1
semantic_requirements:
  logical_roles:
  - name: delegated-worker
    purpose: perform the named independent responsibility and return bounded evidence
  child_spawns:
  - role: delegated-worker
    count: 1
  concurrency:
    required: true
  join:
    required: true
  evidence:
    required: true
    independent: true
  child_model_policies:
  - role: delegated-worker
    model_class: sonnet
---

# Scope Research Skill

Explore a technical research question before experiment design. Produces a
structured scope report covering what is known, what is unknown, prior art
in the codebase, and proposed hypotheses. This is the first phase of the
research recipe — it informs experiment design without making any code changes.

## When to Use

- As the first step of the `research` recipe (phase 1)
- When you need to understand a technical question before designing experiments
- When scoping what is feasible to investigate in this codebase

## Arguments

```
/autoskillit:scope {research_question}
```

`{research_question}` — The technical question or topic to investigate (required).
This may be a free-text description, a GitHub issue reference (#N or URL), or a
combination.

### GitHub Issue Detection

If `{research_question}` contains a GitHub issue reference (full URL, `owner/repo#N`,
or bare `#N`), fetch the issue body via `fetch_github_issue` with `include_comments: true`
before analysis. Use the issue body as the primary research question; any surrounding
text is supplementary context.

## Critical Constraints

**NEVER:**
- Modify any source code files
- Create files outside `{{AUTOSKILLIT_TEMP}}/scope/` directory
- Propose solutions or write implementation code
- Skip the prior art survey — always check what already exists in the codebase
- Fabricate research findings when external sources return no results — if web searches or literature searches yield nothing, state that explicitly and note what the codebase evidence shows instead
- Dispatch retained vectors through explorer roles; retained work remains delegated prose under the declared `sonnet` model-class policy
- Let an explorer or delegated worker choose the research branch, hypotheses, investigation directions, or final conclusions
- Detach child delegations instead of joining them (joining every child is required)
- Start independent child delegations sequentially

**ALWAYS:**
- Classify the question as `scope-software` or `scope-non-software` before dispatch; the parent owns this decision
- Submit every applicable migrated vector to the deterministic router under its registered role and `auto` profile
- Launch every applicable retained vector via child delegation under the declared `sonnet` model-class policy
- Write output to `{{AUTOSKILLIT_TEMP}}/scope/` directory
- Clearly separate facts (what the code does) from hypotheses (what might be true)
- Include a known/unknown matrix in the output
- Start all independent child delegations before awaiting any result to maximize concurrency
- Join every routed and retained result before the parent synthesizes evidence, proposes hypotheses, or selects investigation directions

## Workflow

### Step 0 — Setup

1. Parse the research question from arguments.
2. If a GitHub issue reference is detected, fetch it via `fetch_github_issue`.
3. Create the output directory: `mkdir -p {{AUTOSKILLIT_TEMP}}/scope/`

### Step 1 — Parallel Exploration (SINGLE MESSAGE)

Classify the research question once, before dispatch:

- Use `scope-software` when the question is primarily about this repository's source,
  architecture, runtime behavior, algorithms, tests, fixtures, or evaluation machinery.
- Use `scope-non-software` when the question is primarily a scientific, social,
  operational, or other domain question whose evidence is not repository structure.
- For a hybrid question, select the branch that owns the core unknown. The parent may
  integrate retained `always` research, but no child may revise the branch decision.

Apply `always` plus the selected branch applicability. Submit all applicable migrated
vectors to the deterministic router, launch all applicable retained vectors through
the declared `sonnet` delegated-worker policy, and only then await results. Retained
vectors must not be dispatched to `semantic-code-navigator` or
`repository-impact-profiler`. Join every result before Step 2.

<!-- autoskillit:exploration-vector id="prior-art-codebase" -->
**[PRIOR ART — Codebase]** (`scope-software`, retained) — Search the repository for
existing implementations, tests, benchmarks, and documentation related to the research
question. Report what exists, its consumers and verification surfaces, and remaining
gaps. Return bounded evidence only; do not decide which prior art should govern the
investigation.
<!-- /autoskillit:exploration-vector -->

<!-- autoskillit:exploration-vector id="prior-art-literature" -->
**[PRIOR ART — Literature]** (`scope-non-software`, retained) — Survey published
literature, established protocols, and known methods. Report what exists and what gaps
remain, with source links. Do not make final relevance or direction decisions.
<!-- /autoskillit:exploration-vector -->

<!-- autoskillit:exploration-vector id="external-research" -->
**[EXTERNAL RESEARCH — Web Search]** (`always`, retained) — Search the web for relevant
tools, methods, papers, documentation, prior work, and community discussion. Report
findings with source links and state explicitly when no credible source is found.
<!-- /autoskillit:exploration-vector -->

<!-- autoskillit:exploration-vector id="domain-context-architecture" -->
**[DOMAIN CONTEXT — Software Architecture]** (`scope-software`, retained) — Trace the
architecture surrounding the research area, including key modules, data structures,
algorithms, imports, calls, and their relationships. Document current behavior and
evidence-backed limitations without choosing a solution.
<!-- /autoskillit:exploration-vector -->

<!-- autoskillit:exploration-vector id="domain-context-domain-knowledge" -->
**[DOMAIN CONTEXT — Domain Knowledge]** (`scope-non-software`, retained) — Assess the
domain-specific structures, relationships, mechanisms, and processes central to the
research question. Separate established facts from interpretations.
<!-- /autoskillit:exploration-vector -->

<!-- autoskillit:exploration-vector id="evaluation-framework-software" -->
**[EVALUATION FRAMEWORK — Software Metrics]** (`scope-software`, retained) — Find local
metrics, benchmarks, evaluation or scoring modules, configuration, tests, and consumers.
Report measurement mechanisms, thresholds, and gaps; if none exist, say so explicitly.
<!-- /autoskillit:exploration-vector -->

<!-- autoskillit:exploration-vector id="evaluation-framework-domain-assessment" -->
**[EVALUATION FRAMEWORK — Domain Assessment]** (`scope-non-software`, retained) — Find
the standard scales, assays, indices, or rubrics used to measure outcomes in the domain.
Report the available standards and gaps; if none are credible, say so explicitly.
<!-- /autoskillit:exploration-vector -->

<!-- autoskillit:exploration-vector id="computational-complexity-local" -->
**[COMPUTATIONAL COMPLEXITY — Local Algorithm]** (`scope-software`, retained) — Identify
the most expensive local operation, including focal, baseline, and reference
computations. Report the concrete library call or algorithm, time and space scaling,
local bottlenecks, and repository-evidenced pitfalls without selecting an approach.
<!-- /autoskillit:exploration-vector -->

<!-- autoskillit:exploration-vector id="computational-complexity-external" -->
**[COMPUTATIONAL COMPLEXITY — External Prior Art]** (`scope-software`, retained) —
Research complexity guarantees and known pitfalls from library documentation and prior
art, including implicit materialization, hidden copies, self-inclusion, and baseline
costs. Cite sources and distinguish documented guarantees from inference.
<!-- /autoskillit:exploration-vector -->

<!-- autoskillit:exploration-vector id="data-availability-repository" -->
**[DATA AVAILABILITY — Repository Datasets and Fixtures]** (`scope-software`, retained) —
Inventory local datasets, fixtures, synthetic generators, manifests, and their
consumers. Report availability, provenance visible in the repository, and gaps needed
for a meaningful experiment.
<!-- /autoskillit:exploration-vector -->

<!-- autoskillit:exploration-vector id="data-availability-external" -->
**[DATA AVAILABILITY — External Datasets]** (`always`, retained) — Survey relevant
external datasets, repositories, and domain-standard corpora, including acquisition or
access constraints. Report what is available and what remains missing.
<!-- /autoskillit:exploration-vector -->

<!-- autoskillit:exploration-vector id="custom-research" -->
**[CUSTOM RESEARCH]** (`scope-non-software`, retained) — Define one bounded additional
domain-research task only when an important non-software aspect is not covered above.
This custom-unreviewed vector remains delegated prose and must never dispatch an
explorer role. Return evidence only; the parent decides whether it affects synthesis.
<!-- /autoskillit:exploration-vector -->

### Step 2 — Synthesize Findings

Consolidate subagent findings into a structured scope report. The report
must contain these sections:

```markdown
# Scope Report: {research_question_summary}

## Research Question
{The precise question being investigated, refined from the raw input}

## Known / Unknown Matrix

| Category | Known | Unknown |
|----------|-------|---------|
| Current state | {what is known about how the subject behaves today} | {what we don't know about it} |
| Performance | {existing metrics/benchmarks} | {unmeasured aspects} |
| Edge cases | {known edge cases} | {suspected but unverified} |
| Prior work | {existing implementations} | {gaps in coverage} |

## Prior Art
{What already exists — implementations, experiments, literature, tests, benchmarks,
documentation, or prior attempts relevant to this research question}

## External Research
{Relevant findings from web searches — tools, methods, papers, documentation}

## Domain Context
{For software questions: architecture, key modules, data flow, algorithms involved.
For non-software questions: domain-specific structures, mechanisms, organisms, pathways,
models, or processes that are central to the research question.}

## Computational Complexity
- **Dominant operation:** {the single most expensive computation the experiment will perform — include the specific library call or algorithm, not just a description}
- **Scaling behavior:** {how cost grows with input size — O(n²), O(n·m), O(n log n), etc. — state both time and space complexity}
- **Known bottlenecks:** {specific library calls, data structures, or algorithms with high memory/time cost — include baseline and reference computations, not just the focal algorithm. If the experiment compares a new method against an exact/standard baseline, the baseline's computational cost must be listed here.}
- **Gotchas:** {known pitfalls from prior art or library documentation — self-inclusion bugs, implicit matrix materializations, hidden copies, dtype-dependent memory multipliers}

## Hypotheses
{Proposed explanations or predictions to test, stated as falsifiable claims}

## Proposed Investigation Directions
{2-3 possible experiment approaches, with trade-offs}

## Success Criteria
{What would constitute a conclusive answer to the research question}

## Metric Context *(include only when an evaluation framework was found)*
{If the [EVALUATION FRAMEWORK] subagent found a metrics or assessment infrastructure:
list which evaluation dimensions apply to this research question, what the current
threshold values or scoring standards are, and where they are defined. If no evaluation
framework was found, omit this section entirely — do not emit an empty section.}
```

### Step 2a — Extract Directions Manifest

After writing the scope report, extract the investigation directions into a
machine-readable JSON sidecar. Parse the **Proposed Investigation Directions**
section of the report and produce a `scope_directions_{topic}_{YYYY-MM-DD_HHMMSS}.json`
file in `{{AUTOSKILLIT_TEMP}}/scope/` (the same directory as the scope report).

**Schema:**

```json
{
  "research_question": "{the refined research question from the report}",
  "generated_at": "{ISO-8601 timestamp}",
  "direction_count": 3,
  "must_cover_count": 2,
  "directions": [
    {
      "direction_id": "D1",
      "title": "Short description of the direction (max 80 chars)",
      "priority": "P0",
      "must_cover": true,
      "source_type": "computational",
      "feasibility_notes": "Brief feasibility assessment (1-2 sentences)"
    }
  ]
}
```

**Field rules:**

| Field | Type | Constraints | Rules |
|-------|------|-------------|-------|
| `direction_id` | string | pattern: `^D\d+$` | Sequential: D1, D2, D3, ... |
| `title` | string | maxLength: 80 | Short summary of the direction, max 80 characters |
| `priority` | enum | values: `P0`, `P1`, `P2` | `P0` (primary), `P1` (secondary), `P2` (exploratory) |
| `must_cover` | boolean | — | `true` for P0 directions; `false` for P1/P2 |
| `source_type` | enum | values: `computational`, `wet_lab`, `literature`, `hybrid` | Classify the investigation approach |
| `feasibility_notes` | string | — | Brief assessment of feasibility, 1-2 sentences |

**Priority assignment rules:**
- **P0 (primary):** Directions that directly address the core research question. These
  are the main investigation paths. At least one direction must be P0. Set `must_cover: true`.
- **P1 (secondary):** Directions that support or complement the primary investigation
  but are not essential. Set `must_cover: false`.
- **P2 (exploratory):** Speculative or long-shot directions worth noting but not
  requiring coverage. Set `must_cover: false`.

**Source type classification:**
- **computational:** Can be investigated entirely through computation (code, simulation, analysis)
- **wet_lab:** Requires physical laboratory work (synthesis, assays, measurements)
- **literature:** Can be resolved through literature review and meta-analysis
- **hybrid:** Requires a combination of computational and experimental approaches

**Validation:** `direction_count` must equal `len(directions)`. `must_cover_count` must
equal the number of entries where `must_cover == true`. `must_cover_count` must be ≥ 1
(at least one direction must have `must_cover: true`). If any invariant is violated,
correct the counts by recomputing them from the `directions` array before writing the
file — do not emit a sidecar with stale or inconsistent count fields.

### Step 3 — Write Output

Save both output files to `{{AUTOSKILLIT_TEMP}}/scope/` (relative to the current working directory):

1. **Scope report:** `scope_{topic}_{YYYY-MM-DD_HHMMSS}.md`
   Where `{topic}` is a snake_case summary of the research question (max 40 chars).

2. **Directions manifest:** `scope_directions_{topic}_{YYYY-MM-DD_HHMMSS}.json`

After saving both files, emit the structured output tokens as the last two lines
of your text output, in this exact order:

> **IMPORTANT:** Emit the structured output tokens as **literal plain text with no
> markdown formatting on the token names**. Do not wrap token names in `**bold**`,
> `*italic*`, or any other markdown. Do not wrap the output block in a code fence.
> The adjudicator performs a regex match on the exact token name — decorators and
> code fences cause match failure.

```
scope_report = {absolute_path_to_scope_report}
scope_directions = {absolute_path_to_scope_directions_json}
```
