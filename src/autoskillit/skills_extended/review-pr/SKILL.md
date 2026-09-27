---
name: review-pr
write_paths:
- '{{AUTOSKILLIT_TEMP}}/review-pr/'
categories:
- github
description: Automated diff-scoped PR code review using parallel audit subagents. Posts inline GitHub review comments and submits a summary verdict. Use after a PR is opened to gate CI on review approval.
hooks:
  PreToolUse:
  - matcher: '*'
    hooks:
    - type: command
      command: 'echo ''[SKILL: review-pr] Reviewing pull request...'''
      once: true
semantic_version: 1
semantic_requirements:
  logical_roles:
  - name: pr-review-auditor-abstraction-surface
    purpose: perform the named independent responsibility and return bounded evidence
  - name: pr-review-auditor-reachability
    purpose: perform the named independent responsibility and return bounded evidence
  child_spawns:
  - role: pr-review-auditor-abstraction-surface
    count: 1
  - role: pr-review-auditor-reachability
    count: 1
  concurrency:
    required: true
  join:
    required: true
  evidence:
    required: true
    independent: true
  child_model_policies:
  - role: pr-review-auditor-abstraction-surface
    model_class: sonnet
  - role: pr-review-auditor-reachability
    model_class: sonnet
---

# Review PR Skill

Perform an automated, diff-scoped code review on an open GitHub PR using parallel
audit subagents. Posts inline review comments and submits a summary verdict. Called
by the recipe pipeline after `open_pr_step` opens the PR.

## Arguments

- **anchor_authority_path** (optional) — caller-supplied annotation authority artifact,
  bound to repository, PR, and head SHA. Required for inline comments; when absent,
  publish findings in the review body only. Pass the path unchanged to the guarded
  publication call.

`/autoskillit:review-pr <feature-branch> <base-branch> [annotated_diff_path=<path>] [hunk_ranges_path=<path>] [valid_lines_path=<path>] [anchor_authority_path=<path>] [diff_metrics_path=<path>] [mode=<local|github>]`

- **feature-branch** — The feature branch containing the changes to review
- **base-branch** — The base branch the PR targets (e.g., "main")
- **annotated_diff_path** (optional) — absolute path to a pre-computed annotated diff file (produced by `annotate_pr_diff` run_python step). When provided and present, read from file instead of running python3.
- **hunk_ranges_path** (optional) — absolute path to a pre-computed hunk ranges JSON file (produced by `annotate_pr_diff` run_python step). When provided and present, read from file instead of running python3.
- **valid_lines_path** (optional) — absolute path to the right-side valid-line annotation JSON produced by `annotate_pr_diff`. Retained as analysis evidence; inline admission uses the authority artifact.
- **diff_metrics_path** (optional) — absolute path to a pre-computed diff metrics JSON file (produced by `annotate_pr_diff` run_python step). Contains `dispatch_agents` list that determines which audit dimensions to spawn. When absent, all 6 standard agents are dispatched.
- **mode** (optional, default: `github`) — Controls where findings are written:
  - `mode=github` (or absent/unrecognized): current behavior — post findings as GitHub inline review comments via the GitHub Reviews API.
  - `mode=local`: write `local_findings_{pr_number}.json` with `findings` and `iteration`
    fields; skip all GitHub API calls for comment posting and skip publication. Still write
    `diff_context_{pr_number}.json`, `raw_findings_{pr_number}.json`, and the summary. Gate
    tokens are mode-independent.

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
- Mutating checked-out refs is prohibited during review. Review is observational; do not rewrite refs, `HEAD`, the index, or worktree state to satisfy an authority check.
- Detach child delegations instead of joining them (joining every child is required)
- Start independent child delegations sequentially
- Specify `subagent_type` for standard or deletion audit agents. The only permitted
  registered calls are the exact reachability and abstraction-surface calls in Step 3.
- Give standard or deletion agents repository-read access. Only the two registered
  proof-only auditors may use `Read`, `Grep`, and `Glob`, and only under
  `{checkout_root}`.
- Embed diff content inline in standard or deletion subagent prompts; those ephemeral
  agents continue to consume the annotated artifact path.
- Pass an experimental auditor only artifact paths or a narrative. Both registered calls
  must receive the actual annotated `[LNNN]` content and exact valid-line authority.
- Transcribe, merge, summarize, repair, or re-type any auditor output. Auditor results reach
  validation only through `collect_review_audit` and `finalize_review_audit`, which read each
  child's own transcript.
- Compute, assume, or hard-code the gate state, audit state, candidate or disposition
  identity, or verdict. Use the values `plan_review_audit` and `finalize_review_audit` return.

**ALWAYS:**
- Find the PR by feature branch at invocation time (not from a pre-captured URL)
- Output `verdict=` on the final line
- Exit 0 in all normal cases; verdict drives recipe routing via on_result, not exit code
- Exit non-zero only for unrecoverable errors (e.g., gh CLI truly unavailable after graceful degradation has already output verdict=needs_human)
- Tag the authenticated GitHub user (`gh api user -q .login`) in escalation comments (`needs_human` verdict) — omit the mention silently if username derivation fails
- Spawn all subagents via `child delegation under the declared `sonnet` model-class policy`
- Bind the checkout root, refs, exact diff, manifest generation, agent working directory,
  parent evidence reads, and every effect to the same metrics authority
