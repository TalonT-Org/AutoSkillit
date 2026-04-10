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
- Create files outside `.autoskillit/temp/scope/` directory
- Propose solutions or write implementation code
- Skip the prior art survey — always check what already exists in the codebase

**ALWAYS:**
- Use `model: "sonnet"` when spawning all subagents via the Task tool
- Write output to `.autoskillit/temp/scope/` directory
- Clearly separate facts (what the code does) from hypotheses (what might be true)
- Include a known/unknown matrix in the output

## Workflow

### Step 0 — Setup

1. Parse the research question from arguments.
2. If a GitHub issue reference is detected, fetch it via `fetch_github_issue`.
3. Create the output directory: `mkdir -p .autoskillit/temp/scope/`

### Step 1 — Parallel Exploration

Launch subagents via the Task tool (model: "sonnet") to explore in parallel.
The following are **minimum required** subagents — launch as many additional
subagents as needed to fill information gaps. Use your judgment on what
additional exploration is necessary for the specific research question.

**Minimum subagents:**

**Subagent A — Prior Art Survey:**
> Search the codebase for existing implementations, tests, benchmarks, or
> documentation related to the research question. Look for prior attempts,
> related utilities, and relevant test fixtures. Report what already exists
> and what gaps remain.

**Subagent B — Technical Context:**
> Understand the architecture surrounding the research area. Identify the
> key modules, data structures, algorithms, and their relationships.
> Document the current behavior and any known limitations.

**Subagent C — External Research (Web Search):**
> Search the web for relevant tools, methods, papers, documentation, and
> prior work related to the research question. Look for established
> methodologies, known solutions, manual pages for relevant tools, and
> community discussion of the topic. Report findings with source links.

**Subagent D — Metric Context:**
> Read `src/metrics.rs` to identify which quality dimensions (Accuracy, Parity,
> Performance) the research question touches. If `src/metrics.rs` is absent or
> empty, flag it explicitly in the output (do not silently emit an empty section).
> Report the current threshold values for relevant metrics and any existing test
> coverage in `tests/integration/test_metrics_assess.rs`. If that test file does
> not exist, note it as missing rather than omitting coverage information.
> Output a "Metric Context" section listing which canonical metrics apply to this
> research question and their current thresholds.

**Subagent E — Computational Complexity:**
> Identify the most expensive computation the research question involves.
> Examine the codebase for algorithm implementations, library calls with
> known high complexity (e.g., distance matrix computation, sorting of
> large arrays, pairwise comparisons), and any baseline/reference
> computations that will run alongside the focal algorithm. For each
> expensive operation found, note its time and space complexity class
> (O(n²), O(n log n), etc.) and any known pitfalls from library
> documentation or prior art (implicit matrix materializations, hidden
> copies, self-inclusion bugs). Report findings as: dominant operation,
> scaling behavior, known bottlenecks, and gotchas.

**Additional subagents (launch as many as needed):**
- Web searches for specific tools, libraries, or methods relevant to the question
- Deeper exploration of specific code areas identified by early subagents
- Surveys of existing test or benchmark infrastructure
- External reference gathering (papers, docs, issue discussions)
- Any other investigation that fills knowledge gaps

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
| Current behavior | {what the code does today} | {what we don't know about it} |
| Performance | {existing metrics/benchmarks} | {unmeasured aspects} |
| Edge cases | {known edge cases} | {suspected but unverified} |
| Prior work | {existing implementations} | {gaps in coverage} |

## Prior Art in Codebase
{What already exists — implementations, tests, benchmarks, documentation}

## External Research
{Relevant findings from web searches — tools, methods, papers, documentation}

## Technical Context
{Architecture, key modules, data flow, algorithms involved}

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

## Metric Context
{Which canonical metrics from src/metrics.rs apply to this research question.
List each metric name, quality dimension (Accuracy/Parity/Performance), and
current threshold value. Note any gaps where no canonical metric exists.}
```

### Step 3 — Write Output

Save the scope report to:
`.autoskillit/temp/scope/scope_{topic}_{YYYY-MM-DD_HHMMSS}.md` (relative to the current working directory)

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
