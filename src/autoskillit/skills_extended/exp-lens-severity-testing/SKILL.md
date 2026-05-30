---
name: exp-lens-severity-testing
categories: [exp-lens]
activate_deps: [mermaid]
description: Analyze severity of experimental tests — adversarial cases, negative controls, falsification tests, easy-pass detection, and confirmatory theater. Falsificationist lens answering "Would this design have caught the error?"
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo 'Severity Testing Lens - Analyzing adversarial robustness of experimental conclusions...'"
          once: true
---

# Severity Testing Experimental Design Lens

**Philosophical Mode:** Falsificationist
**Primary Question:** "Would this design have caught the error?"
**Focus:** Adversarial Cases, Negative Controls, Falsification Tests, Easy-Pass Detection, Confirmatory Theater

## Arguments

`/autoskillit:exp-lens-severity-testing [context_path] [experiment_plan_path]`

- **context_path** (optional positional arg 1) — Absolute path to a lens context file
  containing IV/DV tables, H0/H1 hypotheses, controlled variables, and success criteria.
  If provided, read this file before beginning analysis to obtain structured context.
  If omitted, discover context by exploring the CWD.
- **experiment_plan_path** (optional positional arg 2) — Absolute path to the full
  experiment plan. If provided, read for complete experimental methodology and design.
  If omitted, locate the experiment plan by exploring the CWD.

## When to Use

- Evaluating whether positive results are meaningful or trivially achievable
- Checking for adversarial robustness of experimental conclusions
- User invokes `/autoskillit:exp-lens-severity-testing` or `/autoskillit:make-experiment-diag severity`

## Critical Constraints

**NEVER:**
- Fabricate, invent, or embellish information not supported by the available evidence or code.

- Modify any source code files
- Accept a "pass" result without asking what a false result would have looked like under this design
- Create files outside `{{AUTOSKILLIT_TEMP}}/exp-lens-severity-testing/`
- Run subagents in the background (`run_in_background: true` is prohibited)
- Issue subagent Task calls sequentially — ALL must be in a single parallel message

**ALWAYS:**
- For every positive claim, identify what error the test was capable of detecting
- Inventory negative controls and sanity checks explicitly — their absence is a finding
- Rate severity before reporting conclusions, not after
- Flag confirmatory theater: experiments designed to confirm rather than risk refutation
- BEFORE creating any diagram, LOAD the `/autoskillit:mermaid` skill using the Skill tool - this is MANDATORY
- If the Skill tool cannot be used (disable-model-invocation) or refuses this invocation, do NOT proceed with diagram creation. Abort this step and omit the diagram from output.
- Issue all Task calls in a single message to maximize parallelism
- Write output to `{{AUTOSKILLIT_TEMP}}/exp-lens-severity-testing/exp_diag_severity_testing_{YYYY-MM-DD_HHMMSS}.md`
- After writing the file, emit the structured output token as **literal plain text** with no
  markdown formatting on the token name (the adjudicator performs a regex match):

  ```
  diagram_path = /absolute/path/to/{{AUTOSKILLIT_TEMP}}/exp-lens-severity-testing/exp_diag_severity_testing_{...}.md
  ```

---

## Analysis Workflow

### Step 0: Parse optional arguments

If positional arg 1 (context_path) is provided and the file exists, read it to obtain
IV/DV tables, H0/H1 hypotheses, controlled variables, and success criteria. If positional
arg 2 (experiment_plan_path) is provided and exists, read the experiment plan for full
methodology. Use this structured context as the foundation for Steps 1-5; skip the CWD
exploration for these fields if the context file supplies them. For any field absent from the context file, perform CWD exploration for that specific field only.

### Step 1: Launch Parallel Exploration Subagents (SINGLE MESSAGE)

**Issue ALL Explore/Task subagent calls in a single message — one per item — so they execute in parallel. Do NOT iterate across multiple turns.**

Do not output any prose between subagent dispatches. Immediately proceed to the next tool call.

Spawn Explore subagents to investigate:

**Positive Results Claimed**
- Find all conclusions and positive claims in the experiment
- Look for: demonstrates, improves, outperforms, achieves, shows, confirms, validates