- Revalidate checkout/live refs and the byte-identical metrics marker immediately before
  verdict, artifact handoff, or GitHub mutation
- Deduplicate findings by (file, line) pairs before posting
- Start all independent child delegations before awaiting any result to maximize concurrency
- Start every auditor prompt with its slot's `marker_line`, label the child with its
  `slot_token`, keep the child identifier the launch returns, and relaunch exactly the slots
  `collect_review_audit` lists.
- For each manual fixed-name publication, print a fresh timestamp plus UUID as
  `publish_id`, paste it into the literal same-directory path
  `{review_output_dir}{artifact_name}.tmp-{publish_id}`, write there, then
  run `mv -- "{review_output_dir}{artifact_name}.tmp-{publish_id}" "{review_output_dir}{artifact_name}"`.
  Never redirect directly to the fixed destination. Never `open(path, 'w')`
  or `.write_text()` inside a `python3` heredoc or `python3 -c`
  invocation; keep artifact writes within the declared review output directory.

## Workflow

### Step 0: Validate Arguments

Print the allowed output directory without writing to it:

```bash
printf '%s\n' "${AUTOSKILLIT_ALLOWED_WRITE_PREFIX:-{{AUTOSKILLIT_TEMP}}/review-pr/}"
```

Paste the printed value as a literal path in the next commands. Create it,
then resolve its canonical absolute path:

```bash
mkdir -p "{printed_output_dir}"
cd "{printed_output_dir}" && pwd -P
```

Use the printed canonical path with a trailing `/` as `{review_output_dir}`
everywhere below. Paste the actual path, not the brace notation, into every
shell write target. Run `git rev-parse --show-toplevel` in the review checkout
and paste its printed absolute path as `{checkout_root}` wherever this skill
reads the review checkout.

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
JSON file instead. Note: `annotate_pr_diff` still calls `gh api` to cross-validate the
provider head/base SHAs against the local checkout before trusting the local diff; this
is a security cross-check, not a diff-fetch dependency.

### Step 1: Find the Open PR

```bash
pr_lookup="$(gh pr list --head "$feature_branch" --base "$base_branch" \
  --json number,url -q '.[0] | "\(.number) \(.url)"')"
read -r pr_number pr_url <<EOF
$pr_lookup
EOF
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

has_marker_reply = any(RESOLVED_MARKER_RE.search(c.get("body", "")) for c in thread_comments[1:])

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

Save to: `{review_output_dir}prior_threads_{pr_number}.json`

Print a fresh `publish_id` for this publication:

```bash
printf '%s-%s\n' "$(date -u +%Y%m%dT%H%M%S%N)" "$(python3 -c 'import uuid; print(uuid.uuid4())')"
```

Paste the printed value into both literal paths. Render with `jq -n` into
`{review_output_dir}prior_threads_{pr_number}.json.tmp-{publish_id}`, then
atomically `mv` that path to
`{review_output_dir}prior_threads_{pr_number}.json`. If using the Write tool,
write the same literal temporary path first and rename it. Never redirect
directly to the fixed destination. Do not use inline Python one-liners or
heredoc scripts with `open()` — these are blocked by the sandbox.

If the GraphQL call fails (token scope, network): set both lists to `[]` and log a warning.
Prior-thread context is best-effort — failure must not abort the review.

### Step 2: Get PR Diff and Metadata

```bash
# Get the PR diff
gh pr diff {pr_number}

