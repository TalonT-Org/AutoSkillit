---
name: judge-eval
categories: [eval]
description: >
  Evaluate skill variant outputs against detection criteria for a single canary
  test case. Reads eval_context.json, assesses each candidate artifact against
  each criterion with evidence-based grading, produces cross-variant comparison
  and ranking, and writes verdict.json.
---

When creating this skill, the judge must:
1. Read the `eval_context.json` file at the path passed as argument
2. Read the reference artifact at `reference.path` (interpret based on `reference.artifact_type`)
3. Spawn parallel criterion subagents — one per `detection_criteria` entry:
   - Each subagent receives: the criterion text, the reference artifact content, all candidate artifact paths, and `codebase_root`
   - Each subagent reads each candidate artifact and relevant codebase files
   - Each subagent produces: for each candidate, a `{criterion, result: "PASS"|"FAIL", evidence, quote}` tuple
   - `quote` is required (non-null) on PASS; null on FAIL where no supporting evidence exists
   - Failed variants (path is null): `result: "FAIL"`, `evidence: "variant run failed — no artifact produced"`, `quote: null`
4. Spawn cross-validator subagent:
   - Receives all criterion subagent results
   - Checks for contradictions (e.g., one criterion finds plan traces consumer, another says it doesn't reference consumer's module)
   - Flags any scoring inconsistencies
5. Main agent synthesizes:
   - Tabulate verdict matrix (variant x criterion)
   - `overall`: "PASS" if ALL criteria pass (strict AND); "FAIL" if any criterion fails
   - `ranking`: ordered best-to-worst by criteria passed count, then evidence quality
   - Write `cross_variant_notes`: comparative analysis of how variants differ
6. Write `verdict.json` to `{eval_run_dir}/{canary_id}/verdict.json` (path from eval_context)
7. Emit structured output: `verdict_path=<path>` and `overall_pass_rate=N/M`

## Verdict JSON schema

```json
{
  "eval_id": "C1",
  "verdicts": {
    "baseline": {
      "overall": "FAIL",
      "criteria": [
        {
          "criterion": "Identifies _extract_captures as downstream consumer",
          "result": "FAIL",
          "evidence": "No reference to the extractor found in the plan",
          "quote": null
        }
      ]
    }
  },
  "ranking": ["consumer-contract", "baseline"],
  "cross_variant_notes": "..."
}
```

## Criteria Type Handling

Each criterion may have a `type` field:
- `precision` — Verify the agent does NOT report a false positive. Empty output satisfies this.
- `recall` — Verify the agent DOES find a specific issue. Empty output ALWAYS fails this — never apply vacuous satisfaction.
- `recognition` — Verify the agent acknowledges or recognizes a pattern. Empty output fails this.

If a criterion has no `type` field, treat it as precision (backward compatible default).