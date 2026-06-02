---
name: file-audit-issues
categories: [audit-pipeline]
description: >-
  Batch-create GitHub issues from validated audit ticket body files. Discovers
  ticket bodies from the audit run directory, deduplicates against existing open
  issues, creates in batches, applies labels and writes a filed-issues manifest.
  Pipeline-only skill dispatched by the full-audit recipe.
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo '[SKILL: file-audit-issues] Filing audit issues from validated reports...'"
          once: true
---

## Step 1 — Resolve Run Directory

Check `AUTOSKILLIT_AUDIT_RUN_DIR` env var first. If unset, check positional argument
(`{run_dir_or_paths}`). If neither, discover the most recent `validate-audit-*` directory
under `{{AUTOSKILLIT_TEMP}}/`. Abort with an error when no run directory can be resolved.

## Step 2 — Discover Ticket Body Files

Glob `ticket_body_*.md` in the resolved run directory. Parse each file: extract H1 title
via first `# ` line, read full body text. When the discovery produces zero ticket body
files, emit `issue_urls = ` and `issue_count = 0` and exit with success (exit code 0).

## Step 3 — Dedup Against Existing Issues

For each ticket body file, extract 2–3 key terms from the title. Run:
```
gh issue list --search "{key terms}" --json number,title,state --limit 5 --state open
```
in the workspace. If any existing open issue title has high similarity, mark that ticket body
as a duplicate and skip it. Log skipped duplicates to terminal.

**Similarity definition:** Tokenize both titles to lowercase words; discard stopwords
(`a`, `an`, `the`, `in`, `of`, `to`, `with`, `for`, `and`, `or`, `is`, `are`, `that`,
`this`, `it`, `be`, `at`, `by`, `from`). Count the number of shared tokens between the
two sets. Three or more shared tokens ⇒ duplicate.

## Step 4 — Validate Ticket Body Size

For each non-duplicate ticket body file, check its size:
- If any ticket body exceeds 10,240 characters (10 KB), log a warning:
  `"Warning: ticket body '{filename}' is {size} chars — exceeds 10KB ceiling. Consider re-running validation with finer ticket grouping."`
- If any ticket body exceeds 60,000 characters, abort with error:
  `"Error: ticket body '{filename}' is {size} chars — exceeds 60KB hard limit. Aborting to prevent oversized GitHub issues."`
- Continue with non-aborted ticket bodies.

## Step 5 — Batch-Create Issues via GraphQL

Resolve repo identity: use `gh repo view --json owner,name` to get the canonical owner and repo name, then fetch `gh api repos/{owner}/{repo} --jq '.node_id'`.
If this call fails or returns an empty node_id (missing `GH_TOKEN`, wrong remote, insufficient
permissions), abort immediately with a clear error message: `"Error: could not resolve repo node_id
— check GH_TOKEN and remote URL. Aborting."` Do not proceed with GraphQL mutations using an empty node_id.
Resolve label IDs for `audit` and `recipe:implementation` labels (ensure they exist via `gh label create --force`).
Build batched GraphQL `createIssue` mutations with aliases (`issue0`, `issue1`, ...), chunked at 20 per request.

```graphql
mutation {
  issue0: createIssue(input: {repositoryId: "<REPO_ID>", title: "<TITLE>", body: "<BODY>"}) {
    issue { number url }
  }
  issue1: createIssue(input: {repositoryId: "<REPO_ID>", title: "<TITLE>", body: "<BODY>"}) {
    issue { number url }
  }
}
```

```bash
echo "$MUTATION_JSON" | gh api graphql --input -
```

Sleep 1 second between chunks (per GitHub API discipline).
Collect created issue URLs and numbers.

## Step 6 — Apply Source-Specific Labels

Parse the source from each ticket body filename (`ticket_body_{source}_{N}_{ts}.md`). For each unique
source, ensure a label exists (e.g., `audit:tests`, `audit:arch`, `audit:cohesion`, etc.).
Batch-apply source labels via GraphQL `addLabelsToLabelable` mutation with aliases.

```graphql
mutation {
  l0: addLabelsToLabelable(input: {labelableId: "<ISSUE_ID>", labelIds: ["<LABEL_ID>"]}) {
    labelable { ... on Issue { number } }
  }
}
```

```bash
echo "$LABEL_MUTATION" | gh api graphql --input -
```

## Step 7 — Write Filed Issues Manifest

Write `filed_issues_manifest_{timestamp}.json` to the run directory with:
- `created_at` (ISO timestamp)
- `run_dir` (absolute path)
- `issues` array: `[{number, url, title, source, labels, ticket_body_file}]`
- `skipped_duplicates` array: `[{title, matched_issue_number, ticket_body_file}]`
- `total_created` and `total_skipped` counts

## Step 8 — Emit Structured Output

```
issue_urls = {comma-separated URLs}
issue_count = {N}
```

When no ticket body files are found, emit empty values and exit immediately:

```
issue_urls = 
issue_count = 0
```

## Critical Constraints

**NEVER:**
- Fabricate, invent, or embellish information not supported by the available evidence or code.

- Use `gh issue comment` — all issue content goes in the body via `--body-file`
- Create issues individually via REST — always use batched `createIssue` mutations
- Skip the 1-second sleep between consecutive mutating GitHub API calls
- Use `--body` inline for large content — always write to temp file and use `--body-file`
- Prompt interactively when `AUTOSKILLIT_HEADLESS` is `1` — execute all steps directly

**ALWAYS:**
- Emit `issue_count = {N}` and `issue_urls = {urls}` as the final structured output (emit `issue_urls = ` with empty value when N=0)
- Deduplicate against existing open issues before creating any new ones
- Write `filed_issues_manifest_{timestamp}.json` to the audit run directory
- Sleep 1 second between each chunk of batch GraphQL mutations
- Use absolute paths for all temp files written during this skill