# Get owner/repo
gh repo view --json nameWithOwner -q .nameWithOwner
```

Save the diff to `{review_output_dir}diff_{pr_number}.txt`. (relative to the current working directory)

### Step 2.7: Deterministic Diff Annotation

Treat `metrics_{pr_number}.json` as the commit marker for one immutable annotation
generation. The gate has three states: `valid_true`, `valid_false`, or `degraded`.
Freshness and the complete artifact manifest MUST validate before consuming the
gate boolean. The review LLM never counts lines or infers eligibility.

Recipe-provided artifact paths bypass preparation. For a standalone local review,
if any of the metrics, annotated-diff, hunk-range or valid-lines paths is missing,
stop with `verdict=needs_human`, print `%%REVIEW_GATE::CLEAR%%`, and exit
in a headless session. In an interactive session, run this complete command:

```bash
mktemp -d "{review_output_dir}annotation.XXXXXX"
```

Paste its printed absolute path as `{annotation_output_dir}`. Invoke the MCP
helper exactly once:

```text
annotation_result = run_python(
    callable="autoskillit.smoke_utils.annotate_pr_diff",
    args={
        "pr_number": pr_number,
        "cwd": "{checkout_root}",
        "output_dir": "{annotation_output_dir}",
        "base_branch": base_branch,
        "mode": "local",
    },
    timeout=120,
    work_dir="{checkout_root}",
)
```

Require `annotation_result.success=true` and a mapping-valued
`annotation_result.result`. On failure, emit `verdict=needs_human`, print
`%%REVIEW_GATE::CLEAR%%`, and stop. On success, paste its returned
`diff_metrics_path`, `annotated_diff_path`, `hunk_ranges_path`,
`valid_lines_path`, and `anchor_authority_path` as literal paths for the
remaining steps. Keep `anchor_authority_path` for its later consumer.
Use the recipe-provided literal paths directly when they already exist.

Run the gate with the actual values pasted into every argument:

```bash
bash "{{AUTOSKILLIT_SCRIPTS}}/review_pr_gate.sh" snapshot "{review_output_dir}" "{checkout_root}" "{mode}" "{pr_number}" "{diff_metrics_path}" "{annotated_diff_path}" "{hunk_ranges_path}" "{valid_lines_path}"
```

If `snapshot` exits non-zero, stop before evidence reads, verdict computation
or any GitHub mutation; emit `verdict=needs_human` and
`%%REVIEW_GATE::CLEAR%%`. Do not use partial stdout as authority.

On success, parse the one printed JSON object as `GATE_AUTHORITY`. Retain its
`authority_path` as a literal path for every later revalidation call. Bind
`GATE_STATE`, `GATE_REASON_CODE`, and `EXPERIMENTAL_AUDIT_STATE` from it;
bind `METRICS_HEAD_SHA`, `METRICS_BASE_SHA`,
`METRICS_MERGE_BASE_SHA`, `DIFF_SHA256`, and
`ANNOTATION_GENERATION_ID` from its `snapshot` and generation fields.
Take `metrics_marker_snapshot_path`, `annotated_diff_snapshot_path`,
`hunk_ranges_snapshot_path`, and `valid_lines_snapshot_path` from the
same mapping. Paste those paths literally in later shell calls; do not rely
on shell state from the snapshot call.

For a valid gate, read evidence only from the retained paths:

```bash
tail -n +2 "{annotated_diff_snapshot_path}"
cat "{hunk_ranges_snapshot_path}"
cat "{valid_lines_snapshot_path}"
```

Bind these outputs in order as `ANNOTATED_DIFF`, `VALID_LINE_RANGES`,
and `VALID_DIFF_LINES`. Strip trailing LF bytes from the annotated-diff
output as the former command substitution did. For a degraded gate, use an
empty annotated diff, `{}` hunk ranges and empty valid lines.

`VALID_DIFF_LINES` is right-side annotation evidence. All findings use the
explicit anchor authority for inline admission; no finding may fall back to
hunk ranges. Every Git command, agent working directory, containment check,
and parent evidence read uses `{checkout_root}`.

Immediately before evidence reads, verdict computation, every GitHub mutation,
and the single handoff publication, run:

```bash
bash "{{AUTOSKILLIT_SCRIPTS}}/review_pr_gate.sh" revalidate "{authority_path}"
```

Set `FINAL_SNAPSHOT_STATE` to the single printed word only if the command
exits zero and prints exactly `fresh`, `stale`, or
`authority_degraded`. Otherwise set it to `authority_degraded` and route
to `needs_human`. A missing or malformed authority file takes this fallback;
it never becomes a stale snapshot. Only a previously valid retained authority that later
fails byte/ref revalidation becomes `stale`. Never reread publisher sidecars
or adopt a newer generation.

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
printf 'MERGE_BASE=%s\n' "$MERGE_BASE"
```

Store as `deletion_context`:
```python
deletion_context = {
    "merge_base": MERGE_BASE,
    "deleted_files": DELETED_FILES.splitlines(),  # list of paths
    "deleted_symbols": DELETED_SYMBOLS.splitlines(),  # list of "def foo", "class Bar"
    "pr_base": PR_BASE,
}
```

Paste the printed `MERGE_BASE` value (empty when unavailable) as
`{DELETION_MERGE_BASE}`. `plan_review_audit` applies deletion eligibility independently of the
overengineering gate; the deletion audit runs only when the plan returns a `deletion` slot.

### Step 2.9: Deterministic Audit Plan

The plan applies the standard adaptive-selection tiers, experimental eligibility, and deletion
eligibility as separate authorities. It reads the retained authority and artifacts, then returns
the manifest and ordered slots that Step 3 launches.

```text
AUDIT_PLAN = plan_review_audit(
    authority_path="{authority_path}",
    review_output_dir="{review_output_dir}",
    deletion_merge_base="{DELETION_MERGE_BASE}",
    anchor_authority_path="{anchor_authority_path}",
    repository="{repository}",
)
```

When `anchor_authority_path` is absent, pass `""`. The plan builds unavailable anchor
authority from `repository` and the retained snapshot head, preserving body-only review mode.
Require `AUDIT_PLAN.success`. Otherwise emit `verdict=needs_human` and
`%%REVIEW_GATE::CLEAR%%`, then stop before launching a child. Bind
`AUDIT_MANIFEST_PATH`, `AUDIT_SLOTS`, and `EXPERIMENTAL_AUDIT_STATE` from the plan result.

`GATE_STATE=valid_false` dispatches neither proof-only auditor and leaves
`EXPERIMENTAL_AUDIT_STATE=not_required`; by itself it does not block approval.
`GATE_STATE=degraded` dispatches neither and blocks normal approval. Never derive the
gate from churn counts, truthiness, hooks, environment variables, transcripts,
sidecars, or model reasoning.

**Agent selection tiers:**
- **Small diff** (<200 added LoC and <5 changed files): `tests`, `cohesion`, and optionally `arch` if structural files changed (e.g., `__init__.py`, `pyproject.toml`).
- **Medium/large diff** (>= 200 added LoC or >= 5 changed files): All 6 standard agents (`arch`, `tests`, `defense`, `bugs`, `cohesion`, `slop`).

