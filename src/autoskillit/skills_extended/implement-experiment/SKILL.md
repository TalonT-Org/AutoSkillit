---
name: implement-experiment
categories: [research]
description: Deploy experiment artifacts in an isolated git worktree following an approved experiment plan, with per-phase commits.
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo '[SKILL: implement-experiment] Implementing experiment...'"
          once: true
---

# Implement Experiment Skill

Implement an experiment plan in an isolated git worktree. All experiment
artifacts are created inside a single self-contained folder under `research/`.
The worktree is left intact for the orchestrator to run the experiment, test,
and merge separately.

This skill reads the experiment plan and follows its implementation phases.
The plan specifies the directory layout, what scripts to write, what data to
generate, and what environment to set up. This skill builds all of it.

## When to Use

- As the implementation step of the `research` recipe (phase 2)
- After the experiment plan has been approved via GitHub issue

## Arguments

```
/autoskillit:implement-experiment {plan_path}
```

`{plan_path}` — Absolute path to the experiment plan file (required). Scan
tokens after the skill name for the first path-like token (starts with `/`,
`./`, or `.autoskillit/`).

## Critical Constraints

**NEVER:**
- Implement without first exploring affected systems with subagents
- Implement in the main working directory (always use the worktree)
- Force push or perform destructive git operations
- Merge the worktree branch into any branch
- Delete or remove the worktree
- Run the full test suite — `pytest` with no args or targeting the entire repo
  (the orchestrator handles full test execution via test_check)
- Create experiment files outside the planned `research/` subfolder
- Execute `git merge` commands (all branch content must be applied via
  `git cherry-pick` or `git checkout <branch> -- <file>`)

**ALWAYS:**
- Create a new worktree from the current branch
- Use subagents to deeply understand the codebase context BEFORE implementing
- Use `model: "sonnet"` when spawning all subagents via the Task tool
- Follow the implementation phases from the experiment plan
- Put all experiment artifacts in one self-contained `research/` subfolder
- Commit per phase with descriptive messages
- Leave the worktree intact when done
- Write `tests/test_{script_name}.py` alongside each experiment script created in Step 4
- Run `pytest --collect-only` after creating tests to verify discovery before committing

## Context Limit Behavior

If this skill hits the Claude context limit mid-execution, the headless session
terminates with `needs_retry=true` in the tool response. The worktree remains
intact on disk with all commits made up to that point.

The orchestrator should NOT retry this skill — retrying creates a brand-new
worktree, discarding all partial progress. Instead, route to the next step
(run-experiment) which can work with whatever was committed.

## Workflow

### Step 0 — Validate Prerequisites

1. Extract and verify the plan path using **path detection**: scan the tokens
   after the skill name for the first one that starts with `/`, `./`,
   `{{AUTOSKILLIT_TEMP}}/`, or `.autoskillit/` — that token is the plan path.
   Ignore any non-path words that appear before it. If no path-like token is
   found, treat the entire argument string as pasted plan content. Verify the
   resolved file exists before proceeding.
2. Read the experiment plan. Extract:
   - The experiment directory name (`research/YYYY-MM-DD-{slug}/`)
   - The planned directory layout
   - Implementation phases
   - Environment requirements (whether an `environment.yml` is needed)
   - What scripts and artifacts to create
3. Check `git status --porcelain` — if dirty, warn user.

### Step 1 — Create Git Worktree

```bash
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
WORKTREE_NAME="research-$(date +%Y%m%d-%H%M%S)"
WORKTREE_PATH="../worktrees/${WORKTREE_NAME}"
git worktree add -b "${WORKTREE_NAME}" "${WORKTREE_PATH}"
WORKTREE_PATH="$(cd "${WORKTREE_PATH}" && pwd)"

# Record the base branch for reliable discovery:
mkdir -p "{{AUTOSKILLIT_TEMP}}/worktrees/${WORKTREE_NAME}"
echo "${CURRENT_BRANCH}" > "{{AUTOSKILLIT_TEMP}}/worktrees/${WORKTREE_NAME}/base-branch"

# Set upstream tracking if possible:
REMOTE=$(git remote get-url upstream >/dev/null 2>&1 && echo upstream || echo origin)
if ! git fetch "$REMOTE" "${CURRENT_BRANCH}" 2>/dev/null; then
    echo "NOTE: Branch '${CURRENT_BRANCH}' has no remote tracking ref on $REMOTE."
fi
if ! git -C "${WORKTREE_PATH}" branch --set-upstream-to="${REMOTE}/${CURRENT_BRANCH}" "${WORKTREE_NAME}" 2>/dev/null; then
    echo "NOTE: Could not set upstream tracking for '${WORKTREE_NAME}' → '$REMOTE/${CURRENT_BRANCH}'."
fi
```

