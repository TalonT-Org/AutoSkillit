---
name: process-issues
description: Execute recipe sessions batch-by-batch for triaged GitHub issues. Reads the triage-issues output manifest, processes each batch sequentially, and launches the appropriate recipe for each issue. Use when user says "process issues", "run issues", or "execute pipeline for issues".
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo 'Processing issues...'"
          once: true
---

# Process Issues Skill

Execute the appropriate implementation recipe for each triaged GitHub issue, respecting the
batch order defined by the `triage-issues` manifest. This skill is the execution counterpart
to `triage-issues` — it consumes the manifest and orchestrates the full lifecycle: claim all
issues upfront, load recipe, execute session, collect result, report.

## When to Use

- After `/autoskillit:triage-issues` has produced a manifest in `{{AUTOSKILLIT_TEMP}}/triage-issues/`
- User says "process issues", "run issues", "execute pipeline for issues"
- When a triage manifest exists and batched issues need implementation sessions launched

## Critical Constraints

**NEVER:**
- Create files outside `{{AUTOSKILLIT_TEMP}}/process-issues/` directory
- Apply `batch:N` labels to GitHub issues (batch assignments are internal — they live only
  in the manifest JSON, not on GitHub objects)
- Modify any source code files
- Reimplement recipe steps inline — always use `load_recipe` to load the recipe YAML and
  follow it as an orchestrator

**ALWAYS:**
- Process batches in ascending order: batch 1 before batch 2 before batch 3
- Use `load_recipe` to execute the recipe for each issue
- After loading a recipe via `load_recipe`, execute every step in the recipe's step
  graph in sequence. Never skip, replace, or improvise steps.
- `optional: true` means the step is skipped ONLY when its `skip_when_false` ingredient
  evaluates to false. When the ingredient is true, the step is mandatory.
- Follow `on_success`, `on_failure`, `on_result`, and `on_context_limit` routing exactly
  as declared in the recipe YAML.
- NEVER replace recipe PR steps (`prepare_pr`, `run_arch_lenses`, `compose_pr`,
  `annotate_pr_diff`, `review_pr`) with manual `run_cmd` calls such as `gh pr create`.
- Between issues: immediately begin step 1 for the next issue after completing the
  previous issue. Do not output prose status between issues — inter-issue text creates
  end_turn windows that cause stochastic session termination.
- NEVER use AskUserQuestion to confirm proceeding to the next issue or the next batch.
  Once Step 2a's initial confirmation gate passes, all subsequent issue and batch
  transitions are fully automated.
- Emit `---process-issues-result---` result block on completion (success or failure)
- Write the summary report to `{{AUTOSKILLIT_TEMP}}/process-issues/` (relative to the current working directory)
- Use `model: "sonnet"` when spawning subagents via the Task tool
- Use `gh` CLI for all GitHub operations (not raw API calls)
- Include `--force` in all `gh label create` calls

## Arguments

- Positional (optional): path to triage manifest JSON
- `--batch N` — only process batch N (default: process all batches in order)
- `--dry-run` — print the processing plan and exit without launching any recipe sessions
- `--status-updates` — append body sections to each issue at pickup and at completion
- `--merge-batch` — after each batch completes, run `analyze-prs` + `merge-pr` to merge
  the batch PRs into the integration branch before starting the next batch

## Workflow

### Step 0: Parse Arguments

Parse arguments:
- If a positional path is given, use it as the manifest path.
- `--batch N`: record the target batch number; process only that batch.
- `--dry-run`: set dry_run flag; print plan then exit after Step 2.
- `--status-updates`: set status_updates flag.
- `--merge-batch`: set merge_batch flag.

### Step 1: Locate and Read Manifest

**Locate the manifest:**

1. If a positional path argument was given, use it directly.
2. Otherwise, auto-discover the most recently modified manifest:
   ```bash
   ls -t {{AUTOSKILLIT_TEMP}}/triage-issues/triage_manifest_*.json 2>/dev/null | head -1
   ```
3. If no manifest is found, abort:
   > "No triage manifest found. Run `/autoskillit:triage-issues` first,
   > or pass the manifest path as the first argument."

**Parse the manifest JSON.** Extract:
- `batches`: ordered list; each entry has `batch` (number) and `issues` (array)
- Per issue: `number`, `title`, `recipe` (`"implementation"` or `"remediation"`)

**Derive the repository reference** for constructing issue URLs. Try in order:
1. Read `github.default_repo` from `.autoskillit/config.yaml` if present.
2. Infer from git remote:
   ```bash
   { git remote get-url upstream 2>/dev/null || git remote get-url origin; } | sed 's|.*github.com[:/]||; s|\.git$||'
   ```
   This yields `owner/repo`.

Construct each issue's URL:
```
https://github.com/{owner}/{repo}/issues/{number}
```

### Step 2: Dry Run Mode

If `--dry-run` is active, print a table:

