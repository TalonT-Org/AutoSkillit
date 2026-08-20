---
name: exp-lens-reproducibility-artifacts
categories:
- exp-lens
uses_capabilities: []
activate_deps:
- mermaid
description: Create Reproducibility Artifacts experimental design diagram showing run instructions, environment capture, data
  availability, determinism controls, and audit trail. Transparency lens answering "Could an independent party reproduce this?"
hooks:
  PreToolUse:
  - matcher: '*'
    hooks:
    - type: command
      command: echo 'Reproducibility Artifacts Lens - Auditing reproduction chain...'
      once: true
semantic_version: 1
semantic_requirements:
  sibling_skills:
  - name: exp-lens-pipeline-integrity
  - name: exp-lens-variance-stability
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

# Reproducibility Artifacts Experimental Design Lens

> **Preflight:** Before acting on any `exploration-vector` directive below, call `enable_exploration` to establish read-only broker authority for this session; the vectors below assume broker access has already been granted.

**Philosophical Mode:** Transparency
**Primary Question:** "Could an independent party reproduce this?"
**Focus:** Run Instructions, Environment Capture, Data Availability, Determinism Controls, Audit Trail

## Arguments

`/autoskillit:exp-lens-reproducibility-artifacts [context_path] [experiment_plan_path]`

- **context_path** (optional positional arg 1) — Absolute path to a lens context file
  containing IV/DV tables, H0/H1 hypotheses, controlled variables, and success criteria.
  If provided, read this file before beginning analysis to obtain structured context.
  If omitted, discover context by exploring the CWD.
- **experiment_plan_path** (optional positional arg 2) — Absolute path to the full
  experiment plan. If provided, read for complete experimental methodology and design.
  If omitted, locate the experiment plan by exploring the CWD.

## When to Use

- Evaluating reproducibility of computational experiments
- Auditing artifact completeness
- Checking for undocumented dependencies
- User invokes `/autoskillit:exp-lens-reproducibility-artifacts` or `/autoskillit:make-experiment-diag reproducibility`

## Critical Constraints

**NEVER:**
- Fabricate, invent, or embellish information not supported by the available evidence or code.

- Modify any source code files
- Do not litter the codebase with useless comments, TODO markers, or explanatory annotations — the skill output and diagram speak for themselves
- Create files outside `{{AUTOSKILLIT_TEMP}}/exp-lens-reproducibility-artifacts/`
- Import or execute target code, tests, experiments, models, or benchmarks
- Detach child delegations instead of joining them (joining every child is required)
- Run exploration leaves in the background
- Start independent child delegations sequentially