The plan keeps standard, deletion, and experimental slots separate. Experimental degradation
never clears, cancels, replaces, or dynamically subtracts standard calls. Missing or malformed
adaptive selection falls back to all six standard agents and never adds a proof-only auditor to
that fallback.

### Step 3: Run Parallel Audit Subagents (SINGLE MESSAGE)

Launch one child per `AUDIT_SLOTS` entry.

**Issue ALL child delegations in a single message — one per dimension — so they execute
in parallel. Do NOT iterate through dimensions across multiple turns.**

Do not output any prose between subagent dispatches. Immediately proceed to the next tool call.

Iterate `AUDIT_SLOTS` in order and issue one child delegation per slot in the single parallel
message. Initialize `AUDIT_HANDLES = {}` before the wave. A `kind == "standard"` slot uses the
dimensions 1–6 template for its `dimension`;
`kind == "deletion"` uses the dimension 7 template; `kind == "experimental"` uses the exact
registered logical-role call for its `producer` below and runs with cwd `{checkout_root}`.
Start every child prompt with that slot's `marker_line` verbatim, and use its `slot_token` as
the delegation label. If a join declaration precedes the wave, use the same `slot_token` for its
assignment label. Bind `AUDIT_HANDLES[slot_id]` to the identifier the delegation returns for
that child. Standard and deletion children receive only PR diff content.

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
   Spawned only when the plan returns a `deletion` slot.

8. **overengineering_reachability** — Packless, proof-only, repository-reading
   reachability audit. It is eligible only for `GATE_STATE=valid_true`.

9. **overengineering_abstraction_surface** — Packless, proof-only,
   repository-reading abstraction-surface audit. It is eligible only for
   `GATE_STATE=valid_true`.

When eligible, issue these calls exactly once in the same foreground parallel message
as the standard and deletion calls:

- `a child assigned logical role `pr-review-auditor-reachability` under its declared model policy`
- `a child assigned logical role `pr-review-auditor-abstraction-surface` under its declared model policy`

For both registered calls, inline the actual `ANNOTATED_DIFF` string, the exact
`VALID_DIFF_LINES` JSON authority, `{checkout_root}`, `METRICS_HEAD_SHA`,
`METRICS_BASE_SHA`, `METRICS_MERGE_BASE_SHA`, `DIFF_SHA256`, and
`ANNOTATION_GENERATION_ID` in the prompt. A path, placeholder, or description is
insufficient. Reads are restricted to the current checkout root; modifications and
network access are forbidden.

After every launched child has reported completion, collect its own transcript:

```text
AUDIT_COLLECTION = collect_review_audit(
    manifest_path="{AUDIT_MANIFEST_PATH}",
    handles={AUDIT_HANDLES},
)
```

If `AUDIT_COLLECTION.success` is false, emit `needs_human`, emit
`%%REVIEW_GATE::CLEAR%%`, and stop. While `AUDIT_COLLECTION["relaunch"]` is non-empty,
launch one replacement child per listed entry in a single message, using the same template,
`marker_line`, and `slot_token` label. Replace those `AUDIT_HANDLES` entries and call
`collect_review_audit` again. Each relaunch wave is a new delegation wave under the same
backend execution contract. The server bounds attempts per slot; never skip a listed relaunch
or relaunch an unlisted slot.

Any tool failure, refusal, interruption, truncation, missing result, malformed JSON,
non-array result, or schema-invalid item is detected by `collect_review_audit` from the child's
own transcript. This includes output-limit stops and a missing final message, and leaves that
slot unvalidated. A valid `[]` is a successful empty result. One success plus one failure produces
no partial experimental findings. `EXPERIMENTAL_CANDIDATES` is
`AUDIT_COLLECTION["experimental_candidates"]`, populated only when both experimental slots
validated.

Subagent prompt template (dimensions 1–6):

> You are reviewing a GitHub PR diff for [{dimension}] issues only.
> Scope: examine only the diff content provided. Do not fetch or read files outside the diff.
> End your final message with exactly one fenced code block whose opening line is
> ```` ```json ```` and whose closing line is ```` ``` ````, containing the complete JSON
> array of findings. If there are no findings, put an empty array [] in that block. Do not emit
> any other json block in the final message.
> Each finding must have:
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
follows the plan-owned graceful-degradation fallback documented in Step 2.9.

