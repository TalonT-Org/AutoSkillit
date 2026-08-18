---
name: exp-lens-unit-interference
categories:
- exp-lens
uses_capabilities: []
activate_deps:
- mermaid
description: Create Unit Interference experimental design diagram showing unit hierarchy, cluster structure, shared resources,
  and SUTVA violation pathways. Causal-Structural lens answering "What is the unit, and can treatments spill over?"
hooks:
  PreToolUse:
  - matcher: '*'
    hooks:
    - type: command
      command: echo 'Unit Interference Lens - Analyzing experimental units and spillover...'
      once: true
semantic_version: 1
semantic_requirements:
  sibling_skills:
  - name: exp-lens-causal-assumptions
  - name: exp-lens-randomization-blocking
  - name: make-experiment-diag
  - name: mermaid
  logical_roles:
  - name: delegated-worker
    purpose: perform the named independent responsibility and return bounded evidence
  child_spawns:
  - role: delegated-worker
    for_each: design_dimensions
  concurrency:
    required: true
  join:
    required: true
  evidence:
    required: true
    independent: true
---

# Unit Interference Experimental Design Lens

**Philosophical Mode:** Causal-Structural
**Primary Question:** "What is the unit, and can treatments spill over?"
**Focus:** Experimental Unit, Cluster Structure, Shared Resources, Network Effects, SUTVA Violations

## Arguments

`/autoskillit:exp-lens-unit-interference [context_path] [experiment_plan_path]`

- **context_path** (optional positional arg 1) — Absolute path to a lens context file
  containing IV/DV tables, H0/H1 hypotheses, controlled variables, and success criteria.
  If provided, read this file before beginning analysis to obtain structured context.
  If omitted, discover context by exploring the CWD.
- **experiment_plan_path** (optional positional arg 2) — Absolute path to the full
  experiment plan. If provided, read for complete experimental methodology and design.
  If omitted, locate the experiment plan by exploring the CWD.

## When to Use

- Online A/B tests with shared infrastructure
- Distributed systems where units share caches, queues, or services
- Social or network experiments where units are connected
- User invokes `/autoskillit:exp-lens-unit-interference` or `/autoskillit:make-experiment-diag unit`

## Critical Constraints

**NEVER:**
- Fabricate, invent, or embellish information not supported by the available evidence or code.

- Modify any source code files
- Do not litter the codebase with useless comments, TODO markers, or explanatory annotations — the skill output and diagram speak for themselves
- Create files outside `{{AUTOSKILLIT_TEMP}}/exp-lens-unit-interference/`
- Execute target code, experiment workflows, or target test commands to gather exploration evidence
- Detach child delegations instead of joining them (joining every child is required)
- Run exploration leaves in the background
- Start independent child delegations sequentially

**ALWAYS:**
- Focus on the unit definition and whether SUTVA is plausible
- Map the full unit-cluster-resource hierarchy before assessing interference
- Identify every shared resource that could transmit treatment effects across groups
- Distinguish direct spillover (shared cache) from indirect spillover (market equilibrium)
- BEFORE creating any diagram, LOAD the `/autoskillit:mermaid` skill using the Skill tool - this is MANDATORY
- If the Skill tool cannot be used (disable-model-invocation) or refuses this invocation, do NOT proceed with diagram creation. Abort this step and omit the diagram from output.
- Start all independent child delegations before awaiting any result to maximize concurrency
- Use the registered exploration roles for all repository reads
- Register every exploration vector below and route the missing-context fallback only for fields absent after parent-side argument parsing
- Allow parent-boundary handoff between code navigation and declarative shared-resource or assignment evidence without creating extra vectors
- Wait for every applicable exploration result before mapping the hierarchy, analyzing interference, assessing SUTVA, or creating the diagram
- Retain parent authority over unit and cluster interpretation, spillover classification, magnitude and mitigation judgment, and diagram creation
- Write output to `{{AUTOSKILLIT_TEMP}}/exp-lens-unit-interference/exp_diag_unit_interference_{YYYY-MM-DD_HHMMSS}.md`
- After writing the file, emit the structured output token as **literal plain text** with no
  markdown formatting on the token name (the adjudicator performs a regex match):

  ```
  diagram_path = /absolute/path/to/{{AUTOSKILLIT_TEMP}}/exp-lens-unit-interference/exp_diag_unit_interference_{...}.md
  ```

---

## Analysis Workflow

### Step 0: Parse optional arguments

If positional arg 1 (context_path) is provided and the file exists, read it to obtain
IV/DV tables, H0/H1 hypotheses, controlled variables, and success criteria. If positional
arg 2 (experiment_plan_path) is provided and exists, read the experiment plan for full
methodology. Use this structured context as the foundation for Steps 1-5; skip the CWD
exploration for these fields if the context file supplies them.