```
Dry run — would process N issues in M batches:

BATCH  ISSUE  RECIPE           TITLE
------ ------ ---------------- ----------------------------------------
1      #42    implementation   Add user authentication
1      #43    remediation      Fix login redirect bug
2      #44    implementation   Refactor auth module
...

Total: N issues, M batches. No sessions launched.
```

Then emit the `---process-issues-result---` block with `"dry_run": true` and exit.

### Step 2a: Batch Scope Confirmation

Before executing any batch, display the full processing plan and confirm scope with the user:

```
━━━ Process Issues — Batch Scope ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
About to process {N} issues in {M} batches:

BATCH  ISSUE  RECIPE           TITLE
------ ------ ---------------- ----------------------------------------
{batch rows from manifest}

Processing mode: sequential within each batch (batches processed in order)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Proceed? [Y/n]
```

- **Y (or Enter):** Proceed to Step 2b.
- **n:** Abort. Emit `process-issues-result` with `"aborted": true` and exit cleanly.

Skip this step when `--dry-run` is active (Step 2 already prints the plan and exits) or when
`--batch N` limits scope to a single batch (show only that batch's issues).

### Step 2b: Upfront Claiming (Phase 0.5)

Before dispatching any recipe, claim all candidate issues atomically.

Initialize two tracking lists:
```
pre_claimed_urls = []   # issues we successfully claimed
completed_urls   = []   # issues whose recipe fully returned
```

Collect all issues from all batches that will be processed (respecting `--batch N` filtering).

For each issue in the collected list:
1. Call `claim_issue(issue_url=<url>)` — **no** `allow_reentry` (default `False`)
2. If `result.claimed == true`:
   - append `issue_url` to `pre_claimed_urls`
3. If `result.claimed == false`:
   - log: `"Issue #{number} skipped — already claimed by another session"`
   - the issue will be excluded from dispatch entirely

After this phase:
- `pre_claimed_urls` contains every issue for which this session holds the claim
- Issues absent from `pre_claimed_urls` are excluded from dispatch

### Step 3: Process Batches

For each batch in **ascending order** (batch 1, then batch 2, etc.):

- If `--batch N` was given, skip all batches with a different number.

**3a. Log batch header:**
```
━━━ Batch N/M ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Processing X issues:
  #42 — implementation: Add user authentication
  #43 — remediation: Fix login redirect bug
```

**3b. For each issue in the batch (process sequentially):**

1. **Check pre_claimed_urls:** If `issue_url` is NOT in `pre_claimed_urls` → skip
   (excluded by upfront claim phase — another session holds it).

2. **Optionally append pickup status to issue body** (if `--status-updates` is active):
   ```bash
   PROCESS_BODY_FILE="{{AUTOSKILLIT_TEMP}}/process-issues/status_{number}_$(date +%s).md"
   mkdir -p "$(dirname "$PROCESS_BODY_FILE")"
   gh issue view {number} --json body --jq '.body' > "$PROCESS_BODY_FILE"
   printf '\n\n---\n\n## In Progress\n\nProcessing in batch %s — recipe: `%s`\n' \
     "{N}" "{recipe}" >> "$PROCESS_BODY_FILE"
   gh issue edit {number} --body-file "$PROCESS_BODY_FILE"
   sleep 1
   ```

3. **Determine recipe name and `run_name`:**

   | `recipe` field | Recipe to load | `run_name` | PR Title Prefix |
   |---------------|----------------|------------|-----------------|
   | `implementation` | `implementation` | `feature` | `[FEATURE]` |
   | `remediation` | `remediation` | `fix` | `[FIX]` |

   These values correspond to the GitHub labels applied by `triage-issues`:
   `recipe:implementation` (new features/enhancements) and `recipe:remediation` (bugs).

   The `run_name` encodes recipe origin for the `open-pr` skill, which derives
   the PR title prefix from it by convention (see `open-pr` SKILL.md).

4. **Load the recipe:**
   ```
   load_recipe("{recipe_name}")
   ```
   This returns the recipe YAML. Read it and execute it as an orchestrator —
   follow each step in the recipe, calling the specified MCP tool with the
   specified `with:` arguments.

5. **Execute the recipe** with these ingredient values:
   - `task`: the issue title (the recipe's `make-plan` step detects `issue_url`
     and fetches full content internally)
   - `issue_url`: the constructed issue URL
   - `run_name`: `"feature"` (implementation) or `"fix"` (remediation)
   - `base_branch`: `"integration"` (or read `git rev-parse --abbrev-ref HEAD`)
   - `open_pr`: `"true"`
   - `audit`: `"true"`
   - `review_approach`: `"false"`
   - `upfront_claimed`: `"true"`        ← always set for upfront-claimed issues

   The recipe's `claim_issue` step will receive `allow_reentry=true` (via the
   `upfront_claimed` ingredient) and recognize the pre-existing label as a
   valid reentry, returning `claimed=true` to proceed normally.