Subagent prompt template (dimension 7 — deletion_regression, only when the plan returns a `deletion` slot):

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
> - If no regressions are found, the findings array in the final block is empty.
>
> End your final message with exactly one fenced code block whose opening line is
> ```` ```json ```` and whose closing line is ```` ``` ```, containing the complete JSON
> array of findings. If there are no findings, put an empty array [] in that block. Do not emit
> any other json block in the final message.

### Step 4: Aggregate and Deduplicate Findings

`collect_review_audit` reads each child's own transcript, enforces the output schema, and returns
only validated findings. The experimental candidate keys are exactly `file`, `line`, `dimension`,
`severity`, `message`, `requires_decision`, `evidence`, `trace`, `boundary_checks`,
`confidence`, and `simpler_behavior`; reject missing or extra keys. The collector enforces:

- `dimension` is exactly `overengineering_reachability` or
  `overengineering_abstraction_surface`; `severity` is `critical`, `warning`, or `info`;
  `requires_decision` is an exact boolean.
- `file`, `message`, and `simpler_behavior` are non-empty strings after trimming. Primary,
  evidence, and trace lines are positive integers excluding booleans, and the primary
  `(file, line)` must occur in exact `VALID_DIFF_LINES`, never only a hunk range.
- Evidence items have exactly `{path,line,role,claim}`, include at least two distinct
  repository-relative `path:line` locations, and use only `anchor`, `caller`, `consumer`,
  `registration`, `invariant`, or `counterevidence_checked`; every `path`, `role`, and `claim`
  is a non-empty string after trimming.
- Trace items have exactly `{path,line,relation}` and form a non-empty ordered chain; every
  `path` and `relation` is a non-empty string after trimming.
- Boundary checks contain exactly one `{boundary,status,claim}` row for each
  `reflection_decorators`, `dependency_injection`, `plugin_registry`, `cli_entrypoint`,
  `serialization`, `generated_code`, and `public_api`. Status is
  `checked_absent`, `checked_no_reachable_path`, or `not_applicable`; every boundary `claim`
  is a non-empty string after trimming.
- Paths are relative, contain no `..`, and canonically remain under `{checkout_root}`.
  `confidence` is exactly an integer or float, never boolean, finite, and in `[0,1]`.
  `simpler_behavior` covers return values, exceptions, ordering, persistence, concurrency,
  and compatibility.

The collector creates each
`record_digest` and `candidate_id` from the transcript result. Any malformed output is retained
only as a bounded envelope with producer, terminal status, byte length and digest, a bounded
excerpt or iteration-scoped raw reference, parse/schema errors, and rejection reason.

Before repository evidence reads, revalidate checkout head/base/merge-base, the mode-appropriate
live refs, the byte-identical metrics marker, diff identity/profile, and all artifact digests. Quote
and read each cited location under `{checkout_root}`. The parent must verify every role-labelled
evidence claim, every one of the seven boundary claims, every hop in the complete ordered trace as
a reachable chain, and the proposed simpler behavior's semantic equivalence for return values,
exceptions, ordering, persistence, concurrency, and compatibility. Missing, contradictory, or
unverified claims reject the candidate; the parent may not accept a sampled subset. Confidence
never implies acceptance.

After those evidence reads, record one disposition per `EXPERIMENTAL_CANDIDATES` item in
`AUDIT_DISPOSITIONS`. Each entry contains exactly `candidate_id`, `reason_code`, and `explanation`;
use a reason code from the closed list below and keep the explanation within 1 KiB UTF-8. The
server mints disposition identities. The closed disposition/rejection reason codes are:
`accepted`, `schema_invalid`, `path_escape`, `not_changed_line`, `stale_snapshot`,
`insufficient_evidence`, `boundary_unchecked`, `reachable_counterexample`,
`simpler_behavior_not_equivalent`, `suppressed_prior_thread`, `duplicate_candidate`, and
`publication_failed`.

`finalize_review_audit` is the single normal-aggregation boundary. Feed only parent-accepted
experimental findings into normal aggregation; standard and deletion findings enter without
experimental dispositions. It normalizes sources in fixed order: standard dimensions, deletion
regression, reachability, then abstraction-surface, preserving original array index. Suppress and
deduplicate exactly once across that combined sequence; do not append a second standard/deletion
list afterward.

The suppression pass runs before deduplication and removes findings matching
`prior_resolved_findings` by the same file and a line within ±5. Log
`"Suppressing finding at {file}:{line} — matches prior resolved thread"`. For experimental
candidates, create a linked immutable aggregation record with reason `suppressed_prior_thread`;
do not mutate the candidate or disposition. Deduplicate diff-anchored findings by `(file, line)`
using severity, `requires_decision=false`, source rank, and original array index, then record
every `dedup_group_id`, member, winner, and rationale; retain every member in the linked records.
Duplicate losers receive
`duplicate_candidate` records. Only exact anchors admitted by
`anchor_authority_path` may become inline comments. Unpostable findings retain their admission
reasons and appear in the review body's "Outside Diff Range" section. Unavailable or empty
authority admits no inline findings; missing authority permits body-only publication, never a
file-level or individual-comment fallback. Findings are bucketed as actionable, decision, or info
findings, and all findings, including unpostable ones, contribute to the verdict.

Immediately before finalization, revalidate the gate with the literal authority path:

```bash
bash "{{AUTOSKILLIT_SCRIPTS}}/review_pr_gate.sh" revalidate "{authority_path}"
```

Bind `FINAL_SNAPSHOT_STATE` from the one printed word using Step 2.7's failure rule, then call:

```text
AUDIT_FINALIZATION = finalize_review_audit(
    manifest_path="{AUDIT_MANIFEST_PATH}",
    handles={AUDIT_HANDLES},
    dispositions={AUDIT_DISPOSITIONS},
    prior_resolved_findings={prior_resolved_findings},
    final_snapshot_state="{FINAL_SNAPSHOT_STATE}",
)
```

If `AUDIT_FINALIZATION.success` is false, set `verdict=needs_human`, publish nothing effect-bearing,
and emit `%%REVIEW_GATE::CLEAR%%`. Otherwise bind
`FINAL_REVIEW_FINDINGS = FILTERED_FINDINGS = survivors` (already carrying `rendered_body`),
`UNPOSTABLE_FINDINGS = unpostable`, and `REVIEW_LEVEL_FINDINGS = review_level_findings` from
`AUDIT_FINALIZATION`. Set `INLINE_FINDINGS` to survivors whose `candidate_id` is not review-level,
then set `all_findings = FILTERED_FINDINGS + UNPOSTABLE_FINDINGS`.
Bind `AGGREGATION_RECORDS = aggregation_records`, `AUDITOR_RECORDS = auditor_records`, and
`verdict = AUDIT_FINALIZATION["verdict"]`. Record `FINALIZED_SNAPSHOT_STATE` as the state passed to
this finalization; Steps 5 and 8 re-finalize if a later check changes it.

Preserve accepted experimental evidence, trace, boundary checks, confidence, simpler behavior,
`candidate_id`, server-issued `disposition_id`, and snapshot on the dedup winner, diff context,
local handoff, GitHub rendering, and summary. Keep validation, disposition, aggregation, verdict
use, and publication as separate immutable linked records.

If an initially valid retained snapshot is now unavailable or differs, finalization discards
survivor sets from effect-producing consumers, permits only diagnostic raw/summary envelopes with
empty survivors, and emits `stale_snapshot`. In that movement branch revalidation sets
`FINAL_SNAPSHOT_STATE=stale` before any consumer is selected. Initial gate degradation remains
`authority_degraded` and emits `needs_human`. Do not publish diff context, local findings, receipts,
comments, reviews, or approvals from either state. On freshness, set
`COMMIT_ID="$METRICS_HEAD_SHA"` once and never query a later head to replace it.

### Step 4.5: Echo Primary Obligation

After aggregating all subagent findings, before proceeding to publication or posting, you MUST state aloud:

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
Immediately before this computation, run:

```bash
bash "{{AUTOSKILLIT_SCRIPTS}}/review_pr_gate.sh" revalidate "{authority_path}"
```

Bind `FINAL_SNAPSHOT_STATE` from the result using Step 2.7's failure rule.

`verdict` is `AUDIT_FINALIZATION["verdict"]`, computed by `finalize_review_audit` from the
retained gate state, collector-derived audit state, and `FINAL_SNAPSHOT_STATE`. It includes
`stale_snapshot` for a moved snapshot and `needs_human` for `authority_degraded`. Never
recompute, override, or assign a verdict literal. If this revalidation produces a
`FINAL_SNAPSHOT_STATE` different from the one last passed to `finalize_review_audit`, call
`finalize_review_audit` again with the same manifest, handles, dispositions, and prior resolved
findings from Step 4, pass the new snapshot state, and rebind the finalization result and its
derived findings, records, and verdict. Update `FINALIZED_SNAPSHOT_STATE` to the state passed.

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
        review_level_findings=REVIEW_LEVEL_FINDINGS,
        unpostable=UNPOSTABLE_FINDINGS,
        snapshot=GATE_AUTHORITY["snapshot"],
        annotation_generation_id=ANNOTATION_GENERATION_ID,
        mode=MODE,
        snapshot_is_fresh=True,
        handoff_metadata=HANDOFF_METADATA,
    )
    REVIEW_GENERATION_ID = PUBLICATION_SEED["artifacts"]["raw_findings"]["review_generation_id"]
else:
    PUBLICATION_SEED = None
    REVIEW_GENERATION_ID = ""
```