**ALWAYS:**
- Trace the full chain from "clone repo" to "reproduce figures"
- Classify every artifact as available/unavailable and versioned/floating
- Identify the weakest link in the reproduction chain
- Flag all silent non-determinism risks
- BEFORE creating any diagram, LOAD the `/autoskillit:mermaid` skill using the Skill tool - this is MANDATORY
- If the Skill tool cannot be used (disable-model-invocation) or refuses this invocation, do NOT proceed with diagram creation. Abort this step and omit the diagram from output.
- Start all independent child delegations before awaiting any result to maximize concurrency
- Use the registered exploration roles for all repository reads
- Register every exploration vector below and route the missing-context fallback only for fields absent after parent-side argument parsing
- Allow parent-boundary handoff between code navigation and declarative artifact evidence without creating extra vectors
- Wait for every applicable exploration result before mapping the reproduction chain, classifying artifacts, assessing determinism, or creating the diagram
- Retain parent authority over reproducibility judgment, weakest-link analysis, artifact classification, and diagram creation
- Write output to `{{AUTOSKILLIT_TEMP}}/exp-lens-reproducibility-artifacts/exp_diag_reproducibility_artifacts_{YYYY-MM-DD_HHMMSS}.md`
- After writing the file, emit the structured output token as **literal plain text** with no
  markdown formatting on the token name (the adjudicator performs a regex match):

  ```
  diagram_path = /absolute/path/to/{{AUTOSKILLIT_TEMP}}/exp-lens-reproducibility-artifacts/exp_diag_reproducibility_artifacts_{...}.md
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

Dispatch every Step-1 vector below under their registered role policies. The parent/router may hand bounded code or declarative evidence to the other registered role when needed; this does not create another vector. Each leaf returns terminal evidence only and must not execute the target, classify reproducibility, identify the weakest link, create diagrams, or write lens output.

<!-- autoskillit:exploration-vector id="environment-dependencies" -->
1. **Environment and dependencies** — Find dependency files, container definitions, lockfiles, and environment setup, including requirements, Dockerfile, environment files, conda, pip, nix, and lock artifacts.
<!-- /autoskillit:exploration-vector -->

<!-- autoskillit:exploration-vector id="data-provenance" -->
2. **Data provenance** — Find data download declarations, checksums, hashes, versions, data URLs, DVC metadata, manifests, and their consumers.
<!-- /autoskillit:exploration-vector -->

<!-- autoskillit:exploration-vector id="execution-entry-points" -->
3. **Execution entry points** — Trace run scripts, Makefiles, workflow managers, main definitions, entry points, CLI dispatch, and invocation paths without running them.
<!-- /autoskillit:exploration-vector -->

<!-- autoskillit:exploration-vector id="random-seed-determinism" -->
4. **Random seed and determinism** — Trace seed setting and nondeterminism controls, including random-state, deterministic, CUDNN, and hash-seed definitions and calls.
<!-- /autoskillit:exploration-vector -->

<!-- autoskillit:exploration-vector id="output-artifacts-logging" -->
5. **Output artifacts and logging** — Find result storage, logging, generated figures, checkpoints, and experiment-tracking artifacts and consumers.
<!-- /autoskillit:exploration-vector -->

### Step 2: Map the Reproduction Chain

Map the full chain from "clone repo" to "reproduce figures." Identify each link:
- Is it documented?
- Is it automated?
- Is it deterministic?
- What is the weakest link?

### Step 3: Classify Each Artifact Dependency

**CRITICAL — Analyze Reproduction Chain:**
For every artifact dependency:
- Is the source available (open vs gated)?
- Is the version pinned or floating?
- Is the transform deterministic?
- Could silent environment differences change results?

Assign a status of Pass, Warn, or Fail to each link in the chain based on reproducibility confidence.

### Step 4: Create the Diagram

Use flowchart with:

**Direction:** `LR` (reproduction chain flows left to right)

**Subgraphs:**
- SOURCE CODE
- ENVIRONMENT
- DATA
- EXECUTION
- OUTPUTS

**Node Styling:**
- `cli` class: Entry points and run commands
- `stateNode` class: Versioned and pinned artifacts
- `handler` class: Transforms and scripts
- `output` class: Results and figures
- `gap` class: Missing or undocumented links
- `detector` class: Checksum and validation gates
- `phase` class: External dependencies

**Edge Labels:** pinned, floating, deterministic, nondeterministic, gated

### Step 5: Write Output

Write the diagram to: `{{AUTOSKILLIT_TEMP}}/exp-lens-reproducibility-artifacts/exp_diag_reproducibility_artifacts_{YYYY-MM-DD_HHMMSS}.md` (relative to the current working directory)

---

## Output Template

```markdown
# Reproducibility Artifacts Diagram: {Experiment Name}

**Lens:** Reproducibility Artifacts (Transparency)
**Question:** Could an independent party reproduce this?
**Date:** {YYYY-MM-DD}
**Scope:** {What was analyzed}

## Artifact Inventory

| Artifact | Available? | Versioned? | Deterministic? |
|----------|------------|------------|----------------|
| {artifact} | {Yes/No/Gated} | {Pinned/Floating/None} | {Yes/No/Unknown} |

## Reproduction Chain Diagram

