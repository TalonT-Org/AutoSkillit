---
name: stage-data
categories: [research]
description: >
  Pre-flight resource gate for the research recipe. Reads the experiment plan's
  data_manifest, checks disk space and network connectivity for external/gitignored
  entries, creates data directory structure, and emits a PASS/WARN/FAIL feasibility verdict.
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo '[SKILL: stage-data] Checking resource feasibility...'"
          once: true
---

# Stage Data Skill

Pre-flight resource gate for the research recipe. Reads the experiment plan's
`data_manifest` frontmatter section, checks available disk space and network
connectivity for `external` and `gitignored` data source entries, creates the
required data directory structure in the worktree, and emits a PASS/WARN/FAIL
feasibility verdict. PASS and WARN proceed to implementation; FAIL escalates
immediately with a detailed resource feasibility report rather than wasting
compute on doomed downloads.

## When to Use

- Invoked by the research recipe's `stage_data` step between `create_worktree`
  and `decompose_phases`
- Whenever a pre-flight resource check is needed before data-intensive implementation

## Arguments

```
/autoskillit:stage-data <experiment_plan_path>
```

- `experiment_plan_path` — Absolute path to the experiment plan (positional).
  Default: `$AUTOSKILLIT_TEMP/experiment-plan.md` in the current working directory.

## Critical Constraints

**NEVER:**
- Modify the experiment plan
- Modify any source files
- Write files outside `{{AUTOSKILLIT_TEMP}}/stage-data/`
- Emit a WARN or FAIL verdict without a specific, actionable explanation
- Run subagents in the background (`run_in_background: true` is prohibited)

**ALWAYS:**
- Read the `data_manifest` frontmatter section of the experiment plan
- Check ONLY `external` and `gitignored` source_type entries (`synthetic` and
  `fixture` entries require no disk space or network access)
- Create data directories for every entry whose `location` field is non-null,
  using `mkdir -p`
- Write the resource feasibility report before emitting the verdict token
- Use `model: "sonnet"` for all subagents

## Workflow

### Step 1 — Parse the Experiment Plan

Read the experiment plan at the provided path (or default path). Parse the
`data_manifest` YAML frontmatter section. Identify all entries where
`source_type` is `"external"` or `"gitignored"`.

### Step 2 — Short-Circuit for Synthetic/Fixture-Only Plans

If no `external` or `gitignored` entries exist, skip disk and network checks.
Create any data directories for entries with non-null `location`. Emit
`verdict = PASS` and exit.

### Step 3 — Launch Parallel Resource Probe Subagents

Launch parallel subagents — one per `external`/`gitignored` entry — each
performing:

**a. DISK SPACE AGENT:** Run `df -k .` to get available bytes in the worktree.
Estimate storage need from the entry's `description` field using LLM reasoning
(e.g., "10-50GB h5ad files" → project 50GB worst case). Compute headroom:

If `available_bytes == 0` (filesystem completely full), emit **FAIL** immediately —
do not proceed to the formula below.

```
headroom_pct = (available_bytes - projected_bytes) / available_bytes * 100
```

Disk space verdict thresholds:
- **FAIL**: `projected_bytes > available_bytes` (negative headroom)
- **WARN**: `0 < headroom_pct < 20` (less than 20% remaining)
- **PASS**: `headroom_pct >= 20`

**b. NETWORK PROBE AGENT:** Infer the API base URL from the `acquisition`
field. Known endpoints to probe:
- GEO / NCBI: `https://eutils.ncbi.nlm.nih.gov/entrez/eutils/einfo.fcgi`
  Rate limit: 3 req/s without API key, 10 req/s with NCBI API key. Single probe is well within limits.
- ENCODE: `https://www.encodeproject.org/`
  Rate limit: no published hard limit; courtesy throttle expected at high volume.
- UniProt: `https://rest.uniprot.org/uniprotkb/search?query=reviewed:true&size=1`
  Rate limit: undocumented; `size=1` keeps payload ~1KB.
- Allen Brain Atlas: `https://api.brain-map.org/api/v2/data/Gene/query.json?num_rows=1`
  Rate limit: no published limit; public API. `num_rows=1` keeps response ~200B.
- CellxGene: `https://api.cellxgene.cziscience.com/dp/v1/collections`
  Rate limit: CDN-backed, no published limit. HEAD avoids 53KB collection list.
- Expression Atlas: `https://www.ebi.ac.uk/gxa/json/experiments`
  Rate limit: no published hard limit (EBI courtesy policy). CRITICAL: response is ~2.6MB — always use HEAD (`curl -sI`), never GET.
- Human Protein Atlas: `https://www.proteinatlas.org/api/search_download.php?search=CD8A&columns=g&compress=no&format=json`
  Rate limit: no published limit; public API. `columns=g` is required (API returns 400 without it). Response ~20B.
- STRING: `https://string-db.org/api/json/version`
  Rate limit: ~10 req/s for programmatic access. Version endpoint returns 84B — ideal probe.
- JASPAR: `https://jaspar.elixir.no/api/v1/matrix/?page_size=1`
  Rate limit: no published limit (academic resource). Note: domain migrated from genereg.net to elixir.no. `page_size=1` keeps response ~330B.
- For unrecognized sources: attempt a HEAD request to any URL found in
  the `acquisition` field.

Run: `curl -sI --max-time 10 <endpoint>` and inspect HTTP status:
- **FAIL**: connection refused, timeout, or 5xx response
- **WARN**: 4xx response (auth required but endpoint reachable)
- **PASS**: 2xx or 3xx response

Network connectivity check: the WARN condition indicates the endpoint is
reachable but authentication may be needed. A network reachability issue
produces FAIL.

### Step 4 — Create Data Directory Structure

For every entry whose `location` field is non-null, run:

```bash
mkdir -p <worktree_cwd>/<location>
```

This creates the data dir hierarchy required by the experiment implementation.

### Step 5 — Synthesize Overall Verdict

Aggregate results across all entries:
- **FAIL** if ANY entry produced a FAIL result (disk or network)
- **WARN** if any entry produced a WARN result and none produced FAIL
- **PASS** if all entries produced PASS

### Step 6 — Write Resource Feasibility Report

Write the resource feasibility report to:

```
{{AUTOSKILLIT_TEMP}}/stage-data/resource_feasibility_{YYYY-MM-DD_HHMMSS}.md
```

Report structure:

```markdown
## Resource Feasibility Report
**Date:** {timestamp}
**Verdict:** PASS | WARN | FAIL

### Disk Space Assessment
| Entry | Source Type | Projected Size | Available | Headroom | Status |
|-------|-------------|----------------|-----------|----------|--------|
...

### Network Connectivity Assessment
| Entry | Endpoint Probed | HTTP Status | Latency | Status |
|-------|-----------------|-------------|---------|--------|
...

### Data Directories Created
- {location}: created | skipped (null location)
...

### Recommended Actions (WARN/FAIL only)
- {specific actionable step to resolve each issue}
```

### Step 7 — Emit Structured Output Tokens

Emit structured output tokens as LITERAL PLAIN TEXT with NO markdown
formatting on the token names. Do not wrap token names in `**bold**`,
`*italic*`, or any other markdown. The adjudicator performs a regex match
on the exact token name — decorators cause match failure.

```
verdict = PASS
resource_report = /absolute/path/to/resource_feasibility_{YYYY-MM-DD_HHMMSS}.md
```

## Output

```
verdict = PASS|WARN|FAIL
resource_report = /absolute/path/to/{{AUTOSKILLIT_TEMP}}/stage-data/resource_feasibility_{YYYY-MM-DD_HHMMSS}.md
```
