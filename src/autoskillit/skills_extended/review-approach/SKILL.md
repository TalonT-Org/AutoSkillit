---
name: review-approach
uses_capabilities: []
description: Research modern solutions and approaches for issues or features proposed in a report or plan. Use when user says
  "review approach", "review approaches", "research solutions", or wants external validation of a proposed direction.
hooks:
  PreToolUse:
  - matcher: '*'
    hooks:
    - type: command
      command: 'echo ''🌐 [SKILL: review-approach] Researching modern approaches...'''
      once: true
semantic_version: 1
semantic_requirements:
  logical_roles:
  - name: delegated-worker
    purpose: perform the named independent responsibility and return bounded evidence
  child_spawns:
  - role: delegated-worker
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

# Review Approach Skill

Research modern solutions, approaches, and strategies relevant to the issues or features proposed in a report or plan. Uses web search subagents to gather external perspective and surface options the team may not have considered.

## When to Use

- User says "review approach", "review approaches", or "research solutions"
- User wants to validate a proposed direction against current industry practice
- User has a plan or report and wants to explore what modern solutions exist
- After an investigation or plan, before committing to an approach

## Input Contract

In pipeline context, the first argument after the skill name **must be a plan file
path** (e.g. `{{AUTOSKILLIT_TEMP}}/rectify/plan.md`), not just an issue URL. The plan
must already have been produced by a prior `rectify`/`make_plan` step. An issue URL
alone is not a substitute — do not fall back to inferring the plan from "conversation
context" in pipeline context. The pipeline fails with a `review_approach requires a
plan file path argument` error if only an issue URL is provided; treat that as a hard
failure, not something to work around.

## Critical Constraints

**NEVER:**
- Fabricate, invent, or embellish information not supported by the available evidence or code.

- Modify any source code files
- Create files outside `${AUTOSKILLIT_ALLOWED_WRITE_PREFIX:-{{AUTOSKILLIT_TEMP}}/review-approach}`
- Detach child delegations instead of joining them (joining every child is required)
- Start all independent child delegations before awaiting any result so they run concurrently

**ALWAYS:**
- Use subagents with web search for parallel research
- Spawn all subagents via `child delegation under the declared `sonnet` model-class policy`
- Keep findings concise and actionable
- Present options with trade-offs
- Make recommendations based on technical merit and project fit
- Tie research back to the specific problem context
- Include source URLs for all referenced material
- After writing the review file, emit the **absolute path** as a structured output
  token as your final output. Resolve the relative `temp/review-approach/...`
  save path to absolute by prepending the full CWD:
  ```
  review_path = /absolute/cwd/temp/review-approach/{filename}.md
  ```
  This token is MANDATORY — the pipeline cannot proceed without it.
- Start all independent child delegations before awaiting any result to maximize concurrency

## Workflow

### Step 1: Extract Research Targets

From the plan file path argument (or, outside pipeline context, the report/conversation context provided), identify the core problems and proposed features that need external research. Break them into distinct research topics.

### Step 2: Launch Parallel Web Search Subagents (SINGLE MESSAGE)

**Start ALL independent child delegations before awaiting any result — one per item — and join every child before synthesis.**

Do not output any prose between subagent dispatches. Immediately proceed to the next tool call.

Spawn general-purpose subagents (with web search) for each research topic. Each subagent should investigate:

- What modern solutions exist for this problem class
- How mature projects and frameworks approach it
- Recent developments, libraries, or patterns worth considering
- Known pitfalls and trade-offs of common approaches

Tailor the search queries to the specific technologies and constraints of the project.

### Step 3: Synthesize

Consolidate subagent findings into a concise review. For each research topic:

- **What exists**: The relevant modern approaches found
- **Trade-offs**: Strengths and weaknesses in the context of this project
- **Relevance**: How each option relates to the proposed direction

Drop anything that doesn't meaningfully inform the decision.

### Step 4: Write Review

Set the recipe-scoped output directory:

```bash
REVIEW_OUTPUT_DIR="${AUTOSKILLIT_ALLOWED_WRITE_PREFIX:-{{AUTOSKILLIT_TEMP}}/review-approach}"
mkdir -p "${REVIEW_OUTPUT_DIR}"
```

Save to: `${REVIEW_OUTPUT_DIR}/review_approach_{topic}_{YYYY-MM-DD_HHMMSS}.md`.

```markdown
# Approach Review: {Topic}

**Date:** {YYYY-MM-DD}
**Source:** {Name of the report/plan being reviewed}

## Context
{Brief statement of the problem and what was proposed}

## Research Findings

### {Research Topic 1}
{What modern solutions exist, trade-offs, relevance to this project}

**Sources:**
- [{title}]({url})

### {Research Topic 2}
{...}

## Recommendations
{What approaches to pursue and why, based on the research}

## Key Takeaways
{Concise bullets — what matters most for the decision at hand}
```

After saving the review file, emit the structured output token as the very last line
of your text output:

> **IMPORTANT:** Emit the structured output tokens as **literal plain text with no
> markdown formatting on the token names**. Do not wrap token names in `**bold**`,
> `*italic*`, or any other markdown. Do not wrap the output block in a code fence.
> The adjudicator performs a regex match on the exact token name — decorators and
> code fences cause match failure.

```
review_path = {absolute_path_to_review_file}
```
