---
name: analyze-run
write_paths:
- '{{AUTOSKILLIT_TEMP}}/analyze-run/'
uses_capabilities: []
categories:
- diagnostic
description: Interpret one frozen report-index snapshot and publish its observability
  analysis. Use when asked to analyze a retained run or rollout cohort.
hooks:
  PreToolUse:
  - matcher: '*'
    hooks:
    - type: command
      command: 'echo ''[SKILL: analyze-run] Preparing a frozen run analysis...'''
      once: true
---

# Analyze Run

Prepare one dated analysis packet from the report index, interpret only that frozen
packet in this session, and publish its self-contained report. The optional change ID
selects a previously marked rollout cohort; it is never inferred.

## Invocation

`/autoskillit:analyze-run [--change-id=<id>]`

- `--change-id` is optional. Pass only the exact value supplied by the caller. When
  omitted, prepare an explicit no-cohort analysis.

## Arguments

- `instance_dir` — runtime-derived absolute path printed in Step 1; not caller-supplied.
- `working_log` — runtime-derived sibling log path printed in Step 1; not caller-supplied.
- `view_id` — runtime-derived ID of a built view in the frozen packet; not caller-supplied.

## Critical Constraints

**NEVER:**
- Read live sessions, report-index sources, telemetry logs, or render assets after
  `analysis-prepare` has completed. All interpretation uses the frozen packet.
- Directly edit `measurements.json`, the saved render bundle, a previous analysis
  instance, or the completed report. The CLI owns those instance artifacts; this skill
  writes only its bounded sibling working log and the new instance's
  `.candidate-findings.json` file.
- Invent measurements, evidence IDs, cohort markers, model identities, causes, or
  unsupported conclusions. State uncertainty plainly.
- Treat digest, schema, ownership, or evidence-reference validation as certification of
  the prose. `analysis-finish` validates structure and references; this skill is
  responsible for checking that each claim follows from the cited frozen evidence.
- Delegate this interpretation or create files outside
  `{{AUTOSKILLIT_TEMP}}/analyze-run/`.
- Emit `report_path` unless `analysis-finish` returns `completed: true`.

**ALWAYS:**
- Interpret in this session only. This skill does not delegate or call MCP or recipe
  tools.
- Use a unique UTC `YYYY-MM-DD_HHMMSS_<uuid>` instance directory directly under
  `{{AUTOSKILLIT_TEMP}}/analyze-run/`. Keep the per-run working log as a literal sibling
  of the instance directory.
- Capture command stdout and stderr together in that literal working log. Inspect only
  bounded byte slices; parse the final JSON status line separately from refresh output.
- If `interpretation_required` is `false`, reuse the supplied candidate and skip new
  interpretation. Do not rewrite it.
- When interpretation is required, cite evidence IDs from the packet's exact
  `evidence` map. Every cited ID must belong to the finding's `view_id`. Check numeric
  statements against the matching saved row, group, metric, and comparison values.
- Keep candidate JSON to exactly `snapshot_digest`, `interpreter`, and `findings` at the
  top level. Each finding contains exactly `view_id`, `text`, and `evidence_ids`; do not
  add numeric fields.
- For a truly empty frozen corpus, use a built view and the exact finding text
  `No observations in the frozen corpus.` with an empty `evidence_ids` list.
- After successful finish, emit exactly `report_path = <absolute path>` on the final
  line. This output token is captured by the caller.

## Workflow

### 1. Resolve literal paths

Create the skill scratch root:

```bash
set -o pipefail
mkdir -p '{{AUTOSKILLIT_TEMP}}/analyze-run/' 2>&1 | head -c 4000
```

Run this read-only path generator, then copy its two absolute output lines literally
into every later command: first the instance directory, then the sibling working log.
The instance directory is a dated child of the skill scratch root; the working log
stays outside that instance.

```bash
python -c 'from datetime import UTC, datetime; from pathlib import Path; from uuid import uuid4; root=Path("{{AUTOSKILLIT_TEMP}}/analyze-run").resolve(); run=datetime.now(UTC).strftime("%Y-%m-%d_%H%M%S")+"_"+uuid4().hex; print(root/run); print(root/(run+".log"))' 2>&1 | head -c 500
```

