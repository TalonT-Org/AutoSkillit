---
name: chart-course
description: Interactive strategic compass builder. Guides the user through mapping all possible project directions with progressive codebase analysis, web research, and architectural diagrams at every step. Produces a machine-readable compass document for downstream alignment tracking.
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo '[SKILL: chart-course] Charting strategic course...'"
          once: true
---

# Chart Course — Interactive Strategic Compass Builder

## When to Use

- Starting a new project phase and mapping all possible directions
- Project has grown enough that strategic coherence needs explicit tracking
- Understanding how near-term work relates to long-term options
- Updating an existing compass after significant project changes
- Preparing for a planning session and need a current landscape assessment

This is an **interactive** skill. The user provides vision and priorities;
the skill provides codebase analysis, external research, and visualization.
The compass is built collaboratively through conversation, not generated
autonomously.

## Arguments

```
/chart-course {focus_or_question} [compass_path=<path>]
```

- `{focus_or_question}` — Required. A broad strategic question or focus area.
  Examples: "Map all possible directions for the research platform",
  "What are our options for distributed compute?",
  "Where should we invest for compliance readiness?"
- `compass_path=<path>` — Optional. Path to an existing compass document.
  When provided, the skill loads the existing compass as the starting point
  and the conversation focuses on updating, adding, or retiring directions.

### GitHub Issue Detection

Scan ARGUMENTS for GitHub issue references (full URL, `owner/repo#N`, bare `#N`).
If detected, call `fetch_github_issue(issue_url, include_comments: true)` and
incorporate the issue content as additional strategic context.

## Critical Constraints

**NEVER:**
- Modify any source code files — this is a read-only analysis skill
- Create files outside `{{AUTOSKILLIT_TEMP}}/chart-course/`
- Dismiss directions the user raises — every direction gets honest assessment
- Use implementation difficulty as a reason to exclude a direction
- Include cost estimates or timelines — map what IS, not what it costs
- Skip diagrams — progressive visualization is the core value of this skill
- Generate the compass document without user review and approval
- Write the final compass to disk until the user confirms (plan-apply pattern)
- Proceed past a checkpoint without user response

**ALWAYS:**
- Use `model: "sonnet"` for all Task tool subagent calls
- Initialize code-index via `set_project_path` before exploration (Phase 1)
- Ask the user before moving to the next phase
- Generate at least one diagram per direction explored
- Ground readiness assessments in actual file paths, protocols, and code patterns
- Include source URLs for all external research claims
- Invoke arch-lens and mermaid skills via the Skill tool (see Skill Loading Checklist)
- Emit output tokens as literal plain text at the end (no markdown formatting on token names)

## Skill Loading Checklist

When generating architectural diagrams at any point during the conversation:
- [ ] Determine which arch-lens best fits the aspect being visualized
- [ ] LOAD the corresponding `/autoskillit:arch-lens-*` via the Skill tool
- [ ] The arch-lens skill will LOAD `/autoskillit:mermaid` for styling
- [ ] Diagram uses ONLY classDef styles from the mermaid skill (no invented colors)
- [ ] Color legend table included
- [ ] Every new or proposed component is wired into the call chain

If the Skill tool cannot invoke a sub-skill (disable-model-invocation or
unavailable), produce the diagram directly using mermaid syntax with the
standard classDef palette and note the fallback.

For custom diagrams not covered by an arch-lens (timelines, dependency
graphs, comparison matrices), LOAD `/autoskillit:mermaid` directly via
the Skill tool and produce the diagram using its styling conventions.

## Workflow

The workflow is organized into phases. Each phase ends with a user
checkpoint. Do not proceed to the next phase until the user responds.

---

### Phase 1: Codebase Survey (automated)

#### Step 1.1: Code-Index Initialization

```
mcp__code-index__set_project_path(path="{PROJECT_ROOT}")
```

Fall back to native Glob/Grep if unavailable.

#### Step 1.2: Load Existing Compass (if compass_path provided)

If `compass_path` was provided:
1. Verify file exists with Glob
2. Read the document
3. Extract the `---compass-data---` YAML block
4. Parse existing directions — these become the baseline
5. Present to user: "Found existing compass with N directions. I'll
   re-evaluate each against the current codebase."

#### Step 1.3: Launch Parallel Exploration Subagents

Launch ALL concurrently in a single message. Every subagent uses `model: "sonnet"`.

