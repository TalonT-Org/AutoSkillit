---
name: diagnose-ci
write_paths:
- '{{AUTOSKILLIT_TEMP}}/diagnose-ci/'
categories: [ci]
description: Diagnostic executor for CI failures. ALWAYS invoke this skill when instructed to diagnose CI failures. Do not fetch CI logs directly — use this skill first to load the diagnosis workflow.
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo '[SKILL: diagnose-ci] Diagnosing CI failures...'"
          once: true
---

# diagnose-ci Skill

Fetch CI logs for a failing branch, classify the failure type, and write a structured
diagnosis report under `{output_dir}`.
Called by the orchestrator on `ci_watch` failure
before routing to `resolve-failures`.

## Invocation

```
/autoskillit:diagnose-ci {branch} [run_id] [ci_failed_jobs] [workflow] [event]
```

**Positional args:**
- `branch` — the git branch whose CI run to investigate
- `run_id` (optional) — specific workflow run ID; if absent, discover from `gh run list`
- `ci_failed_jobs` (optional) — JSON array of failed job names from `wait_for_ci`, used to scope log fetching
- `workflow` (optional) — workflow filename (e.g. `tests.yml`); if provided, scopes `gh run list` to that workflow only; use `-` to skip
- `event` (optional) — GitHub Actions trigger event (e.g. `push`, `pull_request`); if provided, scopes `gh run list` to that event only; use `-` to skip

For structured recipe invocations, `-` is the explicit vacancy value for both
`ci_failed_jobs` and `event`; treat it exactly as that optional filter being absent.

Workflow values:
- `{output_dir}` is the literal directory printed in Step 5 and pasted into its later
  `mkdir` call; it is not a positional argument.
- `{diagnosis_run_id}` is the timestamp-and-UUID filename identifier generated in
  Step 5. It is distinct from `{run_id}`, the optional CI workflow run ID.

## Critical Constraints

**NEVER:**
- Fabricate, invent, or embellish information not supported by the available evidence or code.

- Modify any source code files
- Run the test suite
- Write files outside `${AUTOSKILLIT_ALLOWED_WRITE_PREFIX:-{{AUTOSKILLIT_TEMP}}/diagnose-ci}`
- Block on missing `gh` CLI — write a minimal `failure_type=unknown` diagnosis instead

**ALWAYS:**
- Write the diagnosis file before emitting output tokens
- Emit the four output tokens (`diagnosis_path`, `failure_type`, `failure_subtype`, `is_fixable`) at the end of the response on their own lines

## Context Limit Behavior

When context is exhausted mid-execution, the diagnosis file may be partially written
or absent. The recipe routes to `on_context_limit: resolve_ci`, which proceeds
best-effort with whatever diagnosis was written (or none).

**Before emitting structured output tokens:**
1. If the diagnosis file was not fully written, emit `diagnosis_path = ` (empty)
2. Emit `failure_type = unknown`, `failure_subtype = unknown`, `is_fixable = false`
   as fallback values when analysis was interrupted

## Workflow

### Step 1: Discover Run ID (if not provided)

If `run_id` is not provided as an argument (or is `-`), construct the `gh run list` command
with any provided filters:

```bash
# Base command (always used):
gh run list --branch {branch} --limit 1 --json databaseId,status,conclusion

# If workflow is provided and is not `-`, add:
  --workflow {workflow}

# If event is provided and is not `-`, add:
  --event {event}
```

Example with both filters:
```bash
gh run list --branch {branch} --workflow {workflow} --event {event} --limit 1 --json databaseId,status,conclusion
```

Parse the JSON to extract `databaseId` as `run_id`.

When `gh` is not accessible or the command fails, proceed to Step 5 (write diagnosis report).

### Step 2: Fetch Failure Summary

```bash
gh run view {run_id} --log-failed
```
Capture the output (stdout). This is the primary failure log.

### Step 3: Fetch Per-Job Logs

For each failing job in `ci_failed_jobs` (or all failed jobs from `gh run view` if not provided):
```bash
gh api repos/{owner}/{repo}/actions/runs/{run_id}/jobs
```
For each failed job, fetch last 200 lines of logs via:
```bash
gh api repos/{owner}/{repo}/actions/jobs/{job_id}/logs
```

Use `gh repo view --json nameWithOwner` to resolve `{owner}/{repo}` if needed.

### Step 4: Classify Failure

