---
name: review-pr
categories: [github]
uses_capabilities: [agent_model, agent_subagent, github_api_write]
description: Automated diff-scoped PR code review using parallel audit subagents. Posts inline GitHub review comments and submits a summary verdict. Use after a PR is opened to gate CI on review approval.
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo '[SKILL: review-pr] Reviewing pull request...'"
          once: true
---

# Review PR Skill

Perform an automated, diff-scoped code review on an open GitHub PR using parallel
audit subagents. Posts inline review comments and submits a summary verdict. Called
by the recipe pipeline after `open_pr_step` opens the PR.

## Arguments

`/autoskillit:review-pr <feature-branch> <base-branch> [annotated_diff_path=<path>] [hunk_ranges_path=<path>] [valid_lines_path=<path>] [diff_metrics_path=<path>] [mode=<local|github>]`

- **feature-branch** — The feature branch containing the changes to review
- **base-branch** — The base branch the PR targets (e.g., "main")
- **annotated_diff_path** (optional) — absolute path to a pre-computed annotated diff file (produced by `annotate_pr_diff` run_python step). When provided and present, read from file instead of running python3.
- **hunk_ranges_path** (optional) — absolute path to a pre-computed hunk ranges JSON file (produced by `annotate_pr_diff` run_python step). When provided and present, read from file instead of running python3.
- **valid_lines_path** (optional) — absolute path to a pre-computed valid lines JSON file (produced by `annotate_pr_diff` run_python step). Contains exact `{filepath: [line_numbers]}` set of new-file line numbers present in the diff. When provided, enables exact set-membership validation in Step 4 instead of hunk-span interval checking.
- **diff_metrics_path** (optional) — absolute path to a pre-computed diff metrics JSON file (produced by `annotate_pr_diff` run_python step). Contains `dispatch_agents` list that determines which audit dimensions to spawn. When absent, all 6 standard agents are dispatched.
- **mode** (optional, default: `github`) — Controls where findings are written:
  - `mode=github` (or absent/unrecognized): current behavior — post findings as GitHub inline review comments via the GitHub Reviews API.
  - `mode=local`: write findings to a local JSON file instead of posting to GitHub. Skips all GitHub API calls for comment posting. Still writes `diff_context_{pr_number}.json`, `raw_findings_{pr_number}.json`, and `summary_{pr_number}_{timestamp}.md` as normal. Gate tokens (`%%REVIEW_GATE::*%%`) are emitted identically in both modes.

## When to Use

- Called by the recipe orchestrator via `run_skill` after `open_pr_step`
- Can be invoked standalone to review any open PR

## Critical Constraints

**NEVER:**
- Fabricate, invent, or embellish information not supported by the available evidence or code.

- Create files outside `{{AUTOSKILLIT_TEMP}}/review-pr/`
- Approve a PR that has `changes_requested` findings
- Post review comments when `gh` is unavailable — output `verdict=needs_human` and exit 0
- Let standard or deletion agents read outside the supplied PR diff content
- Modify any source code
- Run subagents in the background (`run_in_background: true` is prohibited)
- Issue subagent Task calls sequentially — ALL must be in a single parallel message
- Specify `subagent_type` for standard or deletion audit agents. The only permitted
  registered calls are the exact reachability and abstraction-surface calls in Step 3.
- Give standard or deletion agents repository-read access. Only the two registered
  proof-only auditors may use `Read`, `Grep`, and `Glob`, and only under
  `REVIEW_CHECKOUT_ROOT`.
- Embed diff content inline in standard or deletion subagent prompts; those ephemeral
  agents continue to consume the annotated artifact path.
- Pass an experimental auditor only artifact paths or a narrative. Both registered calls
  must receive the actual annotated `[LNNN]` content and exact valid-line authority.

**ALWAYS:**
- Find the PR by feature branch at invocation time (not from a pre-captured URL)
- Output `verdict=` on the final line
- Exit 0 in all normal cases; verdict drives recipe routing via on_result, not exit code
- Exit non-zero only for unrecoverable errors (e.g., gh CLI truly unavailable after graceful degradation has already output verdict=needs_human)
- Tag the authenticated GitHub user (`gh api user -q .login`) in escalation comments (`needs_human` verdict) — omit the mention silently if username derivation fails
- Spawn all subagents via `Agent(model="sonnet")`
- Bind the checkout root, refs, exact diff, manifest generation, agent working directory,
  parent evidence reads, and every effect to the same metrics authority
- Revalidate checkout/live refs and the byte-identical metrics marker immediately before
  verdict, artifact handoff, or GitHub mutation
- Deduplicate findings by (file, line) pairs before posting
- Issue all Task calls in a single message to maximize parallelism
- Publish every fixed-name file through a same-directory `mktemp` path and atomic
  `mv`; a redirect may target only that temporary path, never the fixed destination.
  Never `open(path, 'w')` or `.write_text()` inside a `python3` heredoc or
  `python3 -c` invocation, as these are blocked by the write guard

## Workflow

### Step 0: Validate Arguments

Resolve the output directory from the environment:
```bash
REVIEW_OUTPUT_DIR="${AUTOSKILLIT_ALLOWED_WRITE_PREFIX:-{{AUTOSKILLIT_TEMP}}/review-pr/}"
```

All file writes in this skill MUST target `${REVIEW_OUTPUT_DIR}`.

Parse two positional arguments: `feature_branch` and `base_branch`.

Derive the escalation username for `needs_human` verdicts:

```bash
escalation_user=$(gh api user -q .login 2>/dev/null || echo "")
```

If `escalation_user` is non-empty, set `escalation_user_mention="@${escalation_user}"`.
If empty (gh unavailable or not authenticated), set `escalation_user_mention=""`.

Parse the optional `mode` keyword argument:

```bash
# Extract mode from keyword arguments
MODE="github"
for arg in "$@"; do
    case "$arg" in
        mode=local)  MODE="local" ;;
        mode=github) MODE="github" ;;
    esac
done
```

If `mode` is absent or unrecognized, default to `"github"`. The mode controls where
findings are written — `mode=local` skips all GitHub API posting and writes to a local
JSON file instead.

### Step 1: Find the Open PR

```bash
gh pr list --head "$feature_branch" --base "$base_branch" \
  --json number,url -q '.[0] | "\(.number) \(.url)"'
```

If `gh` is unavailable or not authenticated, or no PR is found:
- Log "No PR found or gh unavailable — skipping review"
- Output `verdict=needs_human`
- Output `%%REVIEW_GATE::CLEAR%%`
- Exit 0 (graceful degradation)

### Step 1.5: Fetch Prior Review Thread Context

This step is always executed when a PR is found. It builds prior-thread context for
suppressing already-resolved findings on re-reviews and for focusing subagents on
known-unresolved items.

Fetch all review threads using cursor-based pagination (same GraphQL query as
resolve-review Step 2, but also fetching `comments(first:5)` to see the original
finding and up to 4 replies):

```graphql
query($owner:String!, $repo:String!, $number:Int!, $after:String) {
  repository(owner:$owner, name:$repo) {
    pullRequest(number:$number) {
      reviewThreads(first:100, after:$after) {
        pageInfo { hasNextPage endCursor }
        nodes {
          isResolved
          path
          line
          originalLine
          comments(first:5) {
            nodes { databaseId body author { login } }
          }
        }
      }
    }
  }
}
```

```bash
# Fetch all pages; repeat with after=$endCursor while hasNextPage is true
gh api graphql \
  -f query='query($owner:String!,$repo:String!,$number:Int!,$after:String){repository(owner:$owner,name:$repo){pullRequest(number:$number){reviewThreads(first:100,after:$after){pageInfo{hasNextPage endCursor}nodes{isResolved path line originalLine comments(first:5){nodes{databaseId body author { login }}}}}}}}' \
  -F owner="$OWNER" \
  -F repo="$REPO" \
  -F number=$PR_NUMBER \
  -f after=null
```

Build two lists from the thread nodes. Do not output prose between iterations. For each thread, resolve line via:
`line = thread.get("line") or thread.get("originalLine")` — `line` is nullable for
outdated threads where new commits have shifted the diff anchor; `originalLine` is
the stable fallback.

If both `line` and `originalLine` are null (file-level comment thread from a prior review),
skip this thread — do not add it to `prior_resolved_findings` or `prior_unresolved_findings`.
File-level threads have no line anchor and must not suppress line-anchored findings via the
±5 proximity match.

**`prior_resolved_findings`** — threads meeting EITHER condition, AND where the first comment body
contains `[critical]` or `[warning]` (autoskillit-posted finding):
- `isResolved=true` (ACCEPT/REJECT findings resolved by resolve-review), OR
- Any reply comment (`comments[1:]`) contains `<!-- autoskillit:resolved` (DISCUSS/INFO findings
  acknowledged by resolve-review but intentionally left unresolved)

Check for the marker using:
```python
RESOLVED_MARKER_RE = re.compile(r"<!--\s*autoskillit:resolved\b")

has_marker_reply = any(
    RESOLVED_MARKER_RE.search(c.get("body", ""))
    for c in thread_comments[1:]
)

if thread.get("isResolved") or has_marker_reply:
    prior_resolved_findings.append({"file": path, "line": line, "body": first_body})
else:
    prior_unresolved_findings.append({"file": path, "line": line, "body": first_body})
```

```json
[{"file": "src/foo.py", "line": 42, "body": "[critical] arch: ..."}]
```

**`prior_unresolved_findings`** — threads where `isResolved=false` AND no reply contains the
`<!-- autoskillit:resolved` marker AND the first comment contains `[critical]` or `[warning]`:
```json
[{"file": "src/bar.py", "line": 17, "body": "[warning] tests: ..."}]
```

Save to: `${REVIEW_OUTPUT_DIR}prior_threads_{pr_number}.json`

Render with `jq -n` into a same-directory `mktemp` path, then atomically `mv` that
temporary over the fixed destination. If using the Write tool, write the temporary
path first and rename it. Never redirect directly to the fixed destination. Do not
use inline Python one-liners or heredoc scripts with `open()` — these are blocked by
the sandbox.

If the GraphQL call fails (token scope, network): set both lists to `[]` and log a warning.
Prior-thread context is best-effort — failure must not abort the review.

### Step 2: Get PR Diff and Metadata

```bash
# Get the PR diff
gh pr diff {pr_number}

# Get owner/repo
gh repo view --json nameWithOwner -q .nameWithOwner
```

Save the diff to `${REVIEW_OUTPUT_DIR}diff_{pr_number}.txt`. (relative to the current working directory)