### Step 6: Publish the Complete Review

**MODE BRANCHING:**

Run the final freshness guard before entering this step and before every GitHub
mutation in it:

```bash
bash "{{AUTOSKILLIT_SCRIPTS}}/review_pr_gate.sh" revalidate "{authority_path}"
```

Bind `FINAL_SNAPSHOT_STATE` using Step 2.7's failure rule. If it yields
`stale_snapshot`, skip Steps 6-7 completely. Every GitHub mutation below uses the
authoritative `COMMIT_ID="$METRICS_HEAD_SHA"` and the same checkout/annotation
generation. Never replace it with a later HEAD query.

**When `mode=local`:**
- Skip ALL GitHub API calls for posting comments and reviews.
- Build the local findings payload in memory; Step 8 atomically publishes
  `{review_output_dir}local_findings_{pr_number}.json` last

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
- `{review_output_dir}diff_context_{pr_number}.json` (Step 8)
- `{review_output_dir}raw_findings_{pr_number}.json` (Step 8)
- `{review_output_dir}summary_{pr_number}_{timestamp}.md` (Step 8)

Then skip directly to Step 8 (verdict emission).

**Gate token emission is mode-independent:** `%%REVIEW_GATE::LOOP_REQUIRED%%` on `changes_requested`, `%%REVIEW_GATE::CLEAR%%` on `approved`/`needs_human` — emitted identically in both modes.

**When `mode=github`:**

Resolve `repository` from the canonical `nameWithOwner` value supplied by the caller and
require it to match `owner/repo`. Require a positive caller-supplied `pr_number`, and validate
the caller-supplied `pr_head_sha` against `^[0-9a-f]{40}$` before publication. Require the
caller-supplied `logical_iteration` to be namespaced and require `receipt_path` to be the
contained `batch_review_response_${pr_number}.json` destination under
`{review_output_dir}` for this invocation. Reject a logical iteration that does not start
with `review-pr:`.

