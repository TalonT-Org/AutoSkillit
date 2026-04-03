---
name: make-experiment-diag
categories: [exp-lens]
description: Interactive selection of experimental design lens for visualizing experiment methodology. Routes to the appropriate exp-lens-* skill.
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo 'make-experiment-diag - Selecting experimental design lens...'"
          once: true
---

# Experimental Design Diagram Selection

Select the right experimental design lens for your analysis. Each lens asks one primary question about an experiment's design, surfacing assumptions and failure modes specific to that epistemic dimension.

## When to Use

- Want to visualize or audit experimental design methodology
- Need to select the right lens for a specific experimental concern
- User says `/autoskillit:make-experiment-diag`
- User wants to examine a specific aspect of an experiment

## Critical Constraints

**NEVER:**
- Skip the selection step — always confirm which lens to use
- Modify any source code or experimental artifacts
- Combine multiple lenses in a single invocation

**ALWAYS:**
- Present the selection table to help the user choose
- Match user description to the most relevant lens
- Load the selected lens skill using the Skill tool
- Follow the loaded skill's instructions exclusively; `%%ORDER_UP%%` is emitted by the delegated skill

---

## Core Lenses (always recommended)

| Your experiment involves... | Use this lens | Question it answers | Philosophical Mode |
|-----------------------------|---------------|---------------------|--------------------| 
| Unclear or shifting claims | Estimand Clarity | What exactly is the claim? | Evidential |
| Causal attribution without explicit assumptions | Causal Assumptions | What causal assumptions support this design? | Causal-Structural |
| Baseline quality or fairness | Comparator Construction | Is the comparator fair and relevant? | Counterfactual |
| Data preprocessing, splits, or feature pipelines | Pipeline Integrity | Could data handling create optimistic bias? | Integrity |
| Nondeterminism, seed sensitivity, run-to-run noise | Variance & Stability | Is the signal larger than the noise? | Stability |
| Asymmetric tuning, compute, or engineering effort | Fair Comparison | Are alternatives compared symmetrically? | Fairness |
| Reproducing results from artifacts alone | Reproducibility & Artifacts | Could an independent party reproduce this? | Transparency |
| Metric choice, proxy validity, score interpretation | Measurement Validity | Do measurements justify the interpretation? | Psychometric |
| Robustness to preprocessing or modeling choices | Sensitivity & Robustness | Which assumptions are load-bearing? | Robustness |
| Generalization claims beyond the test suite | Benchmark Representativeness | Does this generalize beyond the test bed? | Generalizability |

## Extended Lenses (enable by experiment class)

| Your experiment involves... | Use this lens | Question it answers | Philosophical Mode |
|-----------------------------|---------------|---------------------|--------------------| 
| Shared resources, network effects, spillovers | Unit & Interference | What is the unit, and can treatments spill over? | Causal-Structural |
| Power, multiplicity, sequential monitoring | Error Budget | Are error risks sized and controlled? | Statistical |
| Theory claims needing adversarial stress | Severity Testing | Would this design have caught the error? | Falsificationist |
| Assignment mechanism, blocking, stratification | Randomization & Blocking | Where does comparability come from? | Design-Structural |
| Confounds, history effects, co-interventions | Validity Threats | What alternative explanations survive? | Adversarial |
| Multi-step exploration, adaptive allocation | Iterative Learning | How does this maximize learning per cost? | Decision-Theoretic |
| Mixing discovery and confirmation in one study | Exploratory vs Confirmatory | Is this discovery or test? | Boundary |
| Deployment risk, fairness, stakeholder harm | Governance & Risk | What risks arise from acting on this result? | Governance |

---

## Workflow

### Step 1: Prompt User

Ask the user:

> What aspect of your experimental design would you like to examine?

Example prompts:
- "I want to check if my causal claims are well-supported"
- "I need to verify my data pipeline doesn't leak"
- "Are my baselines fair comparisons?"
- "Could someone reproduce my experiment?"
- "Is my benchmark representative enough?"

### Step 2: Match to Lens

Based on the user's description, match to the most appropriate lens using the selection tables above. If ambiguous, present the top 2-3 candidates and ask the user to choose.

### Step 3: Load the Selected Lens

Use the Skill tool to load the selected `/autoskillit:exp-lens-{slug}` skill.

### Step 4: Execute the Loaded Skill

The loaded lens skill takes over and runs its full analysis workflow.

---

## Alias Table

| Alias | Lens |
|-------|------|
| estimand | exp-lens-estimand-clarity |
| causal | exp-lens-causal-assumptions |
| comparator | exp-lens-comparator-construction |
| pipeline | exp-lens-pipeline-integrity |
| variance | exp-lens-variance-stability |
| fairness | exp-lens-fair-comparison |
| reproducibility | exp-lens-reproducibility-artifacts |
| measurement | exp-lens-measurement-validity |
| sensitivity | exp-lens-sensitivity-robustness |
| benchmark | exp-lens-benchmark-representativeness |
| unit | exp-lens-unit-interference |
| error | exp-lens-error-budget |
| severity | exp-lens-severity-testing |
| randomization | exp-lens-randomization-blocking |
| validity | exp-lens-validity-threats |
| iterative | exp-lens-iterative-learning |
| exploratory | exp-lens-exploratory-confirmatory |
| governance | exp-lens-governance-risk |

---

## Related Skills

- `/autoskillit:mermaid` - Shared diagram styling (loaded by individual lenses)
- `/autoskillit:make-arch-diag` - Software architecture lens counterpart
- `/autoskillit:verify-diag` - Verify diagram accuracy against codebase