### Step 2.7: Deterministic Diff Annotation

Treat `metrics_{pr_number}.json` as the commit marker for one immutable annotation
generation. The gate has three states: `valid_true`, `valid_false`, or `degraded`.
Freshness and the complete artifact manifest MUST validate before consuming the
gate boolean. The review LLM never counts lines or infers eligibility.

```bash
REVIEW_CHECKOUT_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
METRICS_HEAD_SHA=""
METRICS_BASE_SHA=""
METRICS_MERGE_BASE_SHA=""
CHECKOUT_HEAD_SHA=""
CHECKOUT_BASE_SHA=""
CHECKOUT_MERGE_BASE_SHA=""
LIVE_REFS=""
LIVE_HEAD_SHA=""
LIVE_BASE_SHA=""
DIFF_SHA256=""
PROFILE_ID=""
ANNOTATION_GENERATION_ID=""
METRICS_MARKER_BEFORE=""
METRICS_MARKER_AFTER=""
ARTIFACT_SNAPSHOT_DIR=""
ANNOTATED_DIFF_SNAPSHOT_PATH=""
HUNK_RANGES_SNAPSHOT_PATH=""
VALID_LINES_SNAPSHOT_PATH=""
ANNOTATED_DIFF=""
VALID_LINE_RANGES="{}"
VALID_DIFF_LINES=""
GATE_STATE=degraded
GATE_REASON_CODE=metrics_missing
GATE_FAILED=false
GATE_AUTHORITY='{"state":"degraded","reason_code":"metrics_missing","snapshot":{},"annotation_generation_id":""}'
STANDARD_RAW_FINDINGS='[]'
EXPERIMENTAL_CANDIDATES='[]'
EXPERIMENTAL_AUDIT_STATE=not_eligible
AUDITOR_STATUS_BY_NAME='{"pr-review-auditor-reachability":{"status":"not_started","reason_code":"not_eligible"},"pr-review-auditor-abstraction-surface":{"status":"not_started","reason_code":"not_eligible"}}'
FINAL_SNAPSHOT_STATE=authority_degraded
COMMIT_ID=""
HTTP_STATUS=""
BATCH_RESPONSE_TMP=""
POSTED_REVIEW_ID=""
RECEIPT_DOCUMENT=""
STALE_REVIEW_COMPENSATION_FAILED=false

# Closed degraded reason codes:
# metrics_missing, metrics_invalid_json, manifest_missing, manifest_invalid,
# profile_invalid, ref_missing, snapshot_mismatch, artifact_missing,
# artifact_name_mismatch, artifact_length_mismatch, artifact_digest_mismatch,
# marker_changed, gate_missing, gate_not_boolean.
degrade_gate() {
    if [ "$GATE_FAILED" = false ]; then
        GATE_STATE=degraded
        GATE_REASON_CODE="$1"
        GATE_FAILED=true
    fi
    ANNOTATED_DIFF=""
    VALID_LINE_RANGES="{}"
    VALID_DIFF_LINES=""
}

if [ -z "$REVIEW_CHECKOUT_ROOT" ] || [ ! -d "$REVIEW_CHECKOUT_ROOT" ]; then
    degrade_gate ref_missing
elif [ -z "${diff_metrics_path:-}" ] || [ ! -f "$diff_metrics_path" ]; then
    degrade_gate metrics_missing
else
    # Retain one candidate generation in invocation-scoped files. Each cp reads
    # through one open descriptor, so atomic publisher replacement can select an
    # old or new complete file but cannot create mixed bytes within a retained file.
    ARTIFACT_SNAPSHOT_DIR="$(mktemp -d "${REVIEW_OUTPUT_DIR%/}/gate_snapshot.XXXXXX")" ||
        degrade_gate artifact_missing
    METRICS_MARKER_BEFORE="${ARTIFACT_SNAPSHOT_DIR}/metrics.before"
    METRICS_MARKER_AFTER="${ARTIFACT_SNAPSHOT_DIR}/metrics.after"
    if [ "$GATE_FAILED" = false ] &&
       ! cp -- "$diff_metrics_path" "$METRICS_MARKER_BEFORE"; then
        degrade_gate metrics_missing
    fi

    if [ "$GATE_FAILED" = false ] &&
       ! jq -e 'type == "object"' < "$METRICS_MARKER_BEFORE" >/dev/null; then
        degrade_gate metrics_invalid_json
    elif [ "$GATE_FAILED" = false ] &&
         ! jq -e '
        has("_head_sha") and
        has("_base_sha") and
        has("generation_id") and
        has("diff_sha256") and
        has("diff_byte_length") and
        has("diff_source") and
        has("artifacts") and
        (.artifacts | has("annotated_diff") and has("hunk_ranges") and has("valid_lines"))
    ' < "$METRICS_MARKER_BEFORE" >/dev/null; then
        degrade_gate manifest_missing
    elif [ "$GATE_FAILED" = false ] &&
         ! jq -e '
        (._head_sha | type == "string" and length > 0) and
        (._base_sha | type == "string" and length > 0) and
        (.generation_id | type == "string" and length > 0) and
        (.diff_sha256 | type == "string" and length == 64) and
        (.diff_byte_length | type == "number" and . >= 0 and floor == .) and
        (.diff_source | type == "object") and
        (.artifacts | type == "object") and
        (.artifacts.annotated_diff | type == "object") and
        (.artifacts.hunk_ranges | type == "object") and
        (.artifacts.valid_lines | type == "object")
    ' < "$METRICS_MARKER_BEFORE" >/dev/null; then
        degrade_gate manifest_invalid
    fi

    if [ "$GATE_FAILED" = false ]; then
        METRICS_HEAD_SHA="$(jq -r '._head_sha' < "$METRICS_MARKER_BEFORE")"
        METRICS_BASE_SHA="$(jq -r '._base_sha' < "$METRICS_MARKER_BEFORE")"
        METRICS_MERGE_BASE_SHA="$(jq -r '._merge_base_sha // ""' < "$METRICS_MARKER_BEFORE")"
        ANNOTATION_GENERATION_ID="$(jq -r '.generation_id' < "$METRICS_MARKER_BEFORE")"
        DIFF_SHA256="$(jq -r '.diff_sha256' < "$METRICS_MARKER_BEFORE")"
        PROFILE_ID="$(jq -r '.diff_source.profile_id // ""' < "$METRICS_MARKER_BEFORE")"

        # Validate the closed source/profile object before any gate read.
        if [ "$MODE" = "local" ]; then
            jq -e '
              .review_mode == "local" and .diff_source == {
                "comparison":"merge_base_to_head","context_lines":3,
                "external_diff":false,"kind":"local_git",
                "profile_id":"local_git_pinned_v1","rename_detection":"50%",
                "text_conversion":false
              }' < "$METRICS_MARKER_BEFORE" >/dev/null || degrade_gate profile_invalid
        else
            jq -e '
              .review_mode == "github" and .diff_source == {
                "comparison":"pull_request","context_lines":3,
                "external_diff":false,"kind":"github_pr",
                "profile_id":"github_pr_diff_v1","rename_detection":"provider_default",
                "text_conversion":false
              }' < "$METRICS_MARKER_BEFORE" >/dev/null || degrade_gate profile_invalid
        fi

        CHECKOUT_HEAD_SHA="$(git -C "$REVIEW_CHECKOUT_ROOT" rev-parse HEAD 2>/dev/null || true)"
        if [ -z "$CHECKOUT_HEAD_SHA" ] || [ "$CHECKOUT_HEAD_SHA" != "$METRICS_HEAD_SHA" ]; then
            degrade_gate snapshot_mismatch
        elif [ "$MODE" = "local" ]; then
            CHECKOUT_BASE_SHA="$(git -C "$REVIEW_CHECKOUT_ROOT" rev-parse "${base_branch}" 2>/dev/null || true)"
            CHECKOUT_MERGE_BASE_SHA="$(git -C "$REVIEW_CHECKOUT_ROOT" merge-base "$CHECKOUT_BASE_SHA" "$CHECKOUT_HEAD_SHA" 2>/dev/null || true)"
            if [ -z "$CHECKOUT_BASE_SHA" ] || [ -z "$CHECKOUT_MERGE_BASE_SHA" ]; then
                degrade_gate ref_missing
            elif [ "$CHECKOUT_BASE_SHA" != "$METRICS_BASE_SHA" ] ||
                 [ "$CHECKOUT_MERGE_BASE_SHA" != "$METRICS_MERGE_BASE_SHA" ]; then
                degrade_gate snapshot_mismatch
            fi
        else
            LIVE_REFS="$(
              gh api "repos/{owner}/{repo}/pulls/${pr_number}" \
                --jq '{headRefOid:.head.sha,baseRefOid:.base.sha}' 2>/dev/null || true
            )"
            LIVE_HEAD_SHA="$(printf '%s' "$LIVE_REFS" | jq -r '.headRefOid // ""' 2>/dev/null)"
            LIVE_BASE_SHA="$(printf '%s' "$LIVE_REFS" | jq -r '.baseRefOid // ""' 2>/dev/null)"
            if [ -z "$LIVE_HEAD_SHA" ] || [ -z "$LIVE_BASE_SHA" ]; then
                degrade_gate ref_missing
            elif [ "$LIVE_HEAD_SHA" != "$METRICS_HEAD_SHA" ] ||
                 [ "$LIVE_BASE_SHA" != "$METRICS_BASE_SHA" ]; then
                degrade_gate snapshot_mismatch
            fi
        fi

        # Verify fixed path names, byte lengths, and SHA-256 digests.
        for artifact_key in annotated_diff hunk_ranges valid_lines; do
            case "$artifact_key" in
              annotated_diff)
                artifact_path="${annotated_diff_path:-}"
                retained_path="${ARTIFACT_SNAPSHOT_DIR}/annotated_diff"
                ANNOTATED_DIFF_SNAPSHOT_PATH="$retained_path"
                ;;
              hunk_ranges)
                artifact_path="${hunk_ranges_path:-}"
                retained_path="${ARTIFACT_SNAPSHOT_DIR}/hunk_ranges"
                HUNK_RANGES_SNAPSHOT_PATH="$retained_path"
                ;;
              valid_lines)
                artifact_path="${valid_lines_path:-}"
                retained_path="${ARTIFACT_SNAPSHOT_DIR}/valid_lines"
                VALID_LINES_SNAPSHOT_PATH="$retained_path"
                ;;
            esac
            expected_name="$(jq -r ".artifacts.${artifact_key}.basename // \"\"" < "$METRICS_MARKER_BEFORE")"
            expected_length="$(jq -r ".artifacts.${artifact_key}.byte_length // \"\"" < "$METRICS_MARKER_BEFORE")"
            expected_digest="$(jq -r ".artifacts.${artifact_key}.sha256 // \"\"" < "$METRICS_MARKER_BEFORE")"
            if [ -z "$artifact_path" ] || [ ! -f "$artifact_path" ]; then
                degrade_gate artifact_missing
            elif [ "$(basename "$artifact_path")" != "$expected_name" ]; then
                degrade_gate artifact_name_mismatch
            elif ! cp -- "$artifact_path" "$retained_path"; then
                degrade_gate artifact_missing
            elif [ "$(wc -c < "$retained_path" | tr -d ' ')" != "$expected_length" ]; then
                degrade_gate artifact_length_mismatch
            elif [ "$(sha256sum "$retained_path" | cut -d' ' -f1)" != "$expected_digest" ]; then
                degrade_gate artifact_digest_mismatch
            fi
        done

        if ! cp -- "$diff_metrics_path" "$METRICS_MARKER_AFTER" ||
           ! cmp -s "$METRICS_MARKER_BEFORE" "$METRICS_MARKER_AFTER"; then
            degrade_gate marker_changed
        elif ! jq -e 'has("run_overengineering_audits")' < "$METRICS_MARKER_BEFORE" >/dev/null; then
            degrade_gate gate_missing
        elif ! jq -e '.run_overengineering_audits | type == "boolean"' < "$METRICS_MARKER_BEFORE" >/dev/null; then
            degrade_gate gate_not_boolean
        elif [ "$GATE_FAILED" = true ]; then
            : # Retain the first deterministic validation failure.
        elif [ "$(jq -r '.run_overengineering_audits' < "$METRICS_MARKER_BEFORE")" = true ]; then
            GATE_STATE=valid_true
            GATE_REASON_CODE=none
            EXPERIMENTAL_AUDIT_STATE=pending
        else
            GATE_STATE=valid_false
            GATE_REASON_CODE=none
            EXPERIMENTAL_AUDIT_STATE=not_required
        fi

        if [ "$GATE_STATE" = valid_true ] || [ "$GATE_STATE" = valid_false ]; then
            # Consume only the retained, digest-validated sidecars. Keep these files
            # for final pre-effect revalidation; never reread the publisher paths.
            ANNOTATED_DIFF="$(tail -n +2 "$ANNOTATED_DIFF_SNAPSHOT_PATH")"
            VALID_LINE_RANGES="$(cat "$HUNK_RANGES_SNAPSHOT_PATH")"
            VALID_DIFF_LINES="$(cat "$VALID_LINES_SNAPSHOT_PATH")"
        fi
    fi
fi

GATE_AUTHORITY="$(jq -cn \
  --arg state "$GATE_STATE" \
  --arg reason_code "$GATE_REASON_CODE" \
  --arg head_sha "$METRICS_HEAD_SHA" \
  --arg base_sha "$METRICS_BASE_SHA" \
  --arg merge_base_sha "$METRICS_MERGE_BASE_SHA" \
  --arg diff_sha256 "${DIFF_SHA256:-}" \
  --arg profile_id "${PROFILE_ID:-}" \
  --arg annotation_generation_id "${ANNOTATION_GENERATION_ID:-}" \
  '{state:$state,reason_code:$reason_code,snapshot:{
    head_sha:$head_sha,base_sha:$base_sha,merge_base_sha:$merge_base_sha,
    diff_sha256:$diff_sha256,profile_id:$profile_id
  },annotation_generation_id:$annotation_generation_id}')"

revalidate_retained_snapshot() {
    local current_marker="" current_head="" current_base="" current_merge_base=""
    local current_live_refs="" current_live_head="" current_live_base=""
    [ "$GATE_STATE" = valid_true ] || [ "$GATE_STATE" = valid_false ] || return 1
    current_marker="$(mktemp "${REVIEW_OUTPUT_DIR%/}/metrics_recheck.XXXXXX")" || return 1
    if ! cp -- "$diff_metrics_path" "$current_marker" ||
       ! cmp -s "$METRICS_MARKER_BEFORE" "$current_marker" ||
       ! cmp -s "$annotated_diff_path" "$ANNOTATED_DIFF_SNAPSHOT_PATH" ||
       ! cmp -s "$hunk_ranges_path" "$HUNK_RANGES_SNAPSHOT_PATH" ||
       ! cmp -s "$valid_lines_path" "$VALID_LINES_SNAPSHOT_PATH"; then
        rm -f -- "$current_marker"
        return 1
    fi
    rm -f -- "$current_marker"

    current_head="$(git -C "$REVIEW_CHECKOUT_ROOT" rev-parse HEAD 2>/dev/null || true)"
    [ "$current_head" = "$METRICS_HEAD_SHA" ] || return 1
    if [ "$MODE" = "local" ]; then
        current_base="$(git -C "$REVIEW_CHECKOUT_ROOT" rev-parse "$base_branch" 2>/dev/null || true)"
        current_merge_base="$(git -C "$REVIEW_CHECKOUT_ROOT" merge-base "$current_base" "$current_head" 2>/dev/null || true)"
        [ "$current_base" = "$METRICS_BASE_SHA" ] &&
            [ "$current_merge_base" = "$METRICS_MERGE_BASE_SHA" ]
    else
        current_live_refs="$(
          gh api "repos/{owner}/{repo}/pulls/${pr_number}" \
            --jq '{headRefOid:.head.sha,baseRefOid:.base.sha}' 2>/dev/null || true
        )"
        current_live_head="$(printf '%s' "$current_live_refs" | jq -r '.headRefOid // ""' 2>/dev/null)"
        current_live_base="$(printf '%s' "$current_live_refs" | jq -r '.baseRefOid // ""' 2>/dev/null)"
        [ "$current_live_head" = "$METRICS_HEAD_SHA" ] &&
            [ "$current_live_base" = "$METRICS_BASE_SHA" ]
    fi
}

refresh_final_snapshot_state() {
    if [ "$GATE_STATE" = valid_true ] || [ "$GATE_STATE" = valid_false ]; then
        if revalidate_retained_snapshot; then
            FINAL_SNAPSHOT_STATE=fresh
        else
            FINAL_SNAPSHOT_STATE=stale
        fi
    else
        # Missing/malformed authority is degradation, not movement of a retained
        # valid snapshot. It must resolve to needs_human, never stale_snapshot.
        FINAL_SNAPSHOT_STATE=authority_degraded
    fi
}
```

