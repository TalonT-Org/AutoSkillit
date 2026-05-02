---
name: audit-review-decisions
categories: [audit]
description: >
  Audit merged PR review threads for agreed-but-deferred suggestions (design decisions,
  future work, out-of-scope items) that were never implemented. Mines REVIEW-FLAG markers
  from resolve-review and legacy keyword signals. Produces a structured markdown report
  with VALID/RESOLVED/STALE classifications and annotates processed threads with [AUDIT]
  markers to prevent re-identification on future runs.
---

# Audit Review Decisions Skill

Mine merged PR review threads for agreed-but-deferred suggestions that were never
implemented. Identify review debt before it compounds.

## When to Use

- User says "audit review decisions", "find deferred review items", "surface review
  debt", "what did reviewers flag for later"

## Arguments

- `$1` — Time period (e.g. `14d`, `30d`, `7d`). Default: `14d`.
- `$2` — Output path. Default:
  `${AUTOSKILLIT_TEMP}/audit-review-decisions/review_decisions_audit_$(date +%Y-%m-%d_%H%M%S).md`

## Critical Constraints

**NEVER:**
- Create files outside `${AUTOSKILLIT_TEMP}/audit-review-decisions/`
- Have triage or validation subagents make GitHub API calls (local data only for Phase 2)
- Post duplicate `[AUDIT]` markers — check for existing marker before posting
- Run subagents in the background (`run_in_background: true` is prohibited)
- Use `gh pr list` without `--limit` to avoid pagination truncation
- Use `\|` in Grep patterns — use `|` for alternation (ERE, not BRE)

**ALWAYS:**
- Save raw PR JSON to temp before any analysis (Phase 1)
- Use GraphQL alias batching (~20 PRs per query) for data collection
- Include `rateLimit { cost remaining resetAt }` in every GraphQL query
- Sleep 1s between consecutive mutating GitHub API calls (Phase 5 watermark posts)
- Phase 2 triage subagents read local JSON files only — zero API calls
- Phase 3 validation subagents grep the actual current codebase
- Skip threads that already contain an `[AUDIT]` comment
- Resolve owner/repo from `git remote get-url origin` — never hardcode
- Use `/autoskillit:` prefix when invoking any other skill

---

## Workflow

### Phase 0: Watermark Resolution

1. Parse `$1` for time period. Default `14d`. Compute `PERIOD_DAYS`.

2. Resolve `OWNER` and `REPO` from `git remote get-url origin`.

3. Query the most recent `[AUDIT]` sentinel comment across recently merged PRs:
   ```bash
   gh api graphql -f query='
     query($owner:String!, $name:String!) {
       rateLimit { cost remaining resetAt }
       repository(owner:$owner, name:$name) {
         pullRequests(first:50, states:MERGED, orderBy:{field:UPDATED_AT,direction:DESC}) {
           nodes { number
             reviewThreads(first:50) {
               nodes { comments(first:10) { nodes { body createdAt } } }
             }
           }
         }
       }
     }' -f owner="${OWNER}" -f name="${REPO}"
   ```
   Extract the most recent `createdAt` from any comment whose `body` starts with
   `[AUDIT]`. Store as `LAST_AUDIT_TS` (empty string if none — first run).

4. Compute `SCAN_SINCE`:
   - If `LAST_AUDIT_TS` is set: `max(LAST_AUDIT_TS, date -d "now - PERIOD_DAYS days")`
   - Else: `date -d "now - PERIOD_DAYS days" --iso-8601=seconds`

5. Log: `Scan window: ${SCAN_SINCE} to now (${PERIOD_DAYS}d configured, last audit: ${LAST_AUDIT_TS:-none})`

---

### Phase 1: Data Collection (GraphQL Batch)

1. List merged PRs in the scan window:
   ```bash
   SCAN_DATE=$(echo "${SCAN_SINCE}" | cut -c1-10)
   PR_NUMS=$(gh pr list --state merged \
     --search "merged:>=${SCAN_DATE}" \
     --json number --limit 500 | jq -r '.[].number')
   ```