Analyze the log output to classify `failure_type` as one of:
- `test` — pytest/jest/unit test failures
- `lint` — ruff, flake8, eslint, or formatting failures
- `build` — compilation or build errors
- `type_check` — mypy, pyright, or TypeScript type errors
- `env` — missing environment variables, secrets, or infrastructure issues
- `unknown` — cannot determine from logs
- `no_failure` — all CI jobs passing; no failure detected

#### Step 4a: Subtype Classification

After determining `failure_type`, classify `failure_subtype` using the following error-pattern decision tree (first match wins):

| Pattern match in log output | `failure_subtype` |
|---|---|
| `TimeoutError`, `deadline exceeded`, `flake`, `intermittent` | `timing_race` |
| `pytest.*FLAKY`, `rerun`, three-or-more identical test runs | `flaky` |
| `ImportError`, `ModuleNotFoundError` | `import` |
| `fixture`, `pytest.fixture`, collection error | `fixture` |
| `environ`, missing `ENV_VAR`, `KeyError.*environ` | `env` |
| Assertion error with stable stack trace (same file/line across runs) | `deterministic` |
| No pattern matched | `unknown` |
| All CI jobs passing / no failure detected | `no_failure` |

**Guidance for `resolve-failures`:** Include a supplementary "Suggested Starting Verdict" field in the
Recommended Fix Approach section that maps the subtype to an initial verdict for `resolve-failures` to
consider (not authoritative — `resolve-failures` makes its own verdict decision from the subtype +
local test result):

| `failure_subtype` | Suggested starting verdict |
|---|---|
| `flaky` or `timing_race` | `flake_suspected` |
| `deterministic` | `ci_only_failure` (if local tests pass) or `real_fix` (if fixable locally) |
| `fixture` or `import` | `flake_suspected` (if local tests pass) |
| `env` | `flake_suspected` |
| `unknown` | `flake_suspected` |

Determine `is_fixable`:
- `true` for `test`, `lint`, `build`, `type_check`
- `false` for `env`, `unknown`, `no_failure`

### Step 5: Write Diagnosis Report

Resolve the recipe-scoped output directory:

```bash
printf '%s\n' "${AUTOSKILLIT_ALLOWED_WRITE_PREFIX:-{{AUTOSKILLIT_TEMP}}/diagnose-ci}"
```

Paste the printed path as `{output_dir}` in every write below. Generate one
timestamp-and-UUID identifier with the read-only command below and use it as `{diagnosis_run_id}` for the filename.
Keep the supplied `{run_id}` for the CI workflow.

```bash
python -c 'from datetime import datetime; from uuid import uuid4; print(datetime.now().strftime("%Y-%m-%d_%H%M%S") + "_" + uuid4().hex)'
```

In a separate tool call after capturing the literal path:

```bash
mkdir -p "{output_dir}"
```

Write the diagnosis file:

```markdown
# CI Diagnosis: {branch}

**Run ID:** {run_id}
**Failure Type:** {failure_type}
**Failure Subtype:** {failure_subtype}
**Is Fixable:** {is_fixable}
**Branch:** {branch}

## Structured Output (machine-readable)

failure_subtype = {failure_subtype}

## Log Excerpt

```
{first 200 lines of failure log}
```

## Recommended Fix Approach

{1-3 sentences describing how resolve-failures should approach this}

**Suggested Starting Verdict:** {suggested starting verdict from the subtype table above}
```

Save to `{output_dir}/diagnosis_{diagnosis_run_id}.md`.

### Step 6: Emit Output Tokens

Emit these tokens on their own lines at the end of your response:

> **IMPORTANT:** Emit the structured output tokens as **literal plain text with no
> markdown formatting on the token names**. Do not wrap token names in `**bold**`,
> `*italic*`, or any other markdown. Do not wrap the output block in a code fence.
> The adjudicator performs a regex match on the exact token name — decorators and
> code fences cause match failure.

```
diagnosis_path = {output_dir}/diagnosis_{diagnosis_run_id}.md
failure_type = test|lint|build|type_check|env|unknown|no_failure
failure_subtype = flaky|timing_race|deterministic|fixture|import|env|unknown|no_failure
is_fixable = true|false
```

## gh Unavailable Fallback

When `gh` is not accessible at any step, write a minimal diagnosis:
- `failure_type = unknown`
- `failure_subtype = unknown`
- `is_fixable = false`
- Diagnosis body: "gh CLI unavailable — logs could not be fetched. Manual inspection required."

Then emit the output tokens and exit.