### Step 1 (cont.) — Emit Structured Tokens Early

Immediately after the worktree is created, output these tokens so the
execution layer can capture them even if context is exhausted later:

> **IMPORTANT:** Emit the structured output tokens as **literal plain text with no
> markdown formatting on the token names**. Do not wrap token names in `**bold**`,
> `*italic*`, or any other markdown. The adjudicator performs a regex match on the
> exact token name — decorators cause match failure.

```
worktree_path = ${WORKTREE_PATH}
branch_name = ${WORKTREE_NAME}
```

### Step 2 — Deep Context Understanding (Subagents)

Before implementing anything, launch subagents (model: "sonnet") to understand
the codebase context needed for the experiment. The following are **minimum
required** — launch as many additional subagents as needed.

**Minimum subagents:**

**Subagent A — Codebase Context:**
> Understand the code areas the experiment will interact with. Identify
> APIs, data structures, functions, and modules that experiment scripts
> will need to reference or call. Report imports, interfaces, and patterns
> the scripts should follow.

**Subagent B — Compute Environment Requirements:**
> Assess the compute environment needed to execute the experiment. Determine
> what system-level tools, libraries, or runtime capabilities the experiment
> requires. If the experiment plan specifies an environment.yml, examine it —
> identify any non-standard dependencies that need system packages installed
> alongside the micromamba environment. Report what the Dockerfile or
> container setup needs to provide beyond the base micromamba image.

**Additional subagents (launch as many as needed):**
- Deeper exploration of specific code areas referenced in the plan
- Understanding specific APIs, types, or interfaces the scripts will use
- Any other codebase investigation needed to write correct experiment code

### Step 3 — Set Up Container Environment

The research worktree is isolated via Docker. All experiment code runs inside
a container built from the experiment's `environment.yml`. Nothing is installed
on the host.

**3a — Write the Dockerfile:**

Locate the `environment.yml` in the planned research directory. The YAML's
`name:` field is the `MAMBA_ENV` slug (e.g., `2026-04-13-my-experiment`).

Write `${RESEARCH_DIR}/Dockerfile` based on the canonical template at
`src/autoskillit/assets/research/Dockerfile.template` in the project root,
substituting `${MAMBA_ENV}` with the actual environment name from `environment.yml`:

```dockerfile
FROM mambaorg/micromamba:1.0-bullseye-slim
SHELL ["/bin/bash", "-c"]
ARG MAMBA_ENV="{slug}"

USER root
RUN apt-get --allow-releaseinfo-change update \
    && apt-get install -y --no-install-recommends procps git curl build-essential \
    && apt-get clean -y && rm -rf /var/lib/apt/lists/*

RUN curl -sL https://taskfile.dev/install.sh | sh -s -- -b /usr/local/bin

RUN rm -f /root/.bashrc \
    && echo "source /etc/container.bashrc" >> /etc/bash.bashrc \
    && echo "set +u" > /etc/container.bashrc \
    && echo 'eval "$(micromamba shell hook --shell=bash)"' >> /etc/container.bashrc

ENV BASH_ENV=/etc/container.bashrc
ENV ENV=/etc/container.bashrc

COPY {slug}.yaml /opt/research/env/
RUN micromamba create -f /opt/research/env/{slug}.yaml && micromamba clean -afy
RUN echo "micromamba activate {slug}" >> /etc/container.bashrc
```

**3b — Write `${RESEARCH_DIR}/Taskfile.yml`:**

```yaml
version: '3'
vars:
  SLUG: "{slug}"
  IMAGE: "research-{{.SLUG}}"
  RESEARCH_DIR:
    sh: pwd

tasks:
  build-env:
    desc: Build Docker image for this experiment
    cmds:
      - docker build --build-arg MAMBA_ENV={{.SLUG}} -t {{.IMAGE}} .
    dir: "{{.RESEARCH_DIR}}"

  run-experiment:
    desc: Run experiment inside container (volume-mounts research dir)
    cmds:
      - docker run --rm -v "{{.RESEARCH_DIR}}:/workspace" {{.IMAGE}} bash -c "cd /workspace && python scripts/run.py"
    dir: "{{.RESEARCH_DIR}}"

  test:
    desc: Run pytest test suite inside container
    cmds:
      - docker run --rm -v "{{.RESEARCH_DIR}}:/workspace" {{.IMAGE}} bash -c "cd /workspace && pytest tests/ -v"
    dir: "{{.RESEARCH_DIR}}"
```