```mermaid
%%{init: {'flowchart': {'nodeSpacing': 50, 'rankSpacing': 60, 'curve': 'basis'}}}%%
flowchart LR
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

    subgraph Source ["SOURCE CODE"]
        REPO["Git Repository<br/>━━━━━━━━━━<br/>Commit hash<br/>Branch"]
        ENTRY["Entry Point<br/>━━━━━━━━━━<br/>run.sh / Makefile"]
    end

    subgraph Env ["ENVIRONMENT"]
        DEPS["Dependencies<br/>━━━━━━━━━━<br/>requirements.txt / lock"]
        MISSING["Undocumented Dep<br/>━━━━━━━━━━<br/>No version pin"]
        EXTDEP["External Service<br/>━━━━━━━━━━<br/>API / cloud resource"]
    end

    subgraph Data ["DATA"]
        RAW["Raw Dataset<br/>━━━━━━━━━━<br/>Checksum available?"]
        CHKSUM["Checksum Gate<br/>━━━━━━━━━━<br/>sha256 / md5"]
        GATED["Gated Dataset<br/>━━━━━━━━━━<br/>Access required"]
    end

    subgraph Exec ["EXECUTION"]
        SEED["Seed Control<br/>━━━━━━━━━━<br/>PYTHONHASHSEED<br/>random_state"]
        SCRIPT["Pipeline Script<br/>━━━━━━━━━━<br/>Deterministic?"]
    end

    subgraph Outputs ["OUTPUTS"]
        RESULTS["Results / Metrics<br/>━━━━━━━━━━<br/>Logged?"]
        FIGS["Figures<br/>━━━━━━━━━━<br/>Reproducible?"]
    end

    %% REPRODUCTION CHAIN %%
    REPO -->|"pinned"| ENTRY
    ENTRY -->|"loads"| DEPS
    DEPS -.->|"floating"| MISSING
    MISSING -.->|"nondeterministic"| SCRIPT
    EXTDEP -->|"gated"| SCRIPT
    RAW -->|"verify"| CHKSUM
    CHKSUM -->|"deterministic"| SCRIPT
    GATED -.->|"gated"| SCRIPT
    SEED -->|"controls"| SCRIPT
    SCRIPT -->|"produces"| RESULTS
    SCRIPT -->|"generates"| FIGS

    %% CLASS ASSIGNMENTS %%
    class REPO,ENTRY cli;
    class DEPS,RAW stateNode;
    class SCRIPT,SEED handler;
    class EXTDEP phase;
    class RESULTS,FIGS output;
    class CHKSUM detector;
    class MISSING,GATED gap;
```

**Color Legend:**
| Color | Category | Description |
|-------|----------|-------------|
| Dark Blue | Entry Point | Run commands and source code |
| Teal | Versioned Artifact | Pinned dependencies and checksummed data |
| Orange | Transform / Script | Pipeline scripts and execution steps |
| Purple | External Dependency | External services and APIs |
| Dark Teal | Output | Results, metrics, and figures |
| Red | Validation Gate | Checksum and integrity checks |
| Amber | Missing Link | Undocumented or gated dependencies |

## Reproduction Checklist

Step-by-step instructions with pass/fail status:

- [ ] Clone repository at pinned commit
- [ ] Reproduce environment from lock/container file
- [ ] Download data and verify checksums
- [ ] Set all random seeds as documented
- [ ] Execute pipeline via documented entry point
- [ ] Compare output metrics/figures to reported values

## Weakest Links

| Link | Issue | Severity | Recommendation |
|------|-------|----------|----------------|
| {link} | {undocumented/gated/floating/nondeterministic} | {High/Medium/Low} | {action} |
```

---

## Pre-Diagram Checklist

Before creating the diagram, verify:

- [ ] LOADED `/autoskillit:mermaid` skill using the Skill tool
- [ ] Using ONLY classDef styles from the mermaid skill (no invented colors)
- [ ] Diagram will include a color legend table

---

## Related Skills

- `/autoskillit:make-experiment-diag` - Parent skill for lens selection
- `/autoskillit:mermaid` - MUST BE LOADED before creating diagram
- `/autoskillit:exp-lens-pipeline-integrity` - For data leakage audit
- `/autoskillit:exp-lens-variance-stability` - For result stability across seeds