`VALID_DIFF_LINES` is the exact right-side changed-line authority. An eligible
experimental run may never fall back to `VALID_LINE_RANGES`. Standard findings retain
the existing exact-line-first, hunk-range-fallback behavior. Every Git command, agent
working directory, containment check, and parent evidence read uses
`REVIEW_CHECKOUT_ROOT`.
Call `refresh_final_snapshot_state` immediately before evidence reads, verdict
computation, every GitHub mutation, and the single handoff publication. Only a
previously valid retained authority that later fails byte/ref revalidation sets
`FINAL_SNAPSHOT_STATE=stale`. Missing or malformed initial gate authority remains
`authority_degraded` and resolves to `needs_human`; it is not a stale snapshot.
Neither branch triggers a fresh sidecar read or adopts a newer generation.

### Step 2.5: Deletion Context Pre-Computation

Before spawning audit subagents, compute the deletion context for the parallel
deletion regression audit. This step runs best-effort: if any command
fails (e.g., no local git checkout available), set `deletion_context = null` and
the deletion regression dimension is skipped in the parallel audit phase.

```bash
# 1. Get the PR's head and base refs
PR_HEAD=$(gh pr view {pr_number} --json headRefName -q .headRefName)
PR_BASE=$(gh pr view {pr_number} --json baseRefName -q .baseRefName)

# 2. Derive merge base via GitHub compare API (no local clone required)
MERGE_BASE=$(
  gh api repos/{owner}/{repo}/compare/${PR_BASE}...${PR_HEAD} \
    --jq '.merge_base_commit.sha' 2>/dev/null
)

# 3. Fetch the base branch locally to run git diff
REMOTE=$(git remote get-url upstream >/dev/null 2>&1 && echo upstream || echo origin)
git fetch "$REMOTE" ${PR_BASE} 2>/dev/null

# 4. Files deleted from base since branch point
DELETED_FILES=$(
  git diff --name-only --diff-filter=D ${MERGE_BASE} "$REMOTE"/${PR_BASE} 2>/dev/null
)

# 5. PR's changed files (from gh pr view, already available)
PR_FILES=$(gh pr view {pr_number} --json files -q '[.files[].path] | join(" ")' 2>/dev/null)

# 6. Symbols removed from files this PR modifies
if [ -n "$PR_FILES" ] && [ -n "$MERGE_BASE" ]; then
  DELETED_SYMBOLS=$(
    git diff --diff-filter=M ${MERGE_BASE} "$REMOTE"/${PR_BASE} -- ${PR_FILES} 2>/dev/null \
      | grep '^-' \
      | grep -E '^-(def |class |async def )' \
      | sed 's/^-//' \
      | sort -u
  )
else
  DELETED_SYMBOLS=""
fi
```

Store as `deletion_context`:
```python
deletion_context = {
    "merge_base": MERGE_BASE,
    "deleted_files": DELETED_FILES.splitlines(),        # list of paths
    "deleted_symbols": DELETED_SYMBOLS.splitlines(),    # list of "def foo", "class Bar"
    "pr_base": PR_BASE,
}
```

If `MERGE_BASE` is empty or any git command fails, set `deletion_context = null`.
The parallel deletion regression audit is skipped when `deletion_context` is null.
Resolve the dispatch decision with the installed production helper; it accepts no
overengineering gate input, so the two authorities cannot become coupled:

```python
from autoskillit.smoke_utils import deletion_regression_is_eligible

DELETION_DISPATCH_REQUIRED = deletion_regression_is_eligible(deletion_context)
```

### Step 2.9: Diff-Size Adaptive Agent Selection

Keep standard adaptive selection, experimental eligibility, and deletion
eligibility as separate authorities:

```bash
STANDARD_AGENT_ALLOWLIST="arch,tests,defense,bugs,cohesion,slop"
STANDARD_DISPATCH_AGENTS=""

if [ -n "$METRICS_MARKER_BEFORE" ]; then
    STANDARD_DISPATCH_AGENTS="$(
      jq -r 'if (.dispatch_agents | type) == "array"
             then .dispatch_agents | join(",") else "" end' \
        < "$METRICS_MARKER_BEFORE" 2>/dev/null || true
    )"
fi

if [ -z "$STANDARD_DISPATCH_AGENTS" ]; then
    STANDARD_DISPATCH_AGENTS="$STANDARD_AGENT_ALLOWLIST"
fi
```

