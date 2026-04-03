---
name: exp-lens-reproducibility-artifacts
categories: [exp-lens]
description: Create Reproducibility Artifacts experimental design diagram showing run instructions, environment capture, data availability, determinism controls, and audit trail. Transparency lens answering "Could an independent party reproduce this?"
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo 'Reproducibility Artifacts Lens - Auditing reproduction chain...'"
          once: true
---

# Reproducibility Artifacts Experimental Design Lens

**Philosophical Mode:** Transparency
**Primary Question:** "Could an independent party reproduce this?"
**Focus:** Run Instructions, Environment Capture, Data Availability, Determinism Controls, Audit Trail

## When to Use

- Evaluating reproducibility of computational experiments
- Auditing artifact completeness
- Checking for undocumented dependencies
- User invokes `/autoskillit:exp-lens-reproducibility-artifacts` or `/autoskillit:make-experiment-diag reproducibility`

## Critical Constraints

**NEVER:**
- Modify any source code files
- Do not litter the codebase with useless comments, TODO markers, or explanatory annotations — the skill output and diagram speak for themselves
- Create files outside `.autoskillit/temp/exp-lens-reproducibility-artifacts/`

**ALWAYS:**
- Trace the full chain from "clone repo" to "reproduce figures"
- Classify every artifact as available/unavailable and versioned/floating
- Identify the weakest link in the reproduction chain
- Flag all silent non-determinism risks
- BEFORE creating any diagram, LOAD the `/autoskillit:mermaid` skill using the Skill tool - this is MANDATORY
- Write output to `.autoskillit/temp/exp-lens-reproducibility-artifacts/exp_diag_reproducibility_artifacts_{YYYY-MM-DD_HHMMSS}.md`
- After writing the file, emit the structured output token as **literal plain text** with no
  markdown formatting on the token name (the adjudicator performs a regex match):

  ```
  diagram_path = /absolute/path/to/.autoskillit/temp/exp-lens-reproducibility-artifacts/exp_diag_reproducibility_artifacts_{...}.md
  %%ORDER_UP%%
  ```

---

## Analysis Workflow

### Step 1: Launch Parallel Exploration Subagents

Spawn Explore subagents to investigate:

**Environment & Dependencies**
- Find dependency files, container definitions, environment setup
- Look for: requirements, Dockerfile, environment.yml, conda, pip, nix, lock

**Data Provenance**
- Find data download scripts, checksums, versioning
- Look for: download, checksum, hash, version, dvc, data_url, manifest

**Execution Entry Points**
- Find run scripts, Makefiles, workflow managers
- Look for: Makefile, run.sh, snakemake, nextflow, main, entrypoint, cli

**Random Seed & Determinism**
- Find seed setting, nondeterminism controls
- Look for: seed, random_state, deterministic, cudnn, PYTHONHASHSEED

**Output Artifacts & Logging**
- Find result storage, logging, figure generation
- Look for: save, log, output, results, figures, checkpoint, wandb, mlflow

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

Write the diagram to: `.autoskillit/temp/exp-lens-reproducibility-artifacts/exp_diag_reproducibility_artifacts_{YYYY-MM-DD_HHMMSS}.md`

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
