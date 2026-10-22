---
name: compose-pr
write_paths:
- '{{AUTOSKILLIT_TEMP}}/compose-pr/'
uses_capabilities:
- github_api_write
categories:
- github
description: Composition executor for pull requests. ALWAYS invoke this skill when instructed to compose a PR. Do not read
  prep files or create PRs directly — use this skill first to load the composition workflow.
hooks:
  PreToolUse:
  - matcher: '*'
    hooks:
    - type: command
      command: 'echo ''[SKILL: compose-pr] Composing and opening PR...'''
      once: true
semantic_version: 1
semantic_requirements:
  logical_roles:
  - name: pr-source-reader
    purpose: read one PR source artifact and return bounded evidence
  - name: pr-synthesizer
    purpose: synthesize the overall PR summary from collected evidence
  child_spawns:
  - role: pr-source-reader
    count: 1
  - role: pr-synthesizer
    count: 1
  concurrency:
    required: true
  join:
    required: true
  evidence:
    required: true
    independent: true
  child_model_policies:
  - role: pr-synthesizer
    model_class: sonnet
---

# Compose PR

Read the PR prep file from `prepare-pr`, validate arch-lens diagrams, compose the PR body,
and create the GitHub PR via `gh pr create`.

This skill Does NOT invoke any sub-skills or slash commands. It is Part 3 of 3 in the
decomposed PR flow (prepare → run_arch_lenses → compose).

## Arguments

`/autoskillit:compose-pr {prep_path} {all_diagram_paths} {work_dir} {base_branch} [closing_issue]`

- **prep_path** — Absolute path to the PR prep file from `prepare-pr`
- **all_diagram_paths** — Quoted comma-separated absolute paths to diagram `.md` files
  (may be empty string if no lenses succeeded)
- **work_dir** — Worktree root (for deriving `feature_branch` via `git rev-parse`)
- **base_branch** — PR target branch
- **closing_issue** (optional) — GitHub issue number for `Closes #N`

Derived value: `{run_id}` is the timestamp-plus-UUID token generated once in Step 0.

## Critical Constraints

**NEVER:**
- Report a PR URL that Step 5 neither created nor verified.
- Invoke any sub-skills or slash commands
- Fail the pipeline when `gh` is not accessible — emit `pr_url = ` (empty) and exit successfully
- Create files outside `{{AUTOSKILLIT_TEMP}}/compose-pr/`
- Invent mermaid classDef colors — when embedding validated diagrams, include them verbatim.
  Using ONLY classDef styles from the mermaid skill (no invented colors).
- Detach child delegations instead of joining them (joining every child is required)
- Start independent child delegations sequentially
- Generate, fabricate, or create any diagrams or mermaid code blocks — the ONLY diagrams permitted in the PR body are those read from `all_diagram_paths` files that passed ★/● marker validation in Step 2. If no diagram paths are provided or all fail validation, the Architecture Impact section must not exist in the output.
- NEVER re-derive, override, or substitute `task_title` from any source other than the prep file's `## Title` section. The title in the prep file is authoritative — do NOT use issue metadata, branch names, or ambient context to modify it.

**ALWAYS:**
- Check `gh auth status` before attempting GitHub operations
- Emit `pr_url = <URL>` only for a PR Step 5 created or verified; emit `pr_url = ` (empty) only when Step 4 finds GitHub unavailable; emit no `pr_url` token after a Step 5 failure.
- Validate diagrams with ★ or ● markers before including them
- Degrade gracefully when `all_diagram_paths` is empty or all diagrams fail validation
  (omit Architecture Impact section; do not include a placeholder)
- Handle `no diagrams` case: when `all_diagram_paths is empty` or every path fails
  marker check, set `validated_diagrams = []` and omit the Architecture Impact section
- Start all independent child delegations before awaiting any result to maximize concurrency

## Context Limit Behavior

When context is exhausted mid-execution, temp files may be written but the PR may
not yet be created. The recipe routes to `on_context_limit` (typically
`release_issue_failure`), bypassing the normal completion protocol.