**Negative Controls & Sanity Checks**
- Find negative controls, baselines, and sanity check tests
- Look for: negative_control, sanity, ablation, degenerate, trivial, null, random

**Adversarial Conditions**
- Find adversarial or stress-test conditions applied
- Look for: adversarial, attack, stress, perturbation, corruption, noise, edge_case

**Alternative Explanations Tested**
- Find whether alternative explanations were examined
- Look for: alternative, confound, artifact, spurious, coincidence, luck

**Prediction Specificity**
- Find how specific the predictions were before seeing data
- Look for: prediction, hypothesis, preregistered, expected, prior

### Step 2: Assess Severity for Each Claim

For each claim:
1. What error was the test capable of detecting?
2. What would a false positive result have looked like under this design?
3. Were negative controls or sanity checks included?
4. Were adversarial conditions tested?
5. Is the test informative (would a bad result look different from a good result)?

### Step 3: Rate Severity and Identify Gaps

Severity ratings: HIGH / MEDIUM / LOW
Flag **confirmatory theater** when design is structured to confirm rather than risk refutation.

### Step 4: Create Optional Severity-Flow Diagram

Show Claims → HIGH/MEDIUM/LOW severity tests → Severity verdicts.

### Step 5: Write Output

Write the analysis to: `{{AUTOSKILLIT_TEMP}}/exp-lens-severity-testing/exp_diag_severity_testing_{YYYY-MM-DD_HHMMSS}.md` (relative to the current working directory)


## Output Template

```markdown
# Severity Testing Diagram: {Experiment Name}

**Lens:** Severity Testing (Falsificationist)
**Question:** Would this design have caught the error?
**Date:** {YYYY-MM-DD}
**Scope:** {What was analyzed}

## Severity Assessment

| Claim | Test | Negative Control? | Adversarial? | Severity | Theater? |
|-------|------|--------------------|--------------|----------|----------|
| {claim} | {test description} | {Yes/No} | {Yes/No} | {HIGH/MEDIUM/LOW} | {Yes/No} |

## Severity-Flow Diagram

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

    subgraph Claims ["CLAIMS"]
        C1["Claim<br/>━━━━━━━━━━<br/>Primary hypothesis"]
    end

    subgraph SeverityTests ["SEVERITY TESTS"]
        HIGH["HIGH Severity<br/>━━━━━━━━━━<br/>Adversarial test"]
        MED["MEDIUM Severity<br/>━━━━━━━━━━<br/>Standard test"]
        LOW["LOW Severity<br/>━━━━━━━━━━<br/>Easy-pass test"]
    end

    subgraph Verdicts ["VERDICTS"]
        PASS["Pass<br/>━━━━━━━━━━<br/>Survives scrutiny"]
        FAIL["Fail<br/>━━━━━━━━━━<br/>Insufficient severity"]
        THEATER["Theater<br/>━━━━━━━━━━<br/>Confirmatory theater"]
    end

    %% SEVERITY FLOWS %%
    C1 -->|"tested by"| HIGH
    C1 -->|"tested by"| MED
    C1 -->|"tested by"| LOW
    HIGH -->|"verdict"| PASS
    MED -->|"verdict"| PASS
    LOW -->|"verdict"| FAIL
    LOW -.->|"risk"| THEATER

    %% CLASS ASSIGNMENTS %%
    class C1 cli;
    class HIGH detector;
    class MED handler;
    class LOW gap;
    class PASS output;
    class FAIL integration;
    class THEATER phase;
```

**Color Legend:**
| Color | Category | Description |
|-------|----------|-------------|
| Dark Blue | Claims | Hypotheses under scrutiny |
| Red | High Severity | Adversarial and negative control tests |
| Orange | Medium Severity | Standard tests with moderate rigor |
| Amber | Low Severity | Easy-pass tests (potential theater) |
| Dark Teal | Pass | Claims surviving severe testing |
| Dark Red | Fail | Claims with insufficient test severity |
| Purple | Theater | Confirmatory theater detected |

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

- `/autoskillit:make-experiment-diag` - Parent skill
- `/autoskillit:mermaid` - MUST BE LOADED before creating diagram
- `/autoskillit:exp-lens-error-budget`
- `/autoskillit:exp-lens-validity-threats`
