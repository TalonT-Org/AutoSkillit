---
name: file-audit-issues
categories: [audit-pipeline]
uses_capabilities: [github_api_write]
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

Do not output prose between payload-write and GitHub calls or between chunks; immediately
proceed to the next required call.

Resolve repo identity: use `gh repo view --json owner,name` to get the canonical owner and repo name, then fetch `gh api repos/{owner}/{repo} --jq '.node_id'`.
If this call fails or returns an empty node_id (missing `GH_TOKEN`, wrong remote, insufficient
permissions), abort immediately with a clear error message: `"Error: could not resolve repo node_id
— check GH_TOKEN and remote URL. Aborting."` Do not proceed with GraphQL mutations using an empty node_id.
Before any label mutation, read the complete repository label inventory with
`gh label list --limit 1000 --json name,id`. Build the required set from `audit`,
`recipe:implementation`, and every source-specific label derived from the ticket filenames.
Determine the genuinely missing definitions. If any are missing, use a file-write tool in a
separate completed tool call to create one bounded GraphQL JSON payload under the absolute run
directory. The payload contains one aliased `createLabel` operation per missing label and a
`variables` object with the repository ID, name, color, and description. Invoke that payload
once using the exact absolute path written in the prior call; this concrete command illustrates
the required shape:

```bash
gh api graphql --input "/absolute/audit-run/create_missing_labels.json"
```

Do not create or rewrite definitions already present. After the mutation, refresh the inventory
and build an explicit label-name-to-node-ID map. Abort before issue creation if any required
label still lacks a node ID. When the missing-definition request ran, sleep one second before
the next mutating call.

Build `createIssue` mutations with aliases (`issue0`, `issue1`, ...), chunked at 20 issues per
request. Put titles and bodies in the JSON payload's `variables` object rather than interpolating
them into the GraphQL document. Each alias must return the issue node ID as well as its number
and URL:

```json
{
  "query": "mutation CreateIssues($repositoryId: ID!, $title0: String!, $body0: String!) { issue0: createIssue(input: {repositoryId: $repositoryId, title: $title0, body: $body0}) { issue { id number url } } }",
  "variables": {
    "repositoryId": "R_1",
    "title0": "Validated audit finding",
    "body0": "Full validated ticket body"
  }
}
```

For every chunk, use a file-write tool in a separate completed tool call to write a bounded JSON
object under the absolute run directory, then invoke its literal absolute path in a later call:

```bash
gh api graphql --input "/absolute/audit-run/create_issues_chunk_0.json"
```

Parse each response by alias. Store both an alias-to-issue-ID map and a ticket-body-file-to-issue-ID
map alongside the issue number and URL. Abort the chunk on a missing alias, missing `id`, `number`,
or `url`, or any GraphQL error. Sleep one second between consecutive mutating chunks.

## Step 6 — Apply Source-Specific Labels

Do not output prose between payload-write and GitHub calls or between chunks; immediately
proceed to the next required call.

Parse the source from each ticket body filename (`ticket_body_{source}_{N}_{ts}.md`). For each unique
source, look up its source-label node ID in the refreshed inventory map from Step 5 (for example,
`audit:tests`, `audit:arch`, or `audit:cohesion`). Never construct a label mutation from a label
name alone. For each created ticket, combine the node IDs for `audit`,
`recipe:implementation`, and its actual source label with the issue node ID stored by Step 5.

Build aliased `addLabelsToLabelable` operations in chunks of 20. Put every issue ID and label-ID
list in the payload's `variables` object:

```json
{
  "query": "mutation ApplyLabels($issue0: ID!, $labels0: [ID!]!) { l0: addLabelsToLabelable(input: {labelableId: $issue0, labelIds: $labels0}) { labelable { ... on Issue { id number } } } }",
  "variables": {
    "issue0": "I_1",
    "labels0": ["LA_audit", "LA_recipe", "LA_source"]
  }
}
```

For each chunk, use a file-write tool in a separate completed tool call to write the bounded JSON
payload under the absolute run directory. Invoke it in a later call through the exact literal
absolute path:

```bash
sleep 1
gh api graphql --input "/absolute/audit-run/apply_labels_chunk_0.json"
```

Treat a missing issue/label ID, missing alias, or GraphQL error as a chunk failure. Preserve the
existing one-second pacing between every consecutive mutating GitHub API call.

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