**Before emitting structured output tokens:**
1. If context runs out before Step 5 runs, emit no `pr_url` token and let the `on_context_limit` route handle the incomplete session. Once Step 5 has run, follow the Output rules.
2. Emit whatever was completed.

## Workflow

### Step 0: Parse Arguments and Initialize

Parse positional arguments:
- arg[1] = `prep_path`
- arg[2] = `all_diagram_paths` (may be empty string)
- arg[3] = `work_dir`
- arg[4] = `base_branch`
- arg[5] = `closing_issue` (optional — must agree with the prep file if set)

Derive `feature_branch` and set shell variables:
```bash
WORK_DIR=$3
FEATURE_BRANCH=$(git -C "$WORK_DIR" rev-parse --abbrev-ref HEAD)
BASE_BRANCH=$4
```

Run this read-only command once and substitute its printed value for every `{run_id}` below:

```bash
python -c 'from datetime import datetime; from uuid import uuid4; print(datetime.now().strftime("%Y-%m-%d_%H%M%S") + "_" + uuid4().hex)'
```

Write targets must be literal paths: never write through a shell variable, `$(...)`,
backticks, or `~`. Bash variables do not persist across tool calls; repeat the
model-substituted literal path in every later command.

Create temp dir (relative to the current working directory):
```bash
mkdir -p {{AUTOSKILLIT_TEMP}}/compose-pr/
```

### Step 1: Read PR Prep File

Read the file at `prep_path`. Extract:
- `task_title` (from `## Title` section)
- Plan summaries (from `## Plan Summary` — detect single vs multi-plan by presence of group headings)
- `requirements_section` (from `## Requirements` — empty if section is blank)
- `conflict_resolution_table` (from `## Conflict Resolution Table` — empty if section is blank)
- `new_files` (from `## Changed Files > ### New (★):`)
- `modified_files` (from `## Changed Files > ### Modified (●):`)
- `feature_branch` from `## Metadata` (use the git-derived value from Step 0 if set)
- `closing_issue` and `source_issue_url` from `## Metadata`

Reject a non-empty arg[5] that differs from the prep file's `closing_issue`. The
two prep fields must be either both empty or a complete pair: a positive numeric
`closing_issue` and the exact canonical
`https://github.com/{owner}/{repo}/issues/{closing_issue}` URL. Do not discover a
newer prep file or substitute issue identity from ambient state.

### Step 2: Read and Validate Diagrams

Split `all_diagram_paths` by comma; trim whitespace from each path.

For each path:
1. Read the file
2. Extract mermaid block(s)
3. Check for `★` or `●` character in the mermaid block
   - If found → add to `validated_diagrams`
   - If not found → discard silently

If `all_diagram_paths` is empty or all diagrams fail → `validated_diagrams = []`.

**HARD RULE — Do not generate, fabricate, or create diagrams.** Every mermaid block in the PR body must originate from a file in `all_diagram_paths` that passed ★/● validation above. If `validated_diagrams` is empty, omit the entire Architecture Impact section — no placeholder, no generated, fabricated, or created diagrams, no mermaid blocks of any kind.

### Step 3: Compose PR Body (SINGLE MESSAGE)

**Issue ALL backend-adapted child-delegation calls in a single message — one per item — so they execute in parallel. Do NOT iterate across multiple turns.**

Do not output any prose between subagent dispatches. Immediately proceed to the next tool call.

Write PR body to `{{AUTOSKILLIT_TEMP}}/compose-pr/pr_body_{run_id}.md`.

#### Single plan format:

```markdown
## Summary

{First paragraph of the plan's ## Summary section, or first 5 lines after the heading}

{If requirements_section is non-empty:}
## Requirements

{requirements_section}

{If conflict_resolution_table is non-empty:}
## Conflict Resolution Decisions

The following files had merge conflicts that were automatically resolved.

{conflict_resolution_table}

{## Architecture Impact — include ONLY if validated_diagrams is non-empty:}
## Architecture Impact

### {Lens Name} Diagram

```mermaid
{diagram content}
```
{## End Architecture Impact conditional}

{If closing_issue is non-empty:}
Closes {source_issue_url}

## Implementation Plan

Plan file: `{plan_path}`

🤖 Generated with [Claude Code](https://claude.com/claude-code) via AutoSkillit
<!-- autoskillit:pipeline-signature steps=prepare_pr,run_arch_lenses,compose_pr,annotate_pr_diff,review_pr -->
```