**Subagent A: Architecture & Extension Points**
Explore protocol definitions, plugin points, layering (L0/L1/L2/L3),
TODO/PLANNED/FIXME comments, stub implementations, and configuration
points. Return: extension points with file paths and what capability
each is designed to support.

**Subagent B: Current Capability Inventory**
Catalog all MCP tools, skills (by tier and category), recipes, hooks,
CLI commands, and run_python callables. Note dependencies between them.
Return: structured capability list.

**Subagent C: Growth Limitations & Technical Debt**
Find hardcoded values, platform assumptions, scaling bottlenecks, tight
coupling, missing abstractions, and test gaps. Return: categorized
limitations with which areas of growth they would affect.

**Subagent D: External Landscape Research (Web Search)**
Research the domain indicated by `focus_or_question`: competitors,
adjacent tools, industry trends, emerging standards, regulations.
Return: landscape summary with source URLs.

**Subagent E: Dependency & Coupling Map**
Map import graph between top-level packages, co-change coupling from
git history, cross-layer violations, circular dependencies.
Return: dependency map with coupling scores.

**Subagent F: Existing Strategic Context**
Search `{{AUTOSKILLIT_TEMP}}/` and `docs/` for existing strategy docs,
research reports, roadmaps. Read last 50 git commits for thematic
patterns. Check GitHub issues for strategic labels.
Return: summary of existing context and themes.

**Additional subagents** may be launched for any other dimensions the
focus question or codebase warrants. The above are the mandatory minimum.

#### Step 1.4: Present Initial Landscape

After all subagents complete, synthesize findings into a concise briefing:

1. **Architecture snapshot** — key layers, extension points, what's flexible
   vs. rigid. Generate a **C4 Container diagram** using:
   ```
   LOAD /autoskillit:arch-lens-c4-container via Skill tool
   ```

2. **Capability map** — what the project can do today

3. **Growth edges** — where the architecture is designed for extension
   vs. where it would resist change. Generate a **Module Dependency diagram**:
   ```
   LOAD /autoskillit:arch-lens-module-dependency via Skill tool
   ```

4. **External landscape highlights** — what others are doing, key trends

5. **Existing strategic context** — what prior work tells us about direction

#### Checkpoint 1

Present the landscape briefing with both diagrams and ask:

> "Here's where the project stands architecturally. What directions are
> you thinking about? What's your vision for where this could go?
> I'll analyze each direction you describe against the codebase."

Wait for user response before proceeding.

---

### Phase 2: Direction Exploration (interactive loop)

This phase repeats for each direction the user describes. The user may
describe one direction at a time or several at once.

#### Step 2.1: Capture the Direction

From the user's description, extract:
- A short name for the direction
- What it would entail (capability, architecture change, domain expansion, etc.)
- Any stated dependencies or prerequisites

#### Step 2.2: Analyze Fit

For each direction, launch targeted analysis:

**Subagent: Codebase Fit Analysis** (`model: "sonnet"`)
- Where in the architecture would this connect?
- What existing protocols, abstractions, or extension points support it?
- What would need to change to accommodate it?
- What existing capabilities does it build on?
- What would it conflict with or make harder?

**Subagent: External Research** (`model: "sonnet"`, web search)
- How do other projects in this space handle this?
- Are there standards, libraries, or patterns to adopt?
- What pitfalls have others encountered?

Launch additional subagents as needed for any other aspects the direction
requires — regulatory implications, performance profiling, integration
patterns, etc.

#### Step 2.3: Generate Direction Diagram

Select the most appropriate arch-lens for this direction and LOAD it:

| Direction type | Arch-lens to use |
|----------------|------------------|
| New execution path or workflow | `arch-lens-process-flow` |
| Data pipeline or storage | `arch-lens-data-lineage` |
| New module or component | `arch-lens-c4-container` |
| API or integration boundary | `arch-lens-security` |
| Parallel execution / scaling | `arch-lens-concurrency` |
| Infrastructure / deployment | `arch-lens-deployment` |
| State management / lifecycle | `arch-lens-state-lifecycle` |
| Error handling / resilience | `arch-lens-error-resilience` |
| Developer tooling / CI | `arch-lens-development` |
| Operational / monitoring | `arch-lens-operational` |
| Data access patterns | `arch-lens-repository-access` |
| Cross-component scenarios | `arch-lens-scenarios` |
| Coupling / dependency | `arch-lens-module-dependency` |

Mark proposed/new components distinctly in the diagram (use the `newComponent`
classDef from the mermaid skill palette and the `★` symbol).

