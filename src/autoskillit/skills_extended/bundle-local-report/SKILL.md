---
name: bundle-local-report
categories: [rendering]
---

# bundle-local-report

Convert a research markdown report into a self-contained `report.html` with inlined
mermaid diagrams and inserted plot images from `yaml:figure-spec` blocks.

## Critical Constraints

**NEVER:**
- Raise a fatal error on missing diagrams or missing visualization-plan — log and continue.
- Use the ESM mermaid build — ESM triggers CORS under `file://`; always use the UMD bundle (`mermaid.min.js`).
- Fabricate HTML rendering details or attribute explanations when the bundling process produces unexpected output — describe what was actually produced rather than inventing plausible rendering behavior.
- Exit without emitting `html_path = ` (even empty) as your final output — the recipe `capture:` block expects it.

**ALWAYS:**
- Emit: `html_path = <absolute path to report.html>` (or `html_path = ` if report_path is absent) as your final output.
- Use `{AUTOSKILLIT_TEMP}` as the base for temp files.
- Using ONLY classDef styles from the mermaid skill (no invented colors).

## Arguments

Positional (space-separated, injected by recipe):
1. `research_dir`             — absolute path to the research directory
2. `report_path`              — absolute path to the markdown report (README.md post-finalize)
3. `all_diagram_paths`        — comma-separated list of exp-lens diagram paths (may be empty)
4. `visualization_plan_path`  — absolute path to visualization-plan.md (may be empty string)

Output: `html_path = {absolute_path_to_report.html}` as your final output.

## Steps

### Step 0 — Parse arguments

Capture positional args:
- `$1` → `research_dir`
- `$2` → `report_path`
- `$3` → `all_diagram_paths` (comma-separated, may be empty)
- `$4` → `visualization_plan_path` (may be empty string)

If `report_path` does not exist, emit `html_path = ` (empty) as your final output immediately
(graceful non-fatal exit — the pipeline continues to begin_archival).

### Step 1 — Run the renderer

Invoke the renderer module directly:

```bash
python -m autoskillit.report.renderer "$1" "$2" "$3" "$4"
```

Capture the stdout line `html_path = ...` and emit it as the structured output token
as your final output.

**ALWAYS** emit `html_path = ` (even empty) as your final output — the recipe `capture:`
block expects it.

**NEVER** raise a fatal error on missing diagrams or missing visualization-plan — log and
continue.

**ALWAYS** use the UMD bundle (`mermaid.min.js`), never the ESM build — ESM triggers CORS
under `file://`.