2. Create temp directory:
   ```bash
   mkdir -p "${AUTOSKILLIT_TEMP}/audit-review-decisions/raw"
   ```

3. Batch fetch in groups of 20 using GraphQL aliases. For each batch, build a query
   with aliased `pr${i}: pullRequest(number: ${NUM})` nodes. Each node fetches:
   ```graphql
   number title mergedAt
   reviews(first: 100) {
     nodes { author { login } body state submittedAt }
   }
   reviewThreads(first: 100) {
     pageInfo { hasNextPage }
     nodes {
       isResolved
       comments(first: 20) {
         nodes { databaseId author { login } body path line createdAt }
       }
     }
   }
   ```
   Include `rateLimit { cost remaining resetAt }` at query root.

4. For each PR in the batch response:
   - Filter out threads whose `comments` list contains any comment with `body`
     starting with `[AUDIT]` (already watermarked — skip entirely).
   - If the PR has zero remaining threads: skip saving.
   - Otherwise: save filtered data to
     `${AUTOSKILLIT_TEMP}/audit-review-decisions/raw/pr_${number}.json`

---

### Phase 2: Triage (Haiku — Broad Pass)

1. List all JSON files in `raw/`. Split into batches of ~5 files per agent.

2. Launch **parallel Haiku subagents** (one per batch, `model: "haiku"`). Each agent:
   - Reads its assigned JSON files only (no API calls).
   - Flags a thread if it matches any signal:
     - `<!-- REVIEW-FLAG:` tag present
     - Body contains one of: `"Valid observation — flagged for design decision"`,
       `"out of scope for this fix cycle"`, `"requires a dedicated cleanup commit"`,
       `"left open for human review"`, `"future improvement"`, `"beyond this PR's scope"`,
       `"requires team consensus"`
     - Thread `isResolved: false` AND author acknowledged validity in a reply
     - Review body `state: COMMENTED` with no corresponding thread (needs_human indicator)
   - Returns candidates as **response text only — no file writes**. Per-candidate format:
     ```
     PR: {number}
     thread_index: {N}
     comment_id: {databaseId of first comment in thread}
     path: {file path or empty}
     line: {line number or empty}
     signal: REVIEW-FLAG|KEYWORD|UNRESOLVED|NEEDS_HUMAN
     severity: {from REVIEW-FLAG tag, or "unknown"}
     dimension: {from REVIEW-FLAG tag, or "unknown"}
     quote: {first 200 chars of flagged comment body}
     ```
   - False positives are acceptable; false negatives are not.

3. Collect and parse candidate text from all agent responses.

---

### Phase 3: Validation (Sonnet — Deep Pass)

1. Group candidates into batches of ~10. Launch **parallel Sonnet subagents**
   (`model: "sonnet"`) per batch.

2. Each Sonnet agent receives its candidate batch and, for each candidate:
   - If `path` is set: reads the file and surrounding context.
   - Greps the current codebase for the core concern from `quote` (judgment-based
     pattern, not a literal string match).
   - Classifies:
     - `VALID` — issue still present, impactful, ticket-worthy
     - `RESOLVED` — code changed; concern no longer applies
     - `STALE` — code deleted/refactored; finding irrelevant
   - Returns findings as **response text only — no file writes**. Per-finding format:
     ```
     PR: {number}
     comment_id: {databaseId}
     classification: VALID|RESOLVED|STALE
     path: {file:line or empty}
     severity: {critical|warning|info|unknown}
     dimension: {arch|bugs|defense|tests|cohesion|slop|unknown}
     priority: HIGH|MEDIUM|LOW
     impact: {one sentence}
     suggested_title: {short GitHub issue title}
     reviewer_quote: {verbatim first 300 chars}
     ```
   - Priority assignment: `HIGH` = severity=critical OR dimension in (bugs, arch);
     `MEDIUM` = severity=warning; `LOW` = severity=info or unknown.

3. Collect and parse validated findings from all agent responses.
