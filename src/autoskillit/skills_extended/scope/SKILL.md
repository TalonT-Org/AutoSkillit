---
name: scope
categories: [research]
backend_requirements: [claude-code]
description: Survey codebase and web sources to build a known/unknown matrix for a research question. Phase 1 of the research recipe.
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo '[SKILL: scope] Scoping research question...'"
          once: true
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
- Run subagents in the background (`run_in_background: true` is prohibited)
- Issue subagent Task calls sequentially — ALL must be in a single parallel message

**ALWAYS:**
- Spawn all subagents via `Agent(model="sonnet")`
- Write output to `{{AUTOSKILLIT_TEMP}}/scope/` directory
- Clearly separate facts (what the code does) from hypotheses (what might be true)
- Include a known/unknown matrix in the output
- Issue all Task calls in a single message to maximize parallelism

## Workflow

### Step 0 — Setup

1. Parse the research question from arguments.
2. If a GitHub issue reference is detected, fetch it via `fetch_github_issue`.
3. Create the output directory: `mkdir -p {{AUTOSKILLIT_TEMP}}/scope/`

### Step 1 — Parallel Exploration (SINGLE MESSAGE)

**Issue ALL Task tool calls in a single message — one per item — so they execute in parallel. Do NOT iterate across multiple turns.**

Do not output any prose between subagent dispatches. Immediately proceed to the next tool call.

Launch subagents via `Agent(model="sonnet")` to explore in parallel.
You **must launch at least 5 subagents**. Select from the suggested menu below,
define entirely custom subagents, or use any combination. The menu is a guide,
not a mandate — you are free to skip entries that are not relevant and substitute
your own tasks for any or all of them.

**Suggested subagent menu:**

**[PRIOR ART — Codebase or Literature]**
> For software questions: search the codebase for existing implementations, tests,
> benchmarks, or documentation related to the research question. For domain-specific
> questions (biology, chemistry, social science, etc.): survey published literature,
> established protocols, and known methods. Report what already exists and what gaps
> remain.

**[EXTERNAL RESEARCH — Web Search]**
> Search the web for relevant tools, methods, papers, documentation, and prior work
> related to the research question. Look for established methodologies, known solutions,
> documentation for relevant tools, and community discussion of the topic. Report
> findings with source links.

**[DOMAIN CONTEXT — Architecture or Domain Knowledge]**
> For software questions: understand the architecture surrounding the research area,
> key modules, data structures, algorithms, and their relationships; document current
> behavior and known limitations. For non-software questions: understand the domain-
> specific structures, relationships, mechanisms, and processes that are central to
> the research question.

**[EVALUATION FRAMEWORK — Metrics or Assessment]**
> Search for whatever evaluation framework the project or domain uses. For software
> projects look for files named `metrics.*`, `benchmark.*`, `evaluation.*`, or any
> assessment/scoring module. For non-software domains, look for standard scales,
> assays, indices, or rubrics that the domain uses to measure outcomes. If no
> dedicated evaluation infrastructure exists, flag it explicitly in the output (do
> not silently emit an empty section). Report what measurement mechanisms exist and
> what gaps remain.

**[COMPUTATIONAL COMPLEXITY — Algorithm Analysis]**
> Relevant when the research question involves an algorithm, model, or computational
> approach. Identify the most expensive computation involved. For each expensive
> operation found, note its time and space complexity class (O(n²), O(n log n), etc.)
> and any known pitfalls from library documentation or prior art (implicit matrix
> materializations, hidden copies, self-inclusion bugs, baseline/reference computation
> costs). Report findings as: dominant operation, scaling behavior, known bottlenecks,
> and gotchas.

**[DATA AVAILABILITY — Datasets or Inputs]**
> Survey what data already exists that is relevant to the research question. Can it be
> generated synthetically? Are there existing datasets, fixtures, repositories, or
> domain-standard corpora? Report what is available and what gaps would need to be
> filled to run a meaningful experiment.

**You may also define entirely custom subagents** for aspects of the research question
that require unique investigation not covered by the menu above. Always consider
launching at least one subagent beyond the obvious selections to explore angles you
might have missed.

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
