---
name: setup-environment
categories: [research]
description: >
  Pre-flight environment gate for the research recipe. Reads the experiment
  plan, detects the required environment type, builds a Docker image or creates
  a host micromamba environment, and emits an env_mode verdict consumed by
  downstream steps.
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo '[SKILL: setup-environment] Setting up experiment environment...'"
          once: true
---

# Environment Setup

Pre-flight environment gate for the research recipe. Decouples environment
decisions (Docker vs host-micromamba vs none) from `implement-experiment`'s
inline Docker build. Reads the experiment plan to determine the required
environment type, probes Docker availability, builds a Docker image when
possible, falls back to a host micromamba environment for pure conda-forge
Python deps, and emits an `env_mode` verdict consumed by downstream steps.
PASS and WARN proceed to `decompose_phases`; FAIL escalates immediately.

## Arguments

```
/autoskillit:setup-environment <worktree_path> <experiment_plan>
```

- `worktree_path` — Absolute path to the shared research worktree (positional).
- `experiment_plan` — Absolute path to the experiment plan YAML (positional).

## When to Use

- Called by the research recipe's `setup_environment` step between `stage_data`
  and `decompose_phases`
- Whenever a pre-flight environment check is needed before experiment implementation

## Critical Constraints

**NEVER:**
- Modify the experiment plan
- Write files outside `{{AUTOSKILLIT_TEMP}}/setup-environment/`
- Skip the Docker availability probe before attempting a build
- Run subagents in the background (`run_in_background: true` is prohibited)

**ALWAYS:**
- Parse `environment.type` from the experiment plan first
- Try Docker before falling back to micromamba
- Write the environment setup report before emitting the verdict token
- Use `model: "sonnet"` for all subagents

## Workflow

### Step 0 — Parse the Experiment Plan

Read the experiment plan at `experiment_plan`. Extract:
- `environment.type` — `"standard"` or `"custom"`
- The `name:` field from the `environment.yml` embedded section (the conda
  environment slug, used as the Docker image tag and micromamba env name)

### Step 1 — Short-Circuit for Standard Environments

If `environment.type == "standard"`, no custom environment is needed:
- Set `env_mode = none`, `verdict = PASS`
- Skip Steps 2–6 and proceed directly to Step 7 (write report) then Step 8
  (emit tokens)

### Step 2 — Probe Docker Availability

Run:

```bash
docker info --format '{{.OSType}}' 2>/dev/null
```

with a 5-second timeout. If the command exits 0, Docker is available.

### Step 3 — Build Docker Image (Docker Available)

Stage the build context under `{{AUTOSKILLIT_TEMP}}/setup-environment/build/`:

```bash
mkdir -p {{AUTOSKILLIT_TEMP}}/setup-environment/build
cp src/autoskillit/assets/research/Dockerfile.template \
   {{AUTOSKILLIT_TEMP}}/setup-environment/build/Dockerfile
cp environment.yml \
   {{AUTOSKILLIT_TEMP}}/setup-environment/build/{slug}.yaml
```

The template is at `src/autoskillit/assets/research/Dockerfile.template` and
uses base image `mambaorg/micromamba:1.0-bullseye-slim`. Build:

```bash
docker build \
  --build-arg MAMBA_ENV={slug} \
  -t research-{slug} \
  {{AUTOSKILLIT_TEMP}}/setup-environment/build
```

### Step 4 — Docker Outcome Routing

- **Build success:** Set `env_mode = docker`, `verdict = PASS`. Proceed to
  Step 7 (write report).
- **Build failure OR Docker unavailable:** Continue to Step 5 (fallback
  viability assessment).

### Step 5 — Assess Micromamba Fallback Viability

Parse `environment.yml` for:

**Channels:** Only `conda-forge` and `defaults` are considered viable. Any
other channel (e.g., `bioconda`, `pytorch`) marks the environment as not
viable.

**Dependency markers (not viable):**
- CUDA deps: `cudatoolkit`, `cuda-*`, `nvidia-*`
- Bioconductor deps: `bioconductor-*`
- System packages: entries that resolve to OS-level packages (detected via
  `dpkg -l <package> 2>/dev/null` returning exit 0, or `which <package>`
  returning a hit in `/usr/bin`, `/usr/sbin`, `/bin`, `/sbin`)

If channels or deps are not viable, proceed to Step 7 with
`env_mode = unavailable`, `verdict = FAIL`.

### Step 6 — Create Host Micromamba Environment

If viable, run:

```bash
micromamba create -f environment.yml -n experiment-{slug}
```

- **Success:** Set `env_mode = micromamba-host`, `verdict = WARN`
- **Failure:** Set `env_mode = unavailable`, `verdict = FAIL`

### Step 7 — Write Environment Setup Report

Always write the report before emitting tokens, regardless of verdict:

```
{{AUTOSKILLIT_TEMP}}/setup-environment/env_setup_report_{YYYY-MM-DD_HHMMSS}.md
```

Report structure:

```markdown
## Environment Setup Report
**Date:** {timestamp}
**Environment Type:** {standard|custom}
**Verdict:** {PASS|WARN|FAIL}
**env_mode:** {none|docker|micromamba-host|unavailable}

### Docker Probe Result
{available|unavailable} — {reason}

### Build Result (if attempted)
{success|failure} — {output or error}

### Micromamba Viability Assessment (if evaluated)
- Channels: {list}
- Problematic deps: {list or "none"}
- Viability: {viable|not viable} — {reason}

### Micromamba Install Result (if attempted)
{success|failure} — {output or error}

### Rationale
{explanation of final verdict}
```

### Step 8 — Emit Structured Output Tokens

Emit structured output tokens as LITERAL PLAIN TEXT with NO markdown
formatting on the token names. Do not wrap token names in `**bold**`,
`*italic*`, or any other markdown. The adjudicator performs a regex match
on the exact token name — decorators cause match failure.

```
env_mode = {none|docker|micromamba-host|unavailable}
env_report = /absolute/path/to/env_setup_report_{YYYY-MM-DD_HHMMSS}.md
verdict = {PASS|WARN|FAIL}
```

## Output

```
env_mode = none|docker|micromamba-host|unavailable
env_report = /absolute/path/to/{{AUTOSKILLIT_TEMP}}/setup-environment/env_setup_report_{YYYY-MM-DD_HHMMSS}.md
verdict = PASS|WARN|FAIL
```
