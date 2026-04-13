---
name: scope
categories: [research]
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

**ALWAYS:**
- Use `model: "sonnet"` when spawning all subagents via the Task tool
- Write output to `{{AUTOSKILLIT_TEMP}}/scope/` directory
- Clearly separate facts (what the code does) from hypotheses (what might be true)
- Include a known/unknown matrix in the output

## Workflow

### Step 0 — Setup

1. Parse the research question from arguments.
2. If a GitHub issue reference is detected, fetch it via `fetch_github_issue`.
3. Create the output directory: `mkdir -p {{AUTOSKILLIT_TEMP}}/scope/`

### Step 1 — Parallel Exploration

Launch subagents via the Task tool (model: "sonnet") to explore in parallel.
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

### Step 3 — Write Output

Save the scope report to:
`{{AUTOSKILLIT_TEMP}}/scope/scope_{topic}_{YYYY-MM-DD_HHMMSS}.md` (relative to the current working directory)

Where `{topic}` is a snake_case summary of the research question (max 40 chars).

After saving, emit the structured output token as the very last line of your
text output:

> **IMPORTANT:** Emit the structured output tokens as **literal plain text with no
> markdown formatting on the token names**. Do not wrap token names in `**bold**`,
> `*italic*`, or any other markdown. The adjudicator performs a regex match on the
> exact token name — decorators cause match failure.

```
scope_report = {absolute_path_to_scope_report}
```