Resolve experimental dispatch from the installed ordered registry and keep its
agent/dimension relationship structured:

```python
from autoskillit.smoke_utils import select_experimental_review_dispatch

EXPERIMENTAL_DISPATCH = select_experimental_review_dispatch(
    gate_state=GATE_STATE,
    annotated_diff=ANNOTATED_DIFF,
    valid_diff_lines=VALID_DIFF_LINES,
    standard_agent_names=STANDARD_AGENT_ALLOWLIST.split(","),
)
EXPERIMENTAL_DISPATCH_AGENTS = EXPERIMENTAL_DISPATCH["dispatch_agents"]
EXPERIMENTAL_AUDIT_STATE = EXPERIMENTAL_DISPATCH["audit_state"]
```

Do not rebuild the registry as a shell comma-string or print Python values back
through a shell bridge. The helper checks that the standard allowlist intersection is
empty and returns `degraded` with no proof-only dispatch if it is not.

`GATE_STATE=valid_false` dispatches neither proof-only auditor and leaves
`EXPERIMENTAL_AUDIT_STATE=not_required`; by itself it does not block approval.
`GATE_STATE=degraded` dispatches neither and blocks normal approval. Never derive the
gate from churn counts, truthiness, hooks, environment variables, transcripts,
sidecars, or model reasoning.

**Agent selection tiers:**
- **Small diff** (<200 added LoC and <5 changed files): `tests`, `cohesion`, and optionally `arch` if structural files changed (e.g., `__init__.py`, `pyproject.toml`).
- **Medium/large diff** (>= 200 added LoC or >= 5 changed files): All 6 standard agents (`arch`, `tests`, `defense`, `bugs`, `cohesion`, `slop`).

Experimental degradation never clears, cancels, replaces, or dynamically subtracts
standard calls. `deletion_context` remains independently gated. Missing or malformed
adaptive selection falls back to all six standard agents and never adds a proof-only
auditor to that fallback.

### Step 3: Run Parallel Audit Subagents (SINGLE MESSAGE)

Parse `STANDARD_DISPATCH_AGENTS` and iterate the structured
`EXPERIMENTAL_DISPATCH_AGENTS` records independently.
Add deletion work only when `DELETION_DISPATCH_REQUIRED` is true, independently of
`GATE_STATE`, and only while constructing the existing single foreground parallel batch.

**Issue ALL Task tool calls in a single message — one per dimension — so they execute
in parallel. Do NOT iterate through dimensions across multiple turns.**

Do not output any prose between subagent dispatches. Immediately proceed to the next tool call.

Standard and deletion subagents use ephemeral `Agent(model="sonnet")` calls and receive
only PR diff content. The two experimental agents use the exact registered calls below
and run with cwd `REVIEW_CHECKOUT_ROOT`.

```json
[
  {
    "file": "path/to/file.py",
    "line": 42,
    "dimension": "arch|tests|defense|bugs|cohesion|slop|deletion_regression|overengineering_reachability|overengineering_abstraction_surface",
    "severity": "critical|warning|info",
    "message": "Description of the finding",
    "requires_decision": false
  }
]
```

**Audit dimensions:**

1. **arch** — Architectural layering, import rule violations, domain separation.
   Check for: cross-layer imports, business logic in server layer, L0 importing L1+.

2. **tests** — Test quality: over-mocking, weak assertions, xdist safety, redundant tests.
   Check for: tests that assert nothing meaningful, broad mock patches, non-isolated state.

3. **defense** — Typed boundaries, error context preservation, validation at construction.
   Check for: missing type annotations at public boundaries, swallowed exceptions, late validation.

4. **bugs** — Diff checked against known recurring root causes.
   Check for: off-by-one errors, missing await, unhandled None, incorrect dict access.

5. **cohesion** — Structural symmetry, naming consistency, feature locality.
   Check for: inconsistent naming, scattered feature code, asymmetric patterns.

6. **slop** — Useless comments, dead code, backward-compat hacks left by AI.
   Check for: commented-out code, TODO without issue refs, over-verbose docstrings.

7. **deletion_regression** — Deliberate deletion regression check: severity: "critical",
   requires_decision: false for every finding. Cross-references the PR diff against
   `deletion_context` (deleted files and symbols computed in Step 2.5) to detect code
   that was intentionally removed from the base branch but re-added by this PR.
   Only spawned when `deletion_context` is non-null.

8. **overengineering_reachability** — Packless, proof-only, repository-reading
   reachability audit. It is eligible only for `GATE_STATE=valid_true`.

9. **overengineering_abstraction_surface** — Packless, proof-only,
   repository-reading abstraction-surface audit. It is eligible only for
   `GATE_STATE=valid_true`.

When eligible, issue these calls exactly once in the same foreground parallel message
as the standard and deletion calls:

- `Agent(subagent_type="autoskillit:pr-review-auditor-reachability", model="sonnet")`
- `Agent(subagent_type="autoskillit:pr-review-auditor-abstraction-surface", model="sonnet")`

For both registered calls, inline the actual `ANNOTATED_DIFF` string, the exact
`VALID_DIFF_LINES` JSON authority, `REVIEW_CHECKOUT_ROOT`, `METRICS_HEAD_SHA`,
`METRICS_BASE_SHA`, `METRICS_MERGE_BASE_SHA`, `DIFF_SHA256`, and
`ANNOTATION_GENERATION_ID` in the prompt. A path, placeholder, or description is
insufficient. Reads are restricted to the current checkout root; modifications and
network access are forbidden.

Await both outcomes and retain them in fixed configured agent order, never completion
order. A parent cancellation cancels the whole foreground group. A proof-only auditor
completes only after a successful terminal status and a top-level JSON array whose
every item passes the closed schema. A valid `[]` is a successful empty result.

Any tool failure, refusal, interruption, truncation, missing result, malformed JSON,
non-array result, or schema-invalid item sets `EXPERIMENTAL_AUDIT_STATE=degraded`.
Record the producer and deterministic reason in `AUDITOR_STATUS_BY_NAME`; do not add a
replacement to standard fallback. One success plus one failure produces
no partial experimental findings. Populate `EXPERIMENTAL_CANDIDATES` only after both complete
and all items validate, preserving auditor order and original array index.

Subagent prompt template (dimensions 1–6):

> You are reviewing a GitHub PR diff for [{dimension}] issues only.
> Scope: examine only the diff content provided. Do not fetch or read files outside the diff.
> Return a JSON array of findings. Each finding must have:
>   file, line, severity (critical/warning/info), dimension, message,
>   requires_decision (boolean).
>
> Set requires_decision=true ONLY for findings where the correct path forward is
> genuinely ambiguous and cannot be determined without the human's intent or
> preference — for example: design trade-offs, approach choices with valid
> alternatives, unclear intent after a merge conflict, plan/implementation
> divergences where both directions are valid.
>
> Set requires_decision=false for ALL bugs, style issues, or anything with a
> clear fix, regardless of severity. When in doubt, set requires_decision=false.
>
> Each line in the diff is prefixed with `[LNNN]` where NNN is the new-file line number.
> When reporting findings, use the `[LNNN]` number as the `line` value in your finding.
> Do not compute line numbers yourself — use the marker.
> If the finding cannot be anchored to a specific `[LNNN]` marker, use the nearest
> `+` or context line's marker in the same hunk.
>
> If no issues found, return an empty array [].
> Read the annotated diff file at path: {annotated_diff_path}
> Each line in the file is prefixed with [LNNN] markers indicating the GitHub diff line number.
> Use the [LNNN] number as the `line` value in your findings JSON.
>
> Prior resolved findings (DO NOT RE-RAISE — these have been addressed by resolve-review):
> {json_list_of_prior_resolved_findings or "[]"}
>
> Prior unresolved findings (FOCUS ON these persistent issues if they appear in the diff you are reviewing):
> {json_list_of_prior_unresolved_findings or "[]"}
>
> When a finding matches a prior resolved entry by file and approximate line (within ±5 lines):
> SKIP it entirely — do not include it in your findings array.
>
> **Severity calibration (bugs dimension):**
>
> - **critical**: The code will produce wrong results, data loss, or silent corruption
>   at runtime. Example: a context manager wrapping only the first line of a function
>   body instead of the entire body — downstream code runs without the expected context.
>
> - **warning**: The code has a structural flaw that does not affect correctness today
>   but will under foreseeable conditions (error paths, edge cases, future changes).
>   Example: a try/except that catches a broad exception class but only handles one
>   specific subtype.
>
> - **info**: Style, convention, or minor improvement that does not affect correctness.
>   Example: an unused import, a redundant type annotation.
>
> **Grouping rule:** If N instances of the same structural pattern appear across
> different functions or files, classify ALL at the highest severity any single
> instance warrants. Do not downgrade duplicates to warning merely because they
> are repetitive.

Pass `prior_resolved_findings` and `prior_unresolved_findings` (both as JSON arrays) into each
subagent prompt via template substitution. The annotated diff is available at `annotated_diff_path`
— instruct subagents to Read it. If `annotated_diff_path` is unset or the file does not exist,
state "No annotated diff is available for this run — evaluate the diff without [LNNN] anchors and
approximate `line` from context" instead of emitting a reference to a nonexistent path. This
mirrors the existing graceful-degradation fallback for `DISPATCH_AGENTS` documented in Step 2.9.

Subagent prompt template (dimension 7 — deletion_regression, only when `deletion_context` is non-null):

> You are checking a GitHub PR diff for DELETION REGRESSIONS only.
> A deletion regression is when a PR reintroduces code (a file, function, or class)
> that was deliberately deleted from the base branch after the PR was branched.
>
> Deletion context (items deleted from {pr_base} since this PR branched at {merge_base}):
> Deleted files: {deletion_context.deleted_files}
> Deleted symbols: {deletion_context.deleted_symbols}
>
> PR diff:
> Read the raw diff file at path: {diff_file_path}
>
> Instructions:
> - For each deleted file in the deletion context: check if the diff adds or recreates it
>   (look for `+++ b/{file}` or `diff --git a/{file}` with added lines).
> - For each deleted symbol (e.g., "def foo", "class Bar"): check if the diff adds it back
>   (look for `+def foo`, `+class Bar`, or `+async def foo` lines in the diff).
> - For each regression found, return a finding with:
>   - severity: "critical"
>   - dimension: "deletion_regression"
>   - requires_decision: false
>   - message: "Deletion regression: '{name}' was deliberately deleted from {pr_base}
>     but this PR reintroduces it. Remove it."
> - If no regressions found, return [].
>
> Return a JSON array of findings.