<!-- autoskillit:exploration-vector id="missing-context-fields" -->
After the parent parses the optional context and experiment plan, dispatch repository retrieval only for required fields still absent. Never rediscover or override a supplied complete field. If no fields remain missing, report this vector not applicable and perform no search. If scoped evidence is absent or unrelated, report the field unavailable or unrelated without widening scope, inferring meaning, or importing or executing target code, tests, experiments, models, or benchmarks.
<!-- /autoskillit:exploration-vector -->

### Step 1: Launch the Routed Exploration Vectors (SINGLE MESSAGE)

Dispatch all ready, scope-disjoint Step-1 vectors through the deterministic router in a single message before awaiting any result. Do not iterate across multiple turns.

Do not output any prose between subagent dispatches. Immediately proceed to the next tool call.

Dispatch every Step-1 vector below under their registered role policies. The parent/router may hand bounded code or declarative evidence to the other registered role when needed; this does not create another vector. Each leaf returns terminal evidence only and must not execute the target, define the final unit hierarchy, assess SUTVA, rate spillover, create diagrams, or write lens output.

<!-- autoskillit:exploration-vector id="unit-definition" -->
1. **Unit definition** — Trace what constitutes one experimental unit, including user, request, session, query, item, sample, trial, or instance definitions and consumers.
<!-- /autoskillit:exploration-vector -->

<!-- autoskillit:exploration-vector id="cluster-group-structure" -->
2. **Cluster and group structure** — Trace groupings and membership paths for clusters, groups, shards, servers, regions, batches, households, and teams, including structures predating assignment.
<!-- /autoskillit:exploration-vector -->

<!-- autoskillit:exploration-vector id="shared-resources" -->
3. **Shared resources** — Identify infrastructure and access declarations shared across treatment and control groups, including caches, queues, pools, databases, services, load balancers, accelerators, and memory.
<!-- /autoskillit:exploration-vector -->

<!-- autoskillit:exploration-vector id="network-social-connections" -->
4. **Network and social connections** — Trace network, graph, friend, neighbor, link, message, recommendation, and influence definitions and call paths connecting units.
<!-- /autoskillit:exploration-vector -->

<!-- autoskillit:exploration-vector id="treatment-assignment-boundary" -->
5. **Treatment assignment boundary** — Trace bucket, hash, experiment, variant, flag, feature-flag, and rollout definitions and control paths, including bounded declarative registration handoffs.
<!-- /autoskillit:exploration-vector -->

### Step 2: Map the Unit-Cluster-Resource Hierarchy

For each level of the hierarchy:
- Can treatment at one level affect outcomes at another?
- Identify specific spillover pathways between levels
- Assess whether SUTVA (stable unit treatment value assumption) is plausible at each level

Document:
- **Unit Level**: The atomic entity receiving treatment
- **Cluster Level**: Natural groupings of units with shared context
- **System Level**: Infrastructure shared across all groups

### Step 3: Analyze Interference Pathways

**CRITICAL — Analyze Interference Pathways:**
For every shared resource or connection:
- Could treatment group A's behavior change the experience of control group B?
- Is this interference direct (shared cache hit rates) or indirect (market-level equilibrium effects)?
- What is the likely magnitude: negligible, moderate, or invalidating?
- Is there a mitigation strategy (cluster-level randomization, holdout, depletion correction)?

Rate each pathway:
- **HIGH**: Interference almost certainly contaminates the control group
- **MEDIUM**: Plausible interference under realistic usage patterns
- **LOW**: Theoretical but unlikely to affect measured outcomes

### Step 4: Create the Diagram

Use flowchart with:

**Direction:** `TB` (units nested within clusters nested within the system)

**Subgraphs:**
- "EXPERIMENTAL UNITS" (the atomic entities being randomized)
- "CLUSTER STRUCTURE" (groupings above the unit level)
- "SHARED RESOURCES" (infrastructure accessible by both groups)
- "INTERFERENCE PATHWAYS" (explicit spillover routes)

**Node Styling:**
- `cli` class: Experimental units
- `phase` class: Cluster / group nodes
- `stateNode` class: Shared resources
- `gap` class: Interference pathways
- `handler` class: Treatment assignment
- `detector` class: SUTVA boundary

### Step 5: Write Output

Write the diagram to: `{{AUTOSKILLIT_TEMP}}/exp-lens-unit-interference/exp_diag_unit_interference_{YYYY-MM-DD_HHMMSS}.md` (relative to the current working directory)

---

## Output Template