6. **After recipe returns** (any outcome), append to completed_urls:
   ```
   completed_urls.append(issue_url)
   ```
   Then record the result:
   - On success path (`done` step reached): `{issue_number, recipe, status: success, pr_url}`
   - On failure path (`escalate_stop` reached): `{issue_number, recipe, status: failure, error}`

7. **Optionally append completion status to issue body** (if `--status-updates` is active):
   ```bash
   PROCESS_BODY_FILE="{{AUTOSKILLIT_TEMP}}/process-issues/status_{number}_$(date +%s).md"
   mkdir -p "$(dirname "$PROCESS_BODY_FILE")"
   gh issue view {number} --json body --jq '.body' > "$PROCESS_BODY_FILE"
   printf '\n\n---\n\n## Status\n\n%s\n' \
     "{✅ Processing complete — PR: $pr_url | ❌ Processing failed — manual intervention required}" \
     >> "$PROCESS_BODY_FILE"
   gh issue edit {number} --body-file "$PROCESS_BODY_FILE"
   sleep 1
   ```

**Fatal failure cleanup** — if any unrecoverable error occurs during recipe dispatch:
```
uncompleted = [url for url in pre_claimed_urls if url not in completed_urls]
For each url in uncompleted:
    Call release_issue(issue_url=url)
Log: "Released N upfront-claimed issues due to fatal failure"
Propagate the error
```

**3c. After all issues in batch complete** (if `--merge-batch` is active):

Run the analyze-prs → merge-pr cycle for the batch's PRs:

```
run_skill("/autoskillit:analyze-prs {base_branch}")
```

Parse the `pr_order_file` from the skill output. For each PR in the recommended
merge order:

```
run_skill("/autoskillit:merge-pr {pr_number} {complexity}")
```

Log merge results and proceed to the next batch.

**3d. Batch Clone Cleanup (always, after all batches complete):**

After all batches finish (whether or not `--merge-batch` was used), call:

```
batch_cleanup_clones()
```

This reads the shared registry at `{{AUTOSKILLIT_TEMP}}/clone-cleanup-registry.json`,
deletes all clones registered with `status=success` by the **current kitchen** (their
pipelines completed cleanly), and leaves all `status=error` clones on disk for investigation.

The call is scoped to the current kitchen's entries by default — entries registered by other
parallel orchestrator sessions are not touched.

**Operator escape hatch (recovery only):** `batch_cleanup_clones(all_owners="true")` ignores
owner scoping and deletes all success-status entries, including legacy orphan entries from
registries created before the owner field was introduced. Do not use this on the normal happy
path — it is intended for manual recovery of stale registry files only.

### Step 4: Write Summary Report

Compute timestamp: `YYYY-MM-DD_HHMMSS`.
Create `{{AUTOSKILLIT_TEMP}}/process-issues/` if it does not exist.

Write `{{AUTOSKILLIT_TEMP}}/process-issues/process_report_{ts}.md`:

```markdown
# Process Issues Report — {ts}

## Summary

| Metric | Value |
|--------|-------|
| Total issues | N |
| Successes | X |
| Failures | Y |
| Skipped (foreign claim) | Z |
| Batches processed | M |

## Results by Batch

### Batch 1

| Issue | Title | Recipe | Status | PR |
|-------|-------|--------|--------|----|
| #42 | Add user auth | implementation | success | #101 |
| #43 | Fix redirect | remediation | failure | — |

### Batch 2
...

## Failures

For each failed issue: error message captured from recipe terminal step.
```

### Step 5: Emit Result Block

Print the structured result for pipeline capture:

```
---process-issues-result---
{
    "report_path": "{{AUTOSKILLIT_TEMP}}/process-issues/process_report_{ts}.md",
    "total_issues": N,
    "successes": X,
    "failures": Y,
    "skipped": Z,
    "batch_count": M,
    "dry_run": false,
    "pr_urls": ["https://github.com/.../pull/101", ...],
    "pre_claimed": <count of pre_claimed_urls>,
    "skipped_foreign_claim": <count of issues skipped because another session owned them>
}
---end-process-issues-result---
```

Also emit the report path as a standalone structured token for recipe capture:

```
dispatch_results = {{AUTOSKILLIT_TEMP}}/process-issues/process_report_{ts}.md
```

## Output Location

```
{{AUTOSKILLIT_TEMP}}/process-issues/
  process_report_{ts}.md   # Human-readable summary (created per run)
```

## Related Skills

- **`/autoskillit:triage-issues`** — Produces the manifest that this skill consumes
- **`/autoskillit:analyze-prs`** — Used in `--merge-batch` mode
- **`/autoskillit:merge-pr`** — Used in `--merge-batch` mode
- **`/autoskillit:open-pr`** — Called by each executed recipe; derives `[FEATURE]`/`[FIX]`
  PR title prefix from the `run_name` ingredient