If the direction involves experimental or research aspects, also consider
loading an appropriate exp-lens for dimensional analysis.

#### Step 2.4: Present Direction Analysis

Present to the user:
1. **Fit assessment** — how this direction connects to existing architecture
2. **Readiness score** — ready / partial / planned / concept, with evidence
3. **Architecture diagram** — showing where this plugs in (new components highlighted)
4. **External landscape** — what others do, relevant standards
5. **Dependencies** — what must exist first
6. **Tensions** — what this would make harder or conflict with

#### Step 2.5: Refine with User

Ask:

> "Here's how [direction name] would fit. Does this match your thinking?
> Any aspects I should dig deeper into? Or shall we explore the next direction?"

The user may:
- Refine the direction (re-analyze with updated description)
- Ask to dig deeper into a specific aspect (launch focused subagents)
- Ask for additional diagrams from different perspectives
- Move to the next direction
- Go back and revise a previous direction

Adapt to whatever the user needs. Repeat Steps 2.1–2.5 for each new direction.

#### Checkpoint 2

After the user has described all directions they want to explore (or says
they're ready to proceed), ask:

> "We've explored N directions so far. Ready for me to map the
> relationships between them — what enables what, what conflicts,
> and where the strategic fork points are?"

Wait for user response.

---

### Phase 3: Relationship Mapping (automated with user review)

#### Step 3.1: Build Direction Catalog

From all explored directions, compile the full catalog. For each direction:

| Field | Source |
|-------|--------|
| `id` | Assign `D{NNN}` IDs (preserve existing IDs in update mode) |
| `name` | From user description (confirmed in Phase 2) |
| `category` | One of: `architecture`, `capability`, `domain-expansion`, `compliance`, `performance`, `developer-experience`, `integration`, `infrastructure` |
| `description` | 2-3 sentences from Phase 2 analysis |
| `readiness` | `ready` / `partial` / `planned` / `concept` — from Phase 2 fit assessment |
| `readiness_evidence` | Specific file paths and code patterns from Phase 2 |
| `dependencies` | Direction IDs that must come first |
| `enables` | Direction IDs that become easier after this |
| `conflicts` | Direction IDs that become harder after this |
| `signals` | Files, grep patterns, modules relevant to this direction |
| `priority` | `high` / `medium` / `low` / `exploratory` — ask user if not clear |

#### Step 3.2: Map Dependencies and Conflicts

For each pair of directions, assess:
- Does pursuing A make B easier? (enables)
- Does pursuing A require B first? (depends-on)
- Does pursuing A make B harder? (conflicts)

Use codebase evidence — shared protocols, module boundaries, test
infrastructure, configuration coupling.

#### Step 3.3: Generate Path Dependency Graph

LOAD `/autoskillit:mermaid` via the Skill tool and produce a custom
flowchart showing all directions and their relationships:

- **Nodes** = directions, colored by readiness (ready=green, partial=yellow,
  planned=blue, concept=gray)
- **Green solid edges** = enables
- **Blue dashed edges** = depends-on
- **Red dotted edges** = conflicts
- Label each node with ID + short name

Annotate the diagram with:
- **Bottleneck directions** — high fan-out of enables edges (mark with `★`)
- **Fork points** — conflicting direction pairs (mark with `⚡`)
- **Clusters** — mutually-reinforcing groups (visual grouping via subgraph)

#### Step 3.4: Generate Timeline / Sequencing Diagram

LOAD `/autoskillit:mermaid` via the Skill tool and produce a Gantt-style
or swim-lane diagram showing a natural sequencing of directions based on
dependency order:

- Foundations first (directions others depend on)
- Independent directions shown in parallel
- Late-stage directions that depend on many prerequisites shown last
- Fork points shown as decision nodes

This is NOT a timeline with dates — it is a dependency-ordered sequence
showing what COULD come before what.

#### Step 3.5: Present Relationship Map

Present to the user:
1. **Direction catalog table** — all directions with readiness and priority
2. **Path dependency graph** — mermaid diagram
3. **Sequencing diagram** — dependency-ordered view
4. **Bottleneck analysis** — which directions unlock the most others
5. **Fork points** — where strategic choices must be made
6. **Cluster analysis** — groups of mutually-reinforcing directions

#### Checkpoint 3

Ask:

> "Here's how all the directions relate to each other. Do these
> relationships look right? Any dependencies or conflicts I missed?
> Any priorities you want to adjust before I assemble the compass?"

Wait for user to review and confirm or request adjustments.
If adjustments needed, revise and re-present.

---

### Phase 4: Current Trajectory Assessment

#### Step 4.1: Analyze Recent Work

Using git log and any available PR/issue data:
1. Read last 30-50 commits
2. Map commits to directions (which directions does recent work advance?)
3. Identify drift — work that moves away from stated priorities
4. Identify any inadvertent path narrowing

#### Step 4.2: Generate Trajectory Overlay

LOAD `/autoskillit:mermaid` via the Skill tool and produce a variant of
the path dependency graph with trajectory annotations:

- Directions being actively advanced: bold border
- Directions with no recent movement: dashed border
- Directions being drifted from: red highlight

#### Step 4.3: Present Trajectory

Present the trajectory assessment with the annotated diagram and a brief
narrative about where the project is currently headed vs. where the
compass says it could go.

#### Checkpoint 4

Ask:

> "This is where recent work has been heading. Ready for me to
> assemble the full compass document?"

---

### Phase 5: Compass Assembly (plan-apply pattern)

#### Step 5.1: Draft the Compass

Assemble the full compass document in memory with these sections:

1. **Strategic Focus** — the question that drove this compass
2. **Executive Summary** — 3-5 bullets on current state + trajectory
3. **Direction Catalog** — full table + detailed per-direction subsections
4. **Path Dependency Graph** — mermaid diagram from Phase 3
5. **Sequencing Diagram** — from Phase 3
6. **Trajectory Overlay** — from Phase 4
7. **Cluster Analysis** — mutually-reinforcing direction groups
8. **Fork Points** — strategic choices requiring decisions
9. **Bottleneck Directions** — high-leverage unlock points
10. **Current Trajectory** — where recent work is heading
11. **Cannot Assess** — minimum 2 directions where evidence was insufficient
12. **Methodology** — tools used, limitations, caveats

Plus the machine-readable compass block at the very end:

```
---compass-data---
version: 1
generated: "{ISO-8601 timestamp}"
focus: "{focus_or_question}"
project_root: "{absolute project root path}"
directions:
  - id: D001
    name: "Direction Name"
    category: architecture
    readiness: partial
    readiness_evidence:
      - "src/path/file.py — ProtocolName exists but lacks implementations"
    dependencies: []
    enables: [D003, D007]
    conflicts: [D012]
    signals:
      files:
        - "src/relevant/module/"
        - "src/specific_file.py"
      patterns:
        - "GhostKitchenProvider"
        - "run_on_eks"
      modules:
        - "execution"
        - "server"
    priority: high
  ...
---end-compass-data---
```

#### Step 5.2: Present for Approval

Show the user a summary of what will be written:
- File path: `{{AUTOSKILLIT_TEMP}}/chart-course/compass_{topic}_{YYYY-MM-DD_HHMMSS}.md`
- Direction count with readiness distribution
- Section outline
- The compass-data block (so user can verify direction metadata)

Ask:

> "Here's what the compass document will contain. Write it?"

#### Step 5.3: Write on Confirmation

Only on user confirmation, write the compass document to disk.

If the user requests changes, revise and re-present Step 5.2.

If the user declines, do not write. Inform the user that all diagrams
produced during the conversation are still available in their context.

---

### Phase 6: Finalization

#### Step 6.1: Terminal Summary

Print:
- Compass path (absolute)
- Direction count and readiness distribution
- Top 3 bottleneck directions
- Any fork points identified
- Reminder: "Use `/check-bearing <branch> compass_path=<path>`
  to check branch alignment against this compass."

#### Step 6.2: Output Tokens

Emit as the absolute last lines:

```
compass_path = {absolute_path_to_compass_document}
direction_count = {integer}
ready_count = {integer}
%%ORDER_UP%%
```

**Token rules:** Plain text only. No markdown bold, italic, or backtick
formatting on token names. The pipeline adjudicator uses regex matching
and decoration breaks it.

## Output

| Token | Value | Used By |
|-------|-------|---------|
| `compass_path` | Absolute path to the compass document | `check-bearing`, recipe capture |
| `direction_count` | Total directions identified | Informational |
| `ready_count` | Directions with readiness = ready | Informational |

## Related Skills

- `/check-bearing` — Assess branch alignment against this compass
- `/autoskillit:investigate` — Deep investigation of specific technical questions
- `/autoskillit:scope` — Scope a specific research question from the compass
- `/autoskillit:review-approach` — Research approaches for specific directions
- `/autoskillit:make-plan` — Plan implementation of a chosen direction