### Step 4: Aggregate and Deduplicate Findings

Keep `STANDARD_RAW_FINDINGS` separate from `EXPERIMENTAL_CANDIDATES`. Append
standard and deletion responses only to `STANDARD_RAW_FINDINGS`.

Before accepting any experimental item, validate both arrays completely. The exact
candidate key set is `file`, `line`, `dimension`, `severity`, `message`,
`requires_decision`, `evidence`, `trace`, `boundary_checks`, `confidence`, and
`simpler_behavior`; reject missing or extra keys. Enforce:

- dimension is exactly `overengineering_reachability` or
  `overengineering_abstraction_surface`; severity is `critical`, `warning`, or
  `info`; `requires_decision` is an exact boolean;
- `file`, `message`, and `simpler_behavior` are non-empty strings after trimming;
- primary, evidence, and trace lines are positive integers excluding booleans;
- the primary `(file, line)` occurs in exact `VALID_DIFF_LINES`, never only a hunk
  range;
- evidence items have exactly `{path,line,role,claim}`, contain at least two
  distinct repository-relative `path:line` locations, and use only `anchor`,
  `caller`, `consumer`, `registration`, `invariant`, or
  `counterevidence_checked`; every `path`, `role`, and `claim` is a non-empty
  string after trimming;
- trace items have exactly `{path,line,relation}` and form a non-empty ordered
  chain; every `path` and `relation` is a non-empty string after trimming;
- boundary checks contain exactly one `{boundary,status,claim}` row for each
  `reflection_decorators`, `dependency_injection`, `plugin_registry`,
  `cli_entrypoint`, `serialization`, `generated_code`, and `public_api`, with
  status `checked_absent`, `checked_no_reachable_path`, or `not_applicable`;
  every boundary `claim` is a non-empty string after trimming;
- paths are relative, contain no `..`, and canonically remain under
  `REVIEW_CHECKOUT_ROOT`;
- `type(confidence)` is exactly integer or float, never boolean, is finite, and
  lies in `[0,1]`;
- `simpler_behavior` is non-empty and covers return values, exceptions, ordering,
  persistence, concurrency, and compatibility.

For each structurally valid item, generate `record_digest` from canonical JSON and
generate `candidate_id` from the snapshot identity, auditor name, original array
index, and digest. These are parent-owned fields. Any structural, containment,
changed-line, or authority failure degrades the whole experimental run and prevents
every sibling from entering normal aggregation.

Before repository evidence reads, revalidate checkout head/base/merge-base, the
mode-appropriate live refs, the byte-identical metrics marker, diff identity/profile,
and all artifact digests. Quote and read each cited location under
`REVIEW_CHECKOUT_ROOT`. Write a separate immutable disposition record with a
parent-generated `disposition_id` referencing `candidate_id`. Confidence never
implies acceptance. The parent must verify every role-labelled evidence claim,
every one of the seven boundary claims, every hop in the complete ordered trace
as a reachable chain, and the proposed simpler behavior's semantic equivalence
for return values, exceptions, ordering, persistence, concurrency, and
compatibility. Missing, contradictory, or unverified claims reject the candidate;
the parent may not accept a sampled subset. The closed disposition/rejection
reason codes are:
`accepted`, `schema_invalid`, `path_escape`, `not_changed_line`,
`stale_snapshot`, `insufficient_evidence`, `boundary_unchecked`,
`reachable_counterexample`, `simpler_behavior_not_equivalent`,
`suppressed_prior_thread`, `duplicate_candidate`, and `publication_failed`.
Bound free-text explanation to 1 KiB UTF-8.

Malformed output is stored only as a bounded envelope: producer, terminal status,
received byte length and SHA-256, at most a 4 KiB excerpt or iteration-scoped raw
reference, parse/schema errors, and rejection reason. Never copy unbounded model
output into an ordinary summary.

Use the installed helpers
`autoskillit.smoke_utils.validate_experimental_auditor_outputs`,
`build_malformed_review_envelope`, and
`aggregate_experimental_review_candidates` as the canonical executable semantics
for fixed-order all-or-nothing validation, bounded malformed envelopes, and
accepted-only suppression-before-dedup. The aggregation call is the single combined
standard/deletion/experimental aggregation boundary: pass the retained snapshot
identity, exact changed-line and hunk-range authority, all standard/deletion findings, parent
dispositions, and prior resolved findings.
Use `prepare_experimental_review_publication` as the canonical executable semantics
for common generation identity, local-findings-last ordering, and stale effect
suppression. Use `publish_experimental_review_artifacts` for same-directory temporary
writes, marker-last atomic renames, cleanup, and rollback. Validation, aggregation,
and preparation perform no repository reads or writes; publication writes only the
already-prepared documents. Parent evidence adjudication and every snapshot
revalidation remain mandatory here.

Invoke validation and aggregation directly and thread their returned records into
the named review state; do not reimplement their behavior in prose:

```python
import json

from autoskillit.smoke_utils import (
    aggregate_experimental_review_candidates,
    render_review_finding_body,
    validate_experimental_auditor_outputs,
)

STANDARD_VALIDATION_ERRORS = []
try:
    STANDARD_FINDINGS_DECODED = json.loads(STANDARD_RAW_FINDINGS)
except json.JSONDecodeError:
    STANDARD_FINDINGS = []
    STANDARD_VALIDATION_ERRORS.append("standard findings are not valid JSON")
else:
    if isinstance(STANDARD_FINDINGS_DECODED, list):
        STANDARD_FINDINGS = STANDARD_FINDINGS_DECODED
    else:
        STANDARD_FINDINGS = []
        STANDARD_VALIDATION_ERRORS.append("standard findings must be a JSON array")
if GATE_STATE == "valid_true":
    VALIDATION_RESULT = validate_experimental_auditor_outputs(
        outputs=EXPERIMENTAL_OUTCOMES_BY_NAME,
        valid_diff_lines=VALID_DIFF_LINES,
        snapshot=GATE_AUTHORITY["snapshot"],
        review_root=REVIEW_CHECKOUT_ROOT,
    )
elif GATE_STATE == "valid_false":
    VALIDATION_RESULT = {
        "state": "not_required",
        "candidates": [],
        "status_by_name": {},
        "malformed_envelopes": [],
    }
else:
    VALIDATION_RESULT = {
        "state": "degraded",
        "candidates": [],
        "status_by_name": {},
        "malformed_envelopes": [],
    }
EXPERIMENTAL_AUDIT_STATE = VALIDATION_RESULT["state"]
EXPERIMENTAL_CANDIDATES = VALIDATION_RESULT["candidates"]
AUDITOR_STATUS_BY_NAME.update(VALIDATION_RESULT["status_by_name"])
MALFORMED_ENVELOPES = VALIDATION_RESULT["malformed_envelopes"]

# Construct DISPOSITION_RECORDS only after the mandatory parent evidence reads.
if STANDARD_VALIDATION_ERRORS:
    AGGREGATION_RESULT = {
        "state": "degraded",
        "survivors": [],
        "aggregation_records": [],
        "validation_errors": STANDARD_VALIDATION_ERRORS,
    }
else:
    AGGREGATION_RESULT = aggregate_experimental_review_candidates(
        candidates=EXPERIMENTAL_CANDIDATES,
        dispositions=DISPOSITION_RECORDS,
        prior_resolved_findings=prior_resolved_findings,
        standard_findings=STANDARD_FINDINGS,
        valid_diff_lines=VALID_DIFF_LINES,
        valid_line_ranges=VALID_LINE_RANGES,
        snapshot=GATE_AUTHORITY["snapshot"],
        review_root=REVIEW_CHECKOUT_ROOT,
    )
if AGGREGATION_RESULT["state"] == "degraded":
    EXPERIMENTAL_AUDIT_STATE = "degraded"
FINAL_REVIEW_FINDINGS = [
    {**finding, "rendered_body": render_review_finding_body(finding)}
    for finding in AGGREGATION_RESULT["survivors"]
]
all_findings = FINAL_REVIEW_FINDINGS
AGGREGATION_RECORDS = AGGREGATION_RESULT["aggregation_records"]
```

Feed only parent-accepted experimental findings into normal aggregation. The helper
is that single normal-aggregation boundary; standard and deletion findings enter
without experimental dispositions. It normalizes every source in fixed order: the
standard dimension allowlist, deletion regression, reachability, then
abstraction-surface, preserving original array index. Suppress and deduplicate
exactly once across that combined sequence; do not append a second
standard/deletion list afterward.

1. Suppression pass — before deduplication, remove a finding matching
   `prior_resolved_findings` by the same file and a line within ±5. Log
   `"Suppressing finding at {file}:{line} — matches prior resolved thread"`.
   For experimental candidates create a linked immutable aggregation record with
   reason `suppressed_prior_thread`; do not mutate the candidate or disposition.
2. Deduplicate by `(file, line)` after suppression. Rank collisions by severity,
   then prefer `requires_decision=false`, then fixed source rank and original array
   index. Create a deterministic `dedup_group_id`; retain every member
   `candidate_id`, the winner, and rationale. Losers receive linked
   `duplicate_candidate` aggregation records.
3. Partition findings using exact line validation when available:
   - When `VALID_DIFF_LINES` is non-empty (loaded in Step 2.7), use **set-membership**:
     a finding is postable if its `line` exists in `VALID_DIFF_LINES[file]`. This is
     strictly more accurate than hunk-span interval checking.
   - When `VALID_DIFF_LINES` is empty but `VALID_LINE_RANGES` is non-empty, fall back to
     **hunk-span interval checking**: a finding is postable if its `(file, line)` falls
     within any hunk range for that file.
   - `FILTERED_FINDINGS`: findings that pass validation (either set-membership or interval).
     These are safe to post as inline comments in Step 6.
   - `UNPOSTABLE_FINDINGS`: findings that fail validation.
     Log a warning for each. These findings are surfaced via:
     - Step 6: Critical-severity unpostable findings are posted as file-level comments
       (subject_type: "file") on the individual comments endpoint.
     - Step 7: All unpostable findings appear in the "Outside Diff Range" section of the
       review body.
   - If both `VALID_DIFF_LINES` and `VALID_LINE_RANGES` are empty, all findings are `FILTERED_FINDINGS`.