#### Multiple plans format:

```markdown
## Summary

{Synthesized overall summary — 2-3 sentences. Spawn a child under the declared `sonnet` model-class policy if needed.}

<details>
<summary>Individual Group Plans</summary>

### Group 1: {heading from plan 1}
{Summary from plan 1}

### Group 2: {heading from plan 2}
{Summary from plan 2}

</details>

{If requirements_section is non-empty:}
## Requirements

{requirements_section}

{If conflict_resolution_table is non-empty:}
## Conflict Resolution Decisions

The following files had merge conflicts that were automatically resolved.

{conflict_resolution_table}

{## Architecture Impact — include ONLY if validated_diagrams is non-empty:}
## Architecture Impact

### {Lens Name} Diagram

```mermaid
{diagram content}
```
{## End Architecture Impact conditional}

{If closing_issue is non-empty:}
Closes {source_issue_url}

## Implementation Plan

Plan files:
- `{plan_path_1}`
- `{plan_path_2}`

🤖 Generated with [Claude Code](https://claude.com/claude-code) via AutoSkillit
<!-- autoskillit:pipeline-signature steps=prepare_pr,run_arch_lenses,compose_pr,annotate_pr_diff,review_pr -->
```

After writing the body, compute SHA-256 over its exact raw bytes and write the
exclusive version-1 sibling metadata file
`{{AUTOSKILLIT_TEMP}}/compose-pr/pr_body_{run_id}.metadata.json`:

```json
{
  "schema_version": 1,
  "body_sha256": "<64 lowercase hexadecimal SHA-256 of the raw body bytes>",
  "closing_issue": 4293,
  "source_issue_url": "https://github.com/TalonT-Org/AutoSkillit/issues/4293"
}
```

Use the actual paired issue values. For the supported no-issue form, both
`closing_issue` and `source_issue_url` are JSON `null`. Do not add fields.

### Step 4: Check GitHub Availability

Run:
```bash
gh auth status 2>/dev/null
```

If exit code is non-zero:
- Log: "GitHub CLI not available or not authenticated — skipping PR creation"
- Output: `pr_url = ` (empty)
- Exit successfully

### Step 5: Create Pull Request

Step 5 succeeds only on (a) `gh pr create` exit 0 with a URL on stdout, or (b) exactly
one OPEN, same-repository PR whose head branch is `FEATURE_BRANCH`, base is `BASE_BRANCH`,
and head commit matches the work dir's local `refs/heads/$FEATURE_BRANCH`. Stderr
classification only decides stop vs. retry; it never establishes success. On exhausted
retries or terminal failures, print the final create diagnostic and return nonzero.

Immediately before the first create attempt, read `PR_CREATE_BODY` and only its
`with_suffix(".metadata.json")` sibling. Require a regular readable body, the exact
four-field version-1 schema above, a matching raw-byte digest, and—when issue-backed—the
matching issue number plus exact canonical URL in the body. Any mismatch is terminal and
must stop before `gh pr create` executes.

