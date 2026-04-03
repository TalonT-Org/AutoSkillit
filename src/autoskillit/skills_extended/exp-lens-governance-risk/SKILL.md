---
name: exp-lens-governance-risk
categories: [exp-lens]
description: Create a risk register and stakeholder impact assessment for experiments with deployment implications. Governance lens answering "What risks arise from acting on this result?"
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo 'Governance Risk Lens - Analyzing deployment risks and stakeholder impacts...'"
          once: true
---

# Governance Risk Experimental Design Lens

**Philosophical Mode:** Governance
**Primary Question:** "What risks arise from acting on this result?"
**Focus:** Deployment Risks, Subgroup Harms, Monitoring Plans, Limitation Disclosure, Responsible Decision-Making

## When to Use

- AI evaluation with deployment implications
- Experiments whose results will affect real users
- Safety-relevant benchmarks
- User invokes `/autoskillit:exp-lens-governance-risk` or `/autoskillit:make-experiment-diag governance`

## Critical Constraints

**NEVER:**
- Modify any source code files
- Create files outside `.autoskillit/temp/exp-lens-governance-risk/`

**ALWAYS:**
- Identify subgroups for whom the experimental evidence may not generalize
- Assess decision sufficiency — does the experiment actually answer the deployment question?
- Treat absent limitation disclosure as a finding requiring explicit flagging
- Distinguish risks that are monitored from risks that are merely acknowledged
- BEFORE creating any diagram, LOAD the `/autoskillit:mermaid` skill using the Skill tool - this is MANDATORY
- Write output to `.autoskillit/temp/exp-lens-governance-risk/exp_diag_governance_risk_{YYYY-MM-DD_HHMMSS}.md`
- After writing the file, emit the structured output token as **literal plain text** with no
  markdown formatting on the token name (the adjudicator performs a regex match):

  ```
  diagram_path = /absolute/path/to/.autoskillit/temp/exp-lens-governance-risk/exp_diag_governance_risk_{...}.md
  %%ORDER_UP%%
  ```

---

## Analysis Workflow

### Step 1: Launch Parallel Exploration Subagents

Spawn Explore subagents to investigate:

**Intended Use & Deployment Context**
- Find intended deployment scenario and audience
- Look for: deploy, production, use_case, audience, user, stakeholder, decision

**Subgroup & Fairness Analysis**
- Find evidence of subgroup analysis or fairness evaluation
- Look for: subgroup, demographic, fairness, equity, bias, disaggregate, protected

**Harm & Risk Metrics**
- Find safety or harm metrics tracked
- Look for: harm, safety, risk, adverse, negative, side_effect, failure_mode

**Monitoring & Feedback Plans**
- Find post-deployment monitoring or feedback loops
- Look for: monitor, alert, feedback, drift, rollback, incident, threshold, canary

**Limitation Disclosure**
- Find explicit acknowledgment of limitations
- Look for: limitation, caveat, not_suitable, generalize, scope, restriction, caveat

### Step 2: Build Risk Register

For each potential action: Who is affected? What could go wrong? Severity? Likelihood? Monitoring? Evidence?
Classify by severity × likelihood.

### Step 3: Analyze Decision Sufficiency

For every deployment decision: Does the experiment provide sufficient evidence? What additional evidence is needed? Are there subgroups with insufficient evidence?

### Step 4: Create the Diagram (Optional)

**Direction:** TB. Results → Decisions → Stakeholder Impacts

### Step 5: Write Output

Write the output to: `.autoskillit/temp/exp-lens-governance-risk/exp_diag_governance_risk_{YYYY-MM-DD_HHMMSS}.md` (relative to the current working directory)

---

## Responsible Deployment Checklist

- [ ] Subgroup performance disaggregated and analyzed
- [ ] Deployment context matches experimental conditions
- [ ] Monitoring plan defined with specific thresholds
- [ ] Rollback criteria specified
- [ ] Limitations disclosed to decision-makers
- [ ] Affected communities consulted where applicable

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
- `/autoskillit:exp-lens-validity-threats`
- `/autoskillit:exp-lens-measurement-validity`