4. Apply verdict logic (Step 5) to ALL findings (`FILTERED_FINDINGS` + `UNPOSTABLE_FINDINGS`
   combined), so unpostable findings still contribute to the `changes_requested` verdict.
5. Bucket by actionability (applied to combined findings):
   - `actionable_findings` — requires_decision=false AND severity in ("critical", "warning")
   - `decision_findings` — requires_decision=true (any severity)
   - `info_findings` — severity == "info" AND requires_decision=false

Preserve accepted experimental evidence, trace, boundary checks, confidence,
simpler behavior, `candidate_id`, `disposition_id`, and snapshot on the dedup winner,
diff context, local handoff, GitHub rendering, and summary. Represent validation,
disposition, aggregation, verdict use, and publication as separate immutable linked
records.

Immediately before verdict computation and again before any artifact handoff or GitHub
effect, call `refresh_final_snapshot_state`. If an initially valid retained snapshot
is now unavailable or differs, discard survivor sets from effect-producing consumers,
permit only diagnostic raw/summary envelopes with empty survivors, and emit
`stale_snapshot`. In that movement branch the refresh helper sets
`FINAL_SNAPSHOT_STATE=stale` before any consumer is selected. Initial gate
degradation remains `authority_degraded` and emits
`needs_human`. Do not publish diff context, local findings, receipts, comments,
reviews, or approvals from either state. On freshness, set
`COMMIT_ID="$METRICS_HEAD_SHA"` once and never query a later head to replace it.

### Step 4.5: Echo Primary Obligation

After aggregating all subagent findings, before proceeding to verdict or posting, you MUST state aloud:

> "I have N findings. My primary job is to post inline comments on specific code lines for each finding. I must use the GitHub Reviews API to leave comments anchored to the exact lines in the diff."

This is not optional. Do not proceed to Step 5 without stating this.

### Step 5: Determine Verdict

Verdict precedence is authoritative:

1. Final ref, marker, diff, profile, or artifact mismatch/unavailability produces
   `stale_snapshot` and no finding-derived external effects.
2. On a fresh snapshot, any accepted critical non-decision finding produces
   `changes_requested`.
3. Otherwise `GATE_STATE=degraded` or
   `EXPERIMENTAL_AUDIT_STATE=degraded` produces `needs_human`.
4. Otherwise preserve warning, decision, and approval behavior.

No degraded gate, eligible audit, or stale snapshot may emit `approved`,
`approved_with_comments`, or a GitHub approval event.

**Verdict logic:**
```python
from autoskillit.smoke_utils import determine_experimental_review_verdict

refresh_final_snapshot_state()
RETAINED_SNAPSHOT_WAS_VALID = GATE_STATE in {"valid_true", "valid_false"}
SNAPSHOT_IS_FRESH = FINAL_SNAPSHOT_STATE == "fresh"
verdict = determine_experimental_review_verdict(
    retained_snapshot_was_valid=RETAINED_SNAPSHOT_WAS_VALID,
    final_snapshot_is_fresh=SNAPSHOT_IS_FRESH,
    gate_state=GATE_STATE,
    experimental_audit_state=EXPERIMENTAL_AUDIT_STATE,
    findings=all_findings,
)
```

Before Step 6, finish `RAW_LEDGER` and `HANDOFF_METADATA` in memory. The metadata
contains the existing `summary`, `verdict`, `pr_number`, `iteration`,
`schema_version`, and `written_at` fields. Derive the generation ID before any
GitHub effect from the same final combined findings and ledger that Step 8 will
publish:

```python
from autoskillit.smoke_utils import prepare_experimental_review_publication

if SNAPSHOT_IS_FRESH:
    PUBLICATION_SEED = prepare_experimental_review_publication(
        raw_ledger=RAW_LEDGER,
        survivors=FINAL_REVIEW_FINDINGS,
        snapshot=GATE_AUTHORITY["snapshot"],
        annotation_generation_id=ANNOTATION_GENERATION_ID,
        mode=MODE,
        snapshot_is_fresh=True,
        handoff_metadata=HANDOFF_METADATA,
    )
    REVIEW_GENERATION_ID = PUBLICATION_SEED["artifacts"]["raw_findings"][
        "review_generation_id"
    ]
else:
    PUBLICATION_SEED = None
    REVIEW_GENERATION_ID = ""
```

### Step 6: Post Inline Review Comments

**MODE BRANCHING:**

Run the final freshness guard before entering this step. If it yields
`stale_snapshot`, skip Steps 6-7 completely. Every GitHub mutation below uses the
authoritative `COMMIT_ID="$METRICS_HEAD_SHA"` and the same checkout/annotation
generation. Never replace it with a later HEAD query.

**When `mode=local`:**
- Skip ALL GitHub API calls for posting comments (no batch review POST, no individual comment POSTs, no file-level comments, no summary review POST)
- Build the local findings payload in memory; Step 8 atomically publishes
  `${REVIEW_OUTPUT_DIR}local_findings_{pr_number}.json` last

**Iteration tracking:** Before writing, check if `local_findings_{pr_number}.json` already exists. If so, read its `iteration` field and set the new value to `iteration + 1`. If the file does not exist, set `iteration` to `0`.

Prepare the local findings JSON:
```json
{
  "findings": [
    {
      "path": "src/foo.py",
      "line": 42,
      "body": "[critical] arch: finding text",
      "severity": "critical",
      "dimension": "arch",
      "side": "RIGHT",
      "evidence": [],
      "trace": [],
      "boundary_checks": [],
      "confidence": 0.98,
      "simpler_behavior": "...",
      "candidate_id": "...",
      "disposition_id": "...",
      "snapshot": {}
    }
  ],
  "summary": "AutoSkillit PR Review — Verdict: {verdict}",
  "verdict": "{verdict}",
  "pr_number": "{pr_number}",
  "iteration": {iteration_number},
  "_head_sha": "{METRICS_HEAD_SHA}",
  "_base_sha": "{METRICS_BASE_SHA}",
  "_merge_base_sha": "{METRICS_MERGE_BASE_SHA}",
  "annotation_generation_id": "{ANNOTATION_GENERATION_ID}",
  "review_generation_id": "{REVIEW_GENERATION_ID}"
}
```

For `iteration_number`: read from existing file (`iteration + 1`) or start at `0`.

Include all findings from `FILTERED_FINDINGS` + `UNPOSTABLE_FINDINGS` (same as what
would have been posted to GitHub). Copy the complete finding dictionary, normalize
`file` to `path`, and add the existing `body`, `side`, and `iteration` aliases without
discarding opaque evidence or provenance fields.

**Still write mode-independent files:**
- `${REVIEW_OUTPUT_DIR}diff_context_{pr_number}.json` (Step 8)
- `${REVIEW_OUTPUT_DIR}raw_findings_{pr_number}.json` (Step 8)
- `${REVIEW_OUTPUT_DIR}summary_{pr_number}_{timestamp}.md` (Step 8)

Then skip directly to Step 8 (verdict emission) — no GitHub API calls, no Step 6.5 confirmation, no Step 7 submission.

**Gate token emission is mode-independent:** `%%REVIEW_GATE::LOOP_REQUIRED%%` on `changes_requested`, `%%REVIEW_GATE::CLEAR%%` on `approved`/`needs_human` — emitted identically in both modes.

**When `mode=github`:** Execute Steps 6, 6.5, and 7 as documented below (current behavior unchanged).

Build review comment bodies for each critical and warning finding. Use the `line` and `side`
fields (modern GitHub Reviews API — not the deprecated `position` field) so that file line
numbers from audit findings map directly without diff-position counting.

For each finding, `line` is the finding's `line` value (the line number in the new file) and
`side` is always `RIGHT` (referring to the right-hand side of the diff — additions and context
in the updated file).

Build `COMMENTS_JSON` from `FILTERED_FINDINGS` only (not `UNPOSTABLE_FINDINGS`). All findings
in `FILTERED_FINDINGS` have been validated against `VALID_LINE_RANGES` in Step 4, so they are
safe to post as inline comments.

Build a proper JSON payload where each comment is a complete object, then post via `--input -`.
The `--field` approach creates one array entry per flag (not one object per comment), so it must
not be used for the `comments` array:

```bash
# Build comments JSON array from FILTERED_FINDINGS only (critical/warning only)
COMMENTS_JSON=$(jq -n --argjson findings "$FILTERED_FINDINGS" '
  $findings | map(select(.severity == "critical" or .severity == "warning")) | map({
    path: .file,
    line: .line,
    side: "RIGHT",
    body: .rendered_body
  })
')

# Build and post the full review payload via stdin. --include makes the HTTP
# status authoritative without relying on response-body shape.
refresh_final_snapshot_state
if [ "$FINAL_SNAPSHOT_STATE" != fresh ]; then
  verdict=stale_snapshot
  SNAPSHOT_IS_FRESH=false
  RECEIPT_DOCUMENT=""
fi
if [ "$FINAL_SNAPSHOT_STATE" = fresh ]; then
  REVIEW_EVENT="{APPROVE|COMMENT|REQUEST_CHANGES}"
  if BATCH_RESPONSE_TMP="$(mktemp "${REVIEW_OUTPUT_DIR%/}/batch_review_response.XXXXXX")"; then
    jq -n \
      --arg body "AutoSkillit PR Review — Verdict: {verdict}" \
      --arg event "$REVIEW_EVENT" \
      --arg commit_id "$COMMIT_ID" \
      --argjson comments "$COMMENTS_JSON" \
      '{body: $body, event: $event, commit_id: $commit_id, comments: $comments}' | \
    gh api /repos/{owner}/{repo}/pulls/{pr_number}/reviews \
      --method POST --include --input - > "$BATCH_RESPONSE_TMP"
    HTTP_STATUS="$(
      awk 'toupper($1) ~ /^HTTP\// {status=$2} END {print status}' "$BATCH_RESPONSE_TMP"
    )"
    POSTED_REVIEW_ID="$(
      awk 'body || index($0, "{") == 1 {body=1; print}' "$BATCH_RESPONSE_TMP" |
        jq -r '.id // empty' 2>/dev/null || true
    )"
  fi
fi
if [ "$HTTP_STATUS" = "200" ]; then
  refresh_final_snapshot_state
  if [ "$FINAL_SNAPSHOT_STATE" = fresh ]; then
    RECEIPT_DOCUMENT="$(jq -cn \
      --arg commit_id "$COMMIT_ID" \
      --arg review_generation_id "$REVIEW_GENERATION_ID" \
      '{posted:true,http_status:200,commit_id:$commit_id,
        review_generation_id:$review_generation_id}')"
  else
    verdict=stale_snapshot
    SNAPSHOT_IS_FRESH=false
    RECEIPT_DOCUMENT=""
    if [ -n "$POSTED_REVIEW_ID" ] && [ "$REVIEW_EVENT" != COMMENT ]; then
      sleep 1
      if ! gh api \
        /repos/{owner}/{repo}/pulls/{pr_number}/reviews/"$POSTED_REVIEW_ID"/dismissals \
        --method PUT \
        --field message="Dismissed because the PR snapshot changed during review submission" \
        >/dev/null
      then
        STALE_REVIEW_COMPENSATION_FAILED=true
      fi
    fi
  fi
fi
rm -f -- "$BATCH_RESPONSE_TMP"
```