At the top of the same Bash invocation, assign `WORK_DIR` (arg[3]), `FEATURE_BRANCH`
(the value Step 0's `rev-parse --abbrev-ref HEAD` printed), `BASE_BRANCH` (arg[4]) and
`TASK_TITLE` (verbatim from `## Title`) as single-quoted literals, and substitute the literal
`{run_id}`. Encode every embedded apostrophe using `'\''`, for example
`TASK_TITLE='user'\''s title'`. Apply this quoting rule to all four values to preserve
their exact bytes.

Run the fence unchanged as one command. Never run its commands piecemeal or substitute
your own judgement for its classification or recovery. If a hook denies it, fix the literal
substitutions and rerun. If that cannot fix the denial, emit the pre-create failure form
with the denial text; do not claim a create attempt ran.

```bash
PR_CREATE_BODY={{AUTOSKILLIT_TEMP}}/compose-pr/pr_body_{run_id}.md
if [ -z "$WORK_DIR" ] || [ -z "$FEATURE_BRANCH" ] || [ -z "$BASE_BRANCH" ] || [ -z "$TASK_TITLE" ]; then
  printf '%s\n' 'Step 5 could not run:' 'work_dir, feature_branch, base_branch and task_title must all be non-empty.' >&2
  exit 1
fi
PR_HEAD_SHA=$(git -C "$WORK_DIR" rev-parse --verify --quiet "refs/heads/$FEATURE_BRANCH" 2>/dev/null || true)
if [ -z "$PR_HEAD_SHA" ]; then
  printf '%s\n' 'Verified PR recovery is disabled: could not resolve the local feature-branch head.' >&2
fi
PR_CREATE_ATTEMPT=1
PR_CREATE_MAX=3
PR_CREATE_STATUS=0
PR_CREATE_LAST_ATTEMPT=1
PR_URL=""
while [ "$PR_CREATE_ATTEMPT" -le "$PR_CREATE_MAX" ]; do
  case "$PR_CREATE_ATTEMPT" in
    2) sleep 1 ;;
    3) sleep 2 ;;
  esac
  gh pr create \
       --base "$BASE_BRANCH" \
       --head "$FEATURE_BRANCH" \
       --title "$TASK_TITLE" \
       --body-file "$PR_CREATE_BODY" \
       > "{{AUTOSKILLIT_TEMP}}/compose-pr/pr_stdout_{run_id}.txt" \
       2> "{{AUTOSKILLIT_TEMP}}/compose-pr/pr_stderr_{run_id}.txt"
  PR_CREATE_STATUS=$?
  PR_CREATE_LAST_ATTEMPT=$PR_CREATE_ATTEMPT
  if [ "$PR_CREATE_STATUS" -eq 0 ]; then
    PR_URL=$(grep -E '^https://github\.com/' "{{AUTOSKILLIT_TEMP}}/compose-pr/pr_stdout_{run_id}.txt" | head -1)
    if [ -n "$PR_URL" ]; then
      break
    fi
  fi
  PR_CREATE_ERR=$(cat "{{AUTOSKILLIT_TEMP}}/compose-pr/pr_stderr_{run_id}.txt")
  case "$PR_CREATE_ERR" in
    # Non-retryable creation rejection — the head has no commits relative to the base.
    *No\ commits\ between*)
      break ;;
    # Duplicate open PR for this head and base — proceed to verified recovery.
    *already\ exists:*)
      : ;;
    # Retryable — HTTP 5xx, HTTP 429, secondary rate limit, rate limit, timeout, connection reset/refused.
    # Classified before broad HTTP 4xx terminal handling so retryable rate limits are not shadowed.
    *HTTP\ 429*|*secondary\ rate\ limit*|*rate\ limit*|*HTTP\ 5*|*timeout*|*connection\ reset*|*connection\ refused*|*temporary\ DNS*|*internal\ server\ error*|*remote\ server\ error*)
      : ;;
    # Terminal — do not retry (broad HTTP 4xx / validation / required-field missing).
    *HTTP\ 4*|*validation*|*required*|*not\ found*)
      break ;;
    # Ambiguous (empty / unknown stderr) — attempt verified response-loss recovery, then retry.
    *)
      : ;;
  esac
  # PR_HEAD_SHA is git's hex object name (empty matches nothing), safe to splice into jq.
  RECOVERED=$(gh pr list --head "$FEATURE_BRANCH" --base "$BASE_BRANCH" --state open \
    --json url,headRefOid,isCrossRepository \
    --jq '[.[] | select(.headRefOid == "'"$PR_HEAD_SHA"'" and (.isCrossRepository | not))] | if length == 1 then .[0].url else empty end' \
    2>/dev/null || true)
  if [ -n "$RECOVERED" ]; then
    PR_URL="$RECOVERED"
    PR_CREATE_STATUS=0
    break
  fi
  PR_CREATE_ATTEMPT=$((PR_CREATE_ATTEMPT + 1))
done

if [ -n "$PR_URL" ]; then
  printf 'pr_url = %s\n' "$PR_URL"
  exit 0
fi

if [ "$PR_CREATE_STATUS" -eq 0 ]; then
  PR_CREATE_STATUS=1
fi
echo "gh pr create failed after ${PR_CREATE_LAST_ATTEMPT} attempt(s):" >&2
LAST_STDERR="{{AUTOSKILLIT_TEMP}}/compose-pr/pr_stderr_{run_id}.txt"
if [ -s "$LAST_STDERR" ]; then
  cat "$LAST_STDERR" >&2
else
  echo "gh pr create exited 0 but returned no PR URL after ${PR_CREATE_LAST_ATTEMPT} attempt(s)." >&2
fi
exit "$PR_CREATE_STATUS"
```

Bounded retry policy:
- `No commits between` creation rejections are non-retryable and never recovered
- `a pull request … already exists:` proceeds to verified recovery
- Maximum 3 total `gh pr create` attempts
- Backoff sleeps of 1 second before attempt 2 and 2 seconds before attempt 3
- Each attempt's stdout/stderr captured under `{{AUTOSKILLIT_TEMP}}/compose-pr/`
  so final diagnostics can print useful detail without contaminating the PR URL capture
- Retryable rate-limit patterns (`HTTP 429`, `secondary rate limit`, broad `rate limit`) are
  classified before the broad terminal `HTTP 4xx` pattern so they are never shadowed
- Retry only transient or ambiguous response-loss failures (HTTP 429, secondary rate limit,
  broad rate limit, HTTP 5xx, timeout, connection reset/refused, temporary DNS resolution,
  remote/internal server errors)
- Do not retry terminal validation or usage failures (broad HTTP 4xx, validation errors,
  missing required fields, not-found errors)

Response-loss recovery uses `gh pr list --head/--base/--state open` before sleeping and
retrying. It selects the local head commit and same repository, accepting exactly one
match after filtering. CLOSED, MERGED, different-commit, different-base, fork or ambiguous
candidates are never emitted. An unresolvable local head commit disables recovery.
Recovery remains reachable when create exits 0 with no URL, and runs for duplicate-PR
signals and transient/ambiguous failures; terminal failures never recover.

Last-attempt tracking: `PR_CREATE_LAST_ATTEMPT` is set to `$PR_CREATE_ATTEMPT` immediately
after each `gh pr create` execution for the attempt-count diagnostic. The fixed
`{{AUTOSKILLIT_TEMP}}/compose-pr/pr_stderr_{run_id}.txt` path is overwritten each
attempt, so final failure reporting reads the last attempt’s stderr after the loop.

Final status propagation: if the loop exits without a captured or recovered PR URL, the exit
status is forced nonzero even if the last raw `gh pr create` exit code was 0 (zero-exit/no-URL
is not a successful outcome). When the last attempt's stderr file is empty or absent (zero-exit
path), a concise no-URL diagnostic is printed instead. The empty-`pr_url` success path is
reserved exclusively for Step 4's `gh auth status` preflight failure.

## Output

Emit these structured output tokens (literal plain text, no markdown decoration):

> **IMPORTANT:** Emit the structured output tokens as **literal plain text with no
> markdown formatting on the token names**. Do not wrap token names in `**bold**`,
> `*italic*`, or any other markdown. Do not wrap the output block in a code fence.
> The adjudicator performs a regex match on the exact token name — decorators and
> code fences cause match failure.

On success:
```
pr_url = https://github.com/owner/repo/pull/N
%%ORDER_UP::<hex>%%
```

When `gh` is not available or not authenticated:
```
pr_url = 
%%ORDER_UP::<hex>%%
```

When the Step 5 fence exits nonzero, report its diagnostic, emit no `pr_url` token,
and still end with the completion marker; `pr_url = ` (empty) is reserved for Step 4.

```text
gh pr create failed after <N> attempt(s):
<gh pr create stderr>
%%ORDER_UP::<hex>%%
```

For an empty required base or a hook denial that prevents the command from running,
emit the pre-create diagnostic and completion marker with no `pr_url` token:

```text
Step 5 could not run:
<precondition or hook denial diagnostic>
%%ORDER_UP::<hex>%%
```