```markdown
# Unit Interference Assessment: {Experiment Name}

**Lens:** Unit Interference (Causal-Structural)
**Question:** What is the unit, and can treatments spill over?
**Date:** {YYYY-MM-DD}
**Scope:** {What was analyzed}

## Unit Hierarchy

| Level | Entity | Shares With | Clustering Risk |
|-------|--------|-------------|-----------------|
| {level} | {entity} | {shared resources} | {High/Medium/Low} |

## Interference Pathways

| Pathway | Type | Mechanism | Magnitude | Mitigation |
|---------|------|-----------|-----------|------------|
| {pathway} | {Direct/Indirect} | {mechanism} | {High/Medium/Low} | {mitigation} |

## Interference Diagram

```mermaid
%%{init: {'flowchart': {'nodeSpacing': 50, 'rankSpacing': 60, 'curve': 'basis'}}}%%
flowchart TB
    %% CLASS DEFINITIONS %%
    classDef cli fill:#1a237e,stroke:#7986cb,stroke-width:2px,color:#fff;
    classDef stateNode fill:#004d40,stroke:#4db6ac,stroke-width:2px,color:#fff;
    classDef handler fill:#e65100,stroke:#ffb74d,stroke-width:2px,color:#fff;
    classDef phase fill:#6a1b9a,stroke:#ba68c8,stroke-width:2px,color:#fff;
    classDef newComponent fill:#2e7d32,stroke:#81c784,stroke-width:2px,color:#fff;
    classDef output fill:#00695c,stroke:#4db6ac,stroke-width:2px,color:#fff;
    classDef detector fill:#b71c1c,stroke:#ef5350,stroke-width:2px,color:#fff;
    classDef gap fill:#ff6f00,stroke:#ffa726,stroke-width:2px,color:#000;
    classDef integration fill:#c62828,stroke:#ef9a9a,stroke-width:2px,color:#fff;

    subgraph ExperimentalUnits ["EXPERIMENTAL UNITS"]
        EU1["Unit A<br/>━━━━━━━━━━<br/>Treatment group"]
        EU2["Unit B<br/>━━━━━━━━━━<br/>Control group"]
        ASSIGN["Treatment Assignment<br/>━━━━━━━━━━<br/>Randomization"]
    end

    subgraph ClusterStructure ["CLUSTER STRUCTURE"]
        CL1["Cluster 1<br/>━━━━━━━━━━<br/>Group above unit"]
        CL2["Cluster 2<br/>━━━━━━━━━━<br/>Group above unit"]
    end

    subgraph SharedResources ["SHARED RESOURCES"]
        SR1["Shared Resource<br/>━━━━━━━━━━<br/>Cross-group access"]
    end

    subgraph InterferencePathways ["INTERFERENCE PATHWAYS"]
        IP1["Spillover<br/>━━━━━━━━━━<br/>Treatment leakage"]
        SUTVA["SUTVA Boundary<br/>━━━━━━━━━━<br/>Violation check"]
    end

    %% INTERFERENCE FLOWS %%
    ASSIGN -->|"treats"| EU1
    ASSIGN -->|"controls"| EU2
    EU1 -->|"belongs to"| CL1
    EU2 -->|"belongs to"| CL2
    CL1 -->|"accesses"| SR1
    CL2 -->|"accesses"| SR1
    SR1 -.->|"spillover"| IP1
    IP1 -.->|"violates"| SUTVA

    %% CLASS ASSIGNMENTS %%
    class EU1,EU2 cli;
    class ASSIGN handler;
    class CL1,CL2 phase;
    class SR1 stateNode;
    class IP1 gap;
    class SUTVA detector;
```

**Color Legend:**
| Color | Category | Description |
|-------|----------|-------------|
| Dark Blue | Units | Experimental units (atomic entities) |
| Orange | Assignment | Treatment assignment mechanism |
| Purple | Clusters | Group structure above unit level |
| Teal | Shared Resources | Infrastructure accessible by both groups |
| Amber | Interference | Spillover pathways |
| Red | SUTVA | SUTVA boundary violation detection |

## Recommendations

- {Recommendation 1}
- {Recommendation 2}
```

---

## Pre-Diagram Checklist

Before creating the diagram, verify:

- [ ] LOADED `/autoskillit:mermaid` skill using the Skill tool
- [ ] Using ONLY classDef styles from the mermaid skill (no invented colors)
- [ ] Diagram will include a color legend table

---

## Related Skills

- `/autoskillit:make-experiment-diag` - Parent skill for experimental lens selection
- `/autoskillit:mermaid` - MUST BE LOADED before creating diagram
- `/autoskillit:exp-lens-causal-assumptions` - For DAG-level causal structure analysis
- `/autoskillit:exp-lens-randomization-blocking` - For randomization strategy and blocking design