Step 6 constructs the receipt in memory only. It must not write the fixed receipt
path. Step 8 passes the parsed `RECEIPT_DOCUMENT` to the sole atomic publisher, so
raw findings and diff context are renamed before the receipt becomes visible.
The post-submit refresh is part of the effect boundary. A moved snapshot immediately
replaces the prior verdict with `stale_snapshot`, suppresses the receipt, and dismisses
an authoritative approval or changes-requested review. Report
`STALE_REVIEW_COMPENSATION_FAILED=true` as a needs-human operational failure while
retaining the `stale_snapshot` verdict and diagnostic-only artifacts.

Event mapping:
- `approved` → `APPROVE`
- `approved_with_comments` → `COMMENT`
- `needs_human` → `COMMENT`
- `changes_requested` → `REQUEST_CHANGES`

**Success signal:** If the batch POST returns HTTP 200, treat the review as successfully
posted regardless of response body content. Do NOT inspect the response body for a
`comments` array — GitHub's review API does not echo back the submitted comments, so any
length check would always read 0 and falsely trigger Tier 1 fallback.

**Own-PR guard:** If the batch POST returns HTTP 422 and the error message mentions
"review" or "author", the PR is self-authored. Retry the same request with event
`COMMENT` instead of `REQUEST_CHANGES`. GitHub does not allow a PR author to submit a
`REQUEST_CHANGES` review on their own PR.

**File-Level Comments for Critical Unpostable Findings:**

After the batch review POST succeeds (or after Tier 1 individual posting completes),
post file-level comments for each **critical-severity** finding in `UNPOSTABLE_FINDINGS`.
These use the individual comments endpoint with `subject_type: "file"` — this parameter
is NOT valid on the batch Reviews API `comments[]` array.

```bash
COMMIT_ID="$METRICS_HEAD_SHA"

# For each CRITICAL finding in UNPOSTABLE_FINDINGS:
# NOTE: Do NOT include a `line` field — `line` must be omitted (not set to null)
# for subject_type: "file". The `gh api --field` syntax naturally omits unspecified
# fields, so simply not including `--field line=...` is correct.
gh api /repos/{owner}/{repo}/pulls/{pr_number}/comments \
  --method POST \
  --field path="{finding.file}" \
  --field subject_type="file" \
  --field commit_id="$COMMIT_ID" \
  --field body="{finding.rendered_body}"
sleep 1  # Rate-limit discipline: 1s between mutating calls
```

Only critical-severity findings are posted as file-level comments to control API call volume.
Warning and info unpostable findings appear in the Step 7 review body only.

If a file-level POST fails, log the failure and continue — file-level comments are
best-effort supplementary visibility. Do not fall through to Tier 1/Tier 2 for these.

**Fallback Tier 1 — Individual Comments (if batch POST fails):**

Iterate only critical and warning findings from `FILTERED_FINDINGS`. Skip info-severity findings — they do not warrant individual GitHub comments.

Attempt to post each critical/warning finding individually via:

```bash
COMMIT_ID="$METRICS_HEAD_SHA"

# For each critical/warning finding in FILTERED_FINDINGS:
gh api /repos/{owner}/{repo}/pulls/{pr_number}/comments \
  --method POST \
  --field path="{finding.file}" \
  --field line={finding.line} \
  --field side="RIGHT" \
  --field commit_id="$COMMIT_ID" \
  --field body="{finding.rendered_body}"
sleep 1  # Rate-limit discipline: 1s between mutating calls
```

Individual POSTs are not atomic — one failure does not block others.
When an individual POST returns HTTP 422 (typically an invalid line number), retry that
specific comment as a file-level comment using `subject_type: "file"` (no `line` field)
on the `/repos/{owner}/{repo}/pulls/{pr_number}/comments` endpoint. If the file-level
retry also fails, log the failure and continue to the next finding.
If at least one per-finding comment succeeds (inline or file-level), proceed to Step 7.

**Fallback Tier 2 — DEGRADED: Bullet-List Summary Dump (if all individual posts fail):**

WARNING: If you reach Tier 2 fallback, the review has FAILED its primary purpose.
Before posting the body dump, you MUST state:

> "FALLBACK: I was unable to post inline comments. Posting summary as review body instead. This is a DEGRADED review."

Tier 2 is a failure mode with a workaround, not an acceptable alternative to inline comments.

Post ALL findings (`FILTERED_FINDINGS` + `UNPOSTABLE_FINDINGS`) via:

```bash
jq -n \
  --arg body "{summary_markdown}" \
  --arg commit_id "$COMMIT_ID" \
  '{body:$body,event:"COMMENT",commit_id:$commit_id}' | \
gh api /repos/{owner}/{repo}/pulls/{pr_number}/reviews \
  --method POST --input -
```

Format each file's findings as a bullet list (not a markdown table). Reuse
`finding.rendered_body` verbatim for every bullet; it is the same compact renderer
used by the primary batch, file-level comments, and Tier 1:

```
## AutoSkillit Review Findings

**Verdict:** {verdict}

### path/to/file.py
- **L{line}** {finding.rendered_body}

### path/to/other.py
- **L{line}** {finding.rendered_body}
```

This bullet-list format avoids horizontal overflow from long message content.

### Step 6.5: Post-Completion Confirmation

After completing Step 6, you MUST state:

> "I confirm that I posted N inline comments on the following files: [list files]. If I posted 0 inline comments and had findings, this review has FAILED its primary purpose."

If you find yourself writing "I posted 0 inline comments and had N findings" — STOP.
Do not proceed to Step 7. Instead, investigate why zero comments were posted. Check
whether the line numbers in your findings match `VALID_LINE_RANGES`. If they do not,
attempt to map each finding to the nearest valid hunk line before falling back.

**CRITICAL — No Local File Paths in GitHub Output:**
Never reference local file paths (e.g., `{{AUTOSKILLIT_TEMP}}/...`, `summary_*.md`, absolute paths) in the review body, inline comments, or any content posted to GitHub. The summary file is a local audit artifact only — GitHub readers cannot access local filesystem paths. Reference findings by file path and line number within the repository, not by local temp file locations.

### Step 7: Submit Summary Review

```bash
# Build the summary review against the same authoritative commit.
jq -n \
  --arg body "$BODY" \
  --arg event "{APPROVE|COMMENT|REQUEST_CHANGES}" \
  --arg commit_id "$COMMIT_ID" \
  '{body:$body,event:$event,commit_id:$commit_id}' | \
gh api /repos/{owner}/{repo}/pulls/{pr_number}/reviews \
  --method POST --input -
```

Use `APPROVE` for approved, `COMMENT` for approved-with-comments or needs-human,
and `REQUEST_CHANGES` for changes-requested. Treat HTTP 200 as success without
inspecting a returned `comments` array. Retain the one-second delay between every
pair of consecutive POST/PATCH/PUT/DELETE calls.

**Building the Outside Diff Range body section:**

When `UNPOSTABLE_FINDINGS` is non-empty, construct the body by appending the following
section after the verdict one-liner. Group unpostable findings by file, format as a
bullet list reusing the Tier 2 `finding.rendered_body` format.

**TRUNCATION GUARD:** Cap the Outside Diff Range section at ~40,000 characters.
The GitHub review body has a hard 65,536-char limit (HTTP 422 on overflow,
no graceful degradation). Reserve headroom for the verdict line and formatting.
If truncated, append: "...and N more findings. See file-level comments for
critical items."

Template for the appended section:

```
### ⚠️ Outside Diff Range

These findings target lines not in the diff and could not be posted as inline comments:

**path/to/file.py**
- **L42** {finding.rendered_body}

**path/to/other.py**
- **L99** {finding.rendered_body}
```

### Step 8: Write Summary and Emit Verdict

**CRITICAL — Ordering:** Step 8 must execute after Steps 6 and 7. Do not write the summary file before posting inline comments and submitting the review verdict to GitHub. Writing the file first anchors you to treating it as the primary output rather than a local audit artifact.

Re-run the final freshness guard before every `diff_context` handoff publication. Derive
`review_generation_id` from the snapshot authority and canonical normalized raw
ledger. Every artifact in a successful invocation carries the same
`review_generation_id`, `_head_sha`, `_base_sha`, optional `_merge_base_sha`, and
`annotation_generation_id`.

Publish every fixed-name artifact through a same-directory temporary file followed
by atomic rename. Clean partial temporary files on failure. Never redirect directly
to a fixed destination. Publish effect-bearing artifacts in this order:

1. diagnostic/raw ledger;
2. diff context;
3. GitHub receipt/publication records when applicable;
4. `local_findings_{pr_number}.json last` in local mode as the local generation
   commit marker.

On `stale_snapshot`, publish only bounded diagnostic raw and summary envelopes with
empty survivor sets. Do not publish diff context, local findings, receipts, comments,
reviews, or approvals.

The raw ledger contains immutable linked arrays named `candidate_records`,
`validation_records`, `disposition_records`, `aggregation_records`,
`verdict_use_records`, and `publication_records`. Every candidate has
`candidate_id` and `record_digest`; later records reference that ID rather than
mutating the candidate. Dedup records include `dedup_group_id`, all member IDs,
winner ID, and rationale. Include `GATE_AUTHORITY`, the fixed-order
`AUDITOR_STATUS_BY_NAME` terminal-status authority, review mode, snapshot,
generations, accepted/rejected counts, and bounded
malformed envelopes.

Save findings summary to `${REVIEW_OUTPUT_DIR}summary_{pr_number}_{timestamp}.md`. (relative to the current working directory)