Adjust the `run-experiment` command to match the actual entry-point script from the experiment plan.

**3c — Build the Docker image:**

```bash
cd "${RESEARCH_DIR}"
docker build --build-arg MAMBA_ENV={slug} -t "research-{slug}" .
```

Verify the build succeeds before proceeding. If the build fails due to missing
system packages, add them to the `apt-get install` layer and rebuild.

**All commands from this point must run from `${WORKTREE_PATH}`.** Use absolute
paths to avoid CWD drift across Bash tool calls.

### Step 4 — Implement Phase by Phase

Follow the implementation phases from the experiment plan. The plan specifies
what to create in each phase. Typical phases include:

1. **Directory structure and environment** — create the `research/` subfolder
   layout. If the plan specifies an `environment.yml`, create it and build
   the environment with micromamba.
2. **Data generation** — create data generation scripts, generate datasets,
   verify data properties.
3. **Experiment scripts** — create measurement, benchmark, and analysis
   scripts. Verify they compile/run.
4. **Dry run** — execute the experiment with minimal inputs to verify the
   pipeline works end-to-end.

For each phase, begin implementation immediately (no announcement):
1. Implement the changes
2. Run any verification the plan specifies
3. Commit with a descriptive message. If the project has pre-commit hooks,
   run `pre-commit run --all-files` and stage any auto-fixed files before
   each commit.

**Test creation (required alongside each script phase):**

When implementing experiment scripts in Phases 2 and 3, also create a corresponding
`tests/test_{script_name}.py` for each script:

1. Create `{WORKTREE_PATH}/research/{slug}/tests/` and `conftest.py` if not yet present
2. For each script (e.g., `analysis.py`), create `tests/test_analysis.py` covering:
   - Data loads without error and has the expected shape/type
   - Key output values fall in expected ranges (sanity checks, not exact match)
   - At least one test per public function or entry point
3. Run `pytest --collect-only {WORKTREE_PATH}/research/{slug}/tests/` to confirm
   pytest can discover all test files. Fix any import errors before committing.

The plan is the authority on what phases exist and what each phase creates.
Follow it.

### Step 5 — Copy Experiment Plan into Research Folder

Copy the experiment plan into the research folder for reference:

```bash
RESEARCH_DIR=$(ls -d "${WORKTREE_PATH}"/research/*/ 2>/dev/null | head -1)
cp "${PLAN_PATH}" "${RESEARCH_DIR}experiment-plan.md"
git -C "${WORKTREE_PATH}" add research/ && git -C "${WORKTREE_PATH}" commit -m "Add experiment plan to research folder"
```

### Step 6 — Pre-commit Checks (Conditional)

Pre-commit is only relevant when the worktree has a `.pre-commit-config.yaml`.
Research worktrees do not — skip pre-commit for them.

```bash
if [ -f "${WORKTREE_PATH}/.pre-commit-config.yaml" ]; then
    cd "${WORKTREE_PATH}" && pre-commit run --all-files
    # Fix any formatting or linting issues, then re-stage and re-commit.
else
    echo "No .pre-commit-config.yaml found — skipping pre-commit (research worktree)."
fi
```

### Step 7 — Handoff Report

Output to terminal:
- **Worktree path:** `${WORKTREE_PATH}`
- **Branch name:** `${WORKTREE_NAME}`
- **Base branch:** the branch the worktree was created from
- **Research folder:** the `research/` subfolder created inside the worktree
- **Summary:** list of implemented phases and artifacts created

Explicitly state: "Worktree left intact for orchestrator to run experiment and test."

Then emit these structured output tokens:

> **IMPORTANT:** Emit the structured output tokens as **literal plain text with no
> markdown formatting on the token names**. Do not wrap token names in `**bold**`,
> `*italic*`, or any other markdown. The adjudicator performs a regex match on the
> exact token name — decorators cause match failure.

```
worktree_path = ${WORKTREE_PATH}
branch_name = ${WORKTREE_NAME}
```

## Error Handling

- **Worktree creation fails** — check `git worktree list`, suggest `git worktree prune`
- **Environment build fails** — report the error, suggest fixes to environment.yml
- **Script creation fails** — report which phase and why, offer to fix/retry or abort.
  Do NOT clean up the worktree.
- **Pre-commit fails** — fix formatting/linting issues and re-commit