Prepare one complete `comments` array from `INLINE_FINDINGS`, filtering at the publication
boundary to `severity == "critical"` or `severity == "warning"`. Preserve each validated
repository-relative `path`, `line`, and `side: "RIGHT"` anchor. Do not place
`UNPOSTABLE_FINDINGS` or `REVIEW_LEVEL_FINDINGS` in `comments`; summarize both partitions
in the complete review `body`.

Map the verdict to the requested event:

- `approved` → `APPROVE`
- `approved_with_comments` → `COMMENT`
- `needs_human` → `COMMENT`
- `changes_requested` → `REQUEST_CHANGES`

Call the structured publication tool once with the already-complete payload:

Render the unpostable partition into the complete review body before publication:

```python
from autoskillit.smoke_utils import render_review_finding_body, render_unpostable_review_section

REVIEW_BODY += render_unpostable_review_section(UNPOSTABLE_FINDINGS)
REVIEW_BODY += "\n".join(render_review_finding_body(finding) for finding in REVIEW_LEVEL_FINDINGS)
```

```text
post_pr_review(
  cwd: "$PWD",
  receipt_path: "$receipt_path",
  anchor_authority_path: "$anchor_authority_path",
  repository: "$repository",
  pr_number: "$pr_number",
  head_sha: "$pr_head_sha",
  logical_iteration: "$logical_iteration",
  event: "$REVIEW_EVENT",
  body: "$REVIEW_BODY",
  comments: "$COMMENTS_JSON",
  dry_run: false
)
```

Capture the authoritative result fields as `review_operation_key`, `review_head_sha`,
`review_post_state`, and `review_receipt_path`. Continue only for a confirmed or reconciled
final-success state. Stop without emitting a success verdict when the state is ambiguous,
throttled, terminal, posting, prepared, or verification-pending. The server owns identity,
remote reconciliation, response classification, safe validation handling, receipts, and
cross-session pacing.

### Step 6.5: Post-Completion Confirmation

Confirm the authoritative result identifies the requested repository, PR, head SHA, and
logical iteration; contains a positive review ID; and accounts for every original finding.
Do not infer publication from a local payload or from an HTTP response body.

**CRITICAL — No Local File Paths in GitHub Output:**
Never reference local file paths (e.g., `{{AUTOSKILLIT_TEMP}}/...`, `summary_*.md`, absolute paths) in the review body, inline comments, or any content posted to GitHub. The summary file is a local audit artifact only — GitHub readers cannot access local filesystem paths. Reference findings by file path and line number within the repository, not by local temp file locations.

### Step 7: Submit Summary Review

The complete summary was included in the Step 6 body. Preserve the structured publication
fields for the recipe effect gate and continue to the local audit artifacts; make no
additional GitHub review write.

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
winner ID, and rationale. The finalize-issued `AUDITOR_RECORDS` terminal-status
authority supplies child status. `RAW_LEDGER` starts from
`AUDIT_FINALIZATION["ledger_records"]` plus `pr_number`, `GATE_AUTHORITY`,
`verdict_use_records`, and `publication_records`. Include review mode, snapshot,
generations, accepted/rejected counts, and bounded malformed envelopes.

Save findings summary to `{review_output_dir}summary_{pr_number}_{timestamp}.md`. (relative to the current working directory)

Prepare and publish the structured artifacts with the installed executable
helpers. The final freshness check selects a complete or diagnostic-only
publication; the publisher stages every document before renaming and rolls back
any completed rename if a later boundary fails. Immediately before this block, run:

```bash
bash "{{AUTOSKILLIT_SCRIPTS}}/review_pr_gate.sh" revalidate "{authority_path}"
```

Bind `FINAL_SNAPSHOT_STATE` using Step 2.7's failure rule, then assign
`FINAL_SNAPSHOT_STATE == "fresh"` to the Python boolean `SNAPSHOT_IS_FRESH`.
Parse `RECEIPT_DOCUMENT` only when it is non-empty:

```text
if FINAL_SNAPSHOT_STATE != FINALIZED_SNAPSHOT_STATE:
    AUDIT_FINALIZATION = finalize_review_audit(
        manifest_path="{AUDIT_MANIFEST_PATH}",
        handles={AUDIT_HANDLES},
        dispositions={AUDIT_DISPOSITIONS},
        prior_resolved_findings={prior_resolved_findings},
        final_snapshot_state="{FINAL_SNAPSHOT_STATE}",
    )
    FINAL_REVIEW_FINDINGS = FILTERED_FINDINGS = AUDIT_FINALIZATION["survivors"]
    UNPOSTABLE_FINDINGS = AUDIT_FINALIZATION["unpostable"]
    REVIEW_LEVEL_FINDINGS = AUDIT_FINALIZATION["review_level_findings"]
    AGGREGATION_RECORDS = AUDIT_FINALIZATION["aggregation_records"]
    AUDITOR_RECORDS = AUDIT_FINALIZATION["auditor_records"]
    verdict = AUDIT_FINALIZATION["verdict"]
    FINALIZED_SNAPSHOT_STATE = FINAL_SNAPSHOT_STATE
```