Prepare and publish the structured artifacts with the installed executable
helpers. The final freshness check selects a complete or diagnostic-only
publication; the publisher stages every document before renaming and rolls back
any completed rename if a later boundary fails. Execute
`refresh_final_snapshot_state` immediately before this block and assign
`FINAL_SNAPSHOT_STATE == "fresh"` to the Python boolean `SNAPSHOT_IS_FRESH`.
Parse `RECEIPT_DOCUMENT` only when it is non-empty:

```python
import json

from autoskillit.smoke_utils import (
    prepare_experimental_review_publication,
    publish_experimental_review_artifacts,
)

SNAPSHOT_IS_FRESH = FINAL_SNAPSHOT_STATE == "fresh"
if not SNAPSHOT_IS_FRESH:
    if FINAL_SNAPSHOT_STATE == "stale":
        verdict = "stale_snapshot"
        FINAL_REVIEW_FINDINGS = []
        HANDOFF_METADATA = {**HANDOFF_METADATA, "verdict": verdict}
        RAW_LEDGER = {**RAW_LEDGER, "verdict": verdict}
    elif FINAL_SNAPSHOT_STATE == "authority_degraded":
        verdict = "needs_human"
        FINAL_REVIEW_FINDINGS = []
        HANDOFF_METADATA = {**HANDOFF_METADATA, "verdict": verdict}
        RAW_LEDGER = {**RAW_LEDGER, "verdict": verdict}

if FINAL_SNAPSHOT_STATE == "authority_degraded":
    # No retained snapshot or annotation generation exists, so fixed-name
    # publication cannot satisfy its identity contract. Keep the bounded
    # diagnostics in the timestamped summary and emit needs_human instead.
    PUBLICATION = None
    PUBLICATION_RESULT = {
        "state": "authority_degraded",
        "publication_records": [],
    }
else:
    PUBLICATION = prepare_experimental_review_publication(
        raw_ledger=RAW_LEDGER,
        survivors=FINAL_REVIEW_FINDINGS,
        snapshot=GATE_AUTHORITY["snapshot"],
        annotation_generation_id=ANNOTATION_GENERATION_ID,
        mode=MODE,
        snapshot_is_fresh=SNAPSHOT_IS_FRESH,
        handoff_metadata=HANDOFF_METADATA,
        receipt=(
            json.loads(RECEIPT_DOCUMENT)
            if MODE == "github" and RECEIPT_DOCUMENT
            else None
        ),
    )
    if SNAPSHOT_IS_FRESH:
        publication_generation_id = PUBLICATION["artifacts"]["raw_findings"][
            "review_generation_id"
        ]
        if publication_generation_id != REVIEW_GENERATION_ID:
            raise RuntimeError("publication generation changed after external effects")
    PUBLICATION_RESULT = publish_experimental_review_artifacts(
        publication=PUBLICATION,
        output_dir=REVIEW_OUTPUT_DIR,
        pr_number=str(pr_number),
    )
```

This publisher is the only writer of the fixed raw-findings, diff-context,
GitHub-receipt, and local-findings paths. Its executable order is raw findings,
diff context, then the GitHub receipt; local mode instead publishes local findings
last. Every downstream finding is copied from `FINAL_REVIEW_FINDINGS`, retains
opaque fields, and carries both `file` and the normalized `path` alias plus
`body`, `side`, and `code_region`.

**Write Raw Findings JSON (first):**

As the first publication, build the complete raw ledger in memory using the schema
specified below. The sole publisher renders it into a same-directory temporary path
and atomically renames it to
`${REVIEW_OUTPUT_DIR}raw_findings_{pr_number}.json`. Do not begin diff-context,
receipt, or local-findings publication until this rename succeeds. On
`stale_snapshot`, the raw ledger is a bounded diagnostic envelope with empty
survivor and publication sets.

**Write Diff-Scoped Context Handoff (before emitting verdict):**

After writing the summary file and before emitting the verdict token, write the handoff
file for resolve-review's pre-built context. This costs zero additional API calls or file
reads — all data is already in the session's context.

Do not output prose between iterations. For each finding in `FILTERED_FINDINGS` + `UNPOSTABLE_FINDINGS` where severity is
`"critical"` or `"warning"`, build a context entry:
- `path` — the finding's `file` field (the finding schema uses `file`, not `path`;
  map `finding.file` → `path` in the context entry for resolve-review compatibility)
- `line` — the finding's line number
- `severity` — `"critical"` or `"warning"`
- `dimension` — the audit dimension (arch, tests, bugs, etc.)
- `message` — the finding's message text
- `code_region` — extract from `ANNOTATED_DIFF`: find the file's section in the
  annotated diff (between its `diff --git` header and the next), then collect all
  lines whose `[LX]` marker has X within ±50 of the finding's `line`. Include those
  raw annotated-diff lines as-is. If ANNOTATED_DIFF is empty or the file section is
  not found, set `code_region` to `""`.

Write to `${REVIEW_OUTPUT_DIR}diff_context_{pr_number}.json`. If it already exists,
replace it through the Step 8 same-directory temporary file and atomic rename:

```json
{
  "pr_number": 1234,
  "schema_version": 1,
  "written_at": "{ISO-8601 timestamp}",
  "context_entries": [
    {
      "path": "src/autoskillit/execution/headless.py",
      "line": 42,
      "severity": "critical",
      "dimension": "arch",
      "message": "...",
      "code_region": "[L40] ...\n[L41] ...\n[L42] ...",
      "evidence": [],
      "trace": [],
      "boundary_checks": [],
      "confidence": 0.98,
      "simpler_behavior": "...",
      "candidate_id": "...",
      "disposition_id": "...",
      "snapshot": {}
    }
  ],
  "_head_sha": "{METRICS_HEAD_SHA}",
  "_base_sha": "{METRICS_BASE_SHA}",
  "_merge_base_sha": "{METRICS_MERGE_BASE_SHA}",
  "annotation_generation_id": "{ANNOTATION_GENERATION_ID}",
  "review_generation_id": "{REVIEW_GENERATION_ID}"
}
```

Log: `"Wrote diff-scoped context handoff: N entries → {path}"`. If the write fails
(e.g., temp dir unavailable), log a warning and continue — the handoff file is
best-effort and its absence is handled gracefully by resolve-review.

Do not independently render or rename this fixed path: the helper invocation above
normalizes and publishes it in the same transaction as raw findings and the final
receipt/local marker.

**Raw Findings JSON schema (published first):**

The raw findings source ledger preserves
standard findings, experimental candidates, validation, disposition, aggregation,
verdict-use, publication, rejected-candidate, and malformed-envelope records.

Write to `${REVIEW_OUTPUT_DIR}raw_findings_{pr_number}.json`:

```json
{
  "pr_number": 1234,
  "candidate_records": [
    {
      "file": "src/autoskillit/execution/headless.py",
      "line": 42,
      "severity": "critical",
      "dimension": "arch",
      "message": "...",
      "candidate_id": "...",
      "record_digest": "..."
    }
  ],
  "validation_records": [],
  "disposition_records": [],
  "aggregation_records": [],
  "verdict_use_records": [],
  "publication_records": []
}
```

Include all standard findings and every experimental candidate/disposition, while
keeping rejected candidates out of effect-bearing survivor sets. Each publication
record captures candidate ID, intended body digest, remote identifier/status,
authoritative commit ID, and retry outcome. Log:
`"Wrote raw findings: N entries → {path}"`.

The summary records accepted/rejected counts and closed reasons, evidence that
affected the verdict, and any gate/audit/snapshot degradation. GitHub rendering
contains compact repository-relative evidence and accepted experimental dimensions
only.

Use the first-publication transaction above; this schema section does not authorize
a second write. Never redirect directly to the fixed destination. Do not use inline
Python one-liners or heredoc scripts with `open()` — these are blocked by the
sandbox.

Output the verdict as the final line:

> **IMPORTANT:** Emit the structured output tokens as **literal plain text with no
> markdown formatting on the token names**. Do not wrap token names in `**bold**`,
> `*italic*`, or any other markdown. Do not wrap the output block in a code fence.
> The adjudicator performs a regex match on the exact token name — decorators and
> code fences cause match failure.

```
verdict = {approved|approved_with_comments|changes_requested|needs_human|stale_snapshot}
```

Immediately after the verdict line, emit the review gate tag on a new line:

- If `verdict = changes_requested`: emit `%%REVIEW_GATE::LOOP_REQUIRED%%`
- If `verdict = approved` or `verdict = needs_human`: emit `%%REVIEW_GATE::CLEAR%%`
- If `verdict = approved_with_comments`: do NOT emit a gate tag
- If `verdict = stale_snapshot`: emit no review gate tag; the recipe routes directly
  through its bounded annotation-refresh waypoint

Exit 0 in all normal cases (approved, approved_with_comments, needs_human,
changes_requested, stale_snapshot).
Exit 1 only for unrecoverable tool-level errors.

**Network Failure Degradation (Codex sandbox):**

When `gh api` exits non-zero and the error output contains a network/connection
error (e.g., `curl: (7) Failed to connect`, `Could not resolve host`) rather
than an API-level error (HTTP 4xx/5xx), the `gh` binary is present but the
sandbox blocks outbound network access. In this case:

- Set `verdict=needs_human` and emit `%%REVIEW_GATE::CLEAR%%`
- Exit 0 — this is a sandbox constraint, not a skill failure
- Log: `"gh api network error in sandbox — setting verdict=needs_human"`

The `needs_human` verdict is in `_SAFE_DEGRADATION_VERDICTS`, so the
`verdict-ungated-degradation` rule does not fire for this path.

## Output

- `verdict=approved` → `%%REVIEW_GATE::CLEAR%%` — No blocking issues; CI can proceed
- `verdict=approved_with_comments` — no gate tag — Warning-only findings; recipe routes to `resolve_review` but does not require a re-review cycle
- `verdict=changes_requested` → `%%REVIEW_GATE::LOOP_REQUIRED%%` — Blocking issues found; recipe routes to `resolve_review`
- `verdict=needs_human` → `%%REVIEW_GATE::CLEAR%%` — Uncertain trade-offs; human review requested via the authenticated GitHub user mention (derived at runtime)
- `verdict=stale_snapshot` — no gate tag or effect-bearing artifact; recipe refreshes
  annotation within its existing bounded recovery path

Summary written to: `${REVIEW_OUTPUT_DIR}summary_{pr_number}_{timestamp}.md`

**Mode-conditional path output:**

When `mode=local`, the following token is emitted:
```
local_findings_path = ${REVIEW_OUTPUT_DIR}local_findings_{pr_number}.json
```

When `mode=github`, no local_findings_path token is emitted (findings are posted directly to GitHub).