Use those printed paths as literal targets. Do not use shell variables, command
substitution, or a second generated ID for any later write.

### 2. Freeze the report-index snapshot

Run exactly one prepare command. If the caller supplied `--change-id`, pass that exact
value as one shell-quoted argument; otherwise omit the option. Redirect both output
streams to the literal sibling working log, for example:

```bash
autoskillit sessions analysis-prepare '{instance_dir}' > '{working_log}' 2>&1
```

Then inspect a bounded tail and the final status line:

```bash
tail -c 12000 '{working_log}' 2>&1 | head -c 12000
tail -n 1 '{working_log}' 2>&1 | head -c 4000
```

The final line is JSON status; preceding lines are operational refresh information.
Stop on a nonzero command exit or malformed status. Record `candidate_path`,
`snapshot_digest`, and `interpretation_required` from that status.

### 3. Interpret only when required

When `interpretation_required` is `false`, keep the candidate already supplied in the
instance and go directly to Step 4.

When it is `true`, inspect only `measurements.json` in this instance. First extract
bounded packet metadata, the coverage table, cohort declaration, and built view IDs:

```bash
jq -c '{input_fingerprint,coverage,cohort:(.cohort|{change_id,reason,effective_at,scope,expected_effect,before_count,after_count,excluded_count,marked_pre_cutover_observation,preregistration}),views:[.payload.views[]|{id,status,question}]}' '{instance_dir}/measurements.json' 2>&1 | head -c 16000
```

For each built view being interpreted, extract only that view's saved evidence, with a
byte cap after extraction and stderr merge:

```bash
jq -c --arg view_id '{view_id}' '[.evidence|to_entries[]|select(.value.view_id==$view_id)|{id:.key,source:.value.source,measurement:.value.measurement}]' '{instance_dir}/measurements.json' 2>&1 | head -c 24000
```

If a query reaches its byte cap, refine it to a smaller view or exact evidence ID. Do
not rely on truncated rows. The packet's `coverage` states retained row counts and
limitations; its `cohort` states the chosen marker, scope, expected effect, before/after
counts, and preregistration limitation. The evidence map contains exact rows and
measurements, including availability, units, sample counts, state counts, and
comparison guardrails.

Write findings only for claims supported by those exact records. Keep each finding's
`view_id` and evidence references aligned. Do not infer a causal or sustained-change
claim from descriptive rollout deltas. Omit views without supported findings. If a
non-empty packet contains no evidence that can support a valid finding, stop without
writing a candidate or claiming completion.

Write the candidate JSON to the exact absolute `candidate_path` from prepare, and nowhere
else in the instance. Replace every angle-bracket example with actual status/evidence
values. Copy the candidate path into the Write tool's literal `file_path`; do not
recompute it or use a shell variable:

```json
{
  "snapshot_digest": "<snapshot_digest from prepare status>",
  "interpreter": {
    "role": "analyze-run",
    "model": null,
    "prompt": null
  },
  "findings": [
    {
      "view_id": "<built view ID>",
      "text": "<plain-language finding supported by the listed evidence>",
      "evidence_ids": ["<same-view evidence ID>"]
    }
  ]
}
```

For an empty corpus, replace the example finding with the required no-observations text
and `"evidence_ids": []`. Use non-null model or prompt identities only when the actual
interpreter identifies them.

### 4. Finish and emit the captured path

Run finish against the same literal instance directory, capturing both streams to the
same literal sibling log:

```bash
autoskillit sessions analysis-finish '{instance_dir}' > '{working_log}' 2>&1
tail -c 12000 '{working_log}' 2>&1 | head -c 12000
tail -n 1 '{working_log}' 2>&1 | head -c 4000
```

Parse the final JSON status. Only when it reports `completed: true`, emit the absolute
`report_path` from that status as a plain output line:

```text
report_path = /absolute/path/to/report.html
```

If finish fails, preserve the candidate and frozen packet, report the bounded error, and
do not emit a `report_path` token.

In a headless session, also reproduce the supplied `%%ORDER_UP::{nonce}%%` completion
token exactly as instructed by that session. Never invent a completion nonce.