```python
import json

from autoskillit.smoke_utils import (
    prepare_experimental_review_publication,
    publish_experimental_review_artifacts,
)

SNAPSHOT_IS_FRESH = FINAL_SNAPSHOT_STATE == "fresh"
if not SNAPSHOT_IS_FRESH:
    if FINAL_SNAPSHOT_STATE == "stale":
        verdict = AUDIT_FINALIZATION["verdict"]
        FINAL_REVIEW_FINDINGS = []
        HANDOFF_METADATA = {**HANDOFF_METADATA, "verdict": verdict}
        RAW_LEDGER = {**RAW_LEDGER, "verdict": verdict}
    elif FINAL_SNAPSHOT_STATE == "authority_degraded":
        verdict = AUDIT_FINALIZATION["verdict"]
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
        review_level_findings=REVIEW_LEVEL_FINDINGS,
        unpostable=UNPOSTABLE_FINDINGS,
        snapshot=GATE_AUTHORITY["snapshot"],
        annotation_generation_id=ANNOTATION_GENERATION_ID,
        mode=MODE,
        snapshot_is_fresh=SNAPSHOT_IS_FRESH,
        handoff_metadata=HANDOFF_METADATA,
        receipt=(json.loads(RECEIPT_DOCUMENT) if MODE == "github" and RECEIPT_DOCUMENT else None),
    )
    if SNAPSHOT_IS_FRESH:
        publication_generation_id = PUBLICATION["artifacts"]["raw_findings"][
            "review_generation_id"
        ]
        if publication_generation_id != REVIEW_GENERATION_ID:
            raise RuntimeError("publication generation changed after external effects")
    PUBLICATION_RESULT = publish_experimental_review_artifacts(
        publication=PUBLICATION,
        output_dir="{review_output_dir}",
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
`{review_output_dir}raw_findings_{pr_number}.json`. Do not begin diff-context,
receipt, or local-findings publication until this rename succeeds. On
`stale_snapshot`, the raw ledger is a bounded diagnostic envelope with empty
survivor and publication sets.

**Write Diff-Scoped Context Handoff (before emitting verdict):**

After writing the summary file and before emitting the verdict token, write the handoff
file for resolve-review's pre-built context. This costs zero additional API calls or file
reads — all data is already in the session's context.

Do not output prose between iterations. For each non-review-level finding in
`INLINE_FINDINGS` + `UNPOSTABLE_FINDINGS` where severity is
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

Write to `{review_output_dir}diff_context_{pr_number}.json`. If it already exists,
replace it through the Step 8 same-directory temporary file and atomic rename:

```json
{
  "pr_number": 1234,
  "schema_version": 2,
  "written_at": "{ISO-8601 timestamp}",
  "context_entries": [
    {
      "path": "src/autoskillit/execution/headless.py",
      "line": 42,
      "anchor_digest": "sha256 of the normalized annotated target line",
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
  "review_level_findings": [],
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

Version 2 requires a non-empty `anchor_digest` on every context entry whose `line`
is an integer. For `line: null`, omit the digest and do not manufacture source bytes.
`review_level_findings` is separate from `context_entries`; it remains an empty
list until a future review source explicitly defines a review-level contract.

**Raw Findings JSON schema (published first):**

The raw findings source ledger preserves
standard findings, experimental candidates, validation, disposition, aggregation,
verdict-use, publication, rejected-candidate, and malformed-envelope records.

Write to `{review_output_dir}raw_findings_{pr_number}.json`:

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

When the final freshness guard yields `stale_snapshot`, emit publication sentinels
without calling the structured publication tool: use `review_operation_key = none`,
`review_head_sha = ${METRICS_HEAD_SHA}`, `review_post_state = STALE_SNAPSHOT`, and
`review_receipt_path = none`.

Output the verdict as the final line:

> **IMPORTANT:** Emit the structured output tokens as **literal plain text with no
> markdown formatting on the token names**. Do not wrap token names in `**bold**`,
> `*italic*`, or any other markdown. Do not wrap the output block in a code fence.
> The adjudicator performs a regex match on the exact token name — decorators and
> code fences cause match failure.

```
review_operation_key = {authoritative operation key, local when mode=local, or none when stale}
review_head_sha = {authoritative requested head, pr_head_sha when mode=local, or METRICS_HEAD_SHA when stale}
review_post_state = {SUCCEEDED|RECONCILED|LOCAL|STALE_SNAPSHOT}
review_receipt_path = {authoritative receipt path, or none when local/stale}
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

- `review_operation_key`, `review_head_sha`, `review_post_state`, and
  `review_receipt_path` — authoritative publication identity in GitHub mode; explicit
  `local`/`LOCAL`/`none` sentinels in local mode
- `verdict=approved` → `%%REVIEW_GATE::CLEAR%%` — No blocking issues; CI can proceed
- `verdict=approved_with_comments` — no gate tag — Warning-only findings; recipe routes to `resolve_review` but does not require a re-review cycle
- `verdict=changes_requested` → `%%REVIEW_GATE::LOOP_REQUIRED%%` — Blocking issues found; recipe routes to `resolve_review`
- `verdict=needs_human` → `%%REVIEW_GATE::CLEAR%%` — Uncertain trade-offs; human review requested via the authenticated GitHub user mention (derived at runtime)
- `verdict=stale_snapshot` — no gate tag or effect-bearing artifact; recipe refreshes
  annotation within its existing bounded recovery path

Summary written to: `{review_output_dir}summary_{pr_number}_{timestamp}.md`

**Mode-conditional path output:**

When `mode=local`, the following token is emitted:
```
local_findings_path = {review_output_dir}local_findings_{pr_number}.json
```

When `mode=github`, no local_findings_path token is emitted (findings are posted directly to GitHub).
