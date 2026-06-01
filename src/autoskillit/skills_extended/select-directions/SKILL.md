---
name: select-directions
categories: [research]
backend_requirements: [claude-code]
description: >
  Direction-selection gate: parse scope directions manifest, present for
  selection (human or agent), enforce breadth, output filtered directions.
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo '[SKILL: select-directions] Starting direction selection gate...'"
          once: true
---

# Select Directions Skill

Direction-selection gate between scope and plan_experiment. Parses the structured
directions manifest emitted by scope, presents directions for human selection (interactive)
or auto-selects P0/must_cover directions (headless), enforces a configurable minimum
breadth threshold, and outputs a filtered selected_directions JSON file.

## When to Use

- Immediately after `scope` and before `plan_experiment` in research.yaml and research-design.yaml
- Invoked automatically by the recipe engine via `run_skill`

## Critical Constraints

**NEVER:**
- Fabricate, invent, or embellish information not supported by the available evidence or code.
- Bypass the direction selection step — every direction must be explicitly selected or dropped with justification
- Modify the original `scope_directions` JSON file
- Pass through silently if `scope_directions_path` is missing or unparseable — STOP with an error

**ALWAYS:**
- Preserve all must_cover: true directions regardless of selection mode
- Output a valid selected_directions JSON file to `{{AUTOSKILLIT_TEMP}}/select-directions/`
- Emit the `selected_directions = {absolute_path}` token at the end of output

## Arguments

`{scope_directions_path} [scope_report_path] [min_breadth]`

- `scope_directions_path` (position 1, required): absolute path to the scope_directions JSON manifest
- `scope_report_path` (position 2, optional): absolute path to scope_report.md for display context
- `min_breadth` (position 3, optional): minimum directions to select when ≥3 available (default: "2")

## Workflow

### Step 1: Parse Inputs

Read `scope_directions_path` (position 1) as JSON. Extract:
- `research_question`
- `generated_at`
- `directions` array

Parse `min_breadth` from position 3 arguments (default to "2" if not provided).

### Step 2: Detect Mode

Run:
```bash
echo "${AUTOSKILLIT_HEADLESS:-0}"
```
- `"1"` → headless mode (auto-select)
- `"0"` or empty → interactive mode (AskUserQuestion)

### Step 3: Format Directions Table

Display all directions as a numbered table:

```
| # | ID | Title | Priority | Must Cover | Source Type | Feasibility |
|---|----|----|---------|------------|-------------|-------------|
| 1 | D1 | ... | P0 | yes | computational | ... |
```

### Step 4a: Interactive Mode — AskUserQuestion

Use AskUserQuestion to ask:
> "Select directions to pursue (comma-separated IDs, e.g. D1,D3). Minimum {min_breadth} required when {direction_count} directions are available."

Validate:
- All specified IDs exist in the manifest
- Selection count ≥ min_breadth when direction_count ≥ 3
- If validation fails, re-prompt once with error message

### Step 4b: Headless Mode — Auto-select

Auto-select using this algorithm:
1. Select all `must_cover: true` directions (P0)
2. If selected count < min_breadth AND direction_count ≥ 3, add P1 directions in order until threshold met (set `must_cover: true` on each P1 direction added)
3. Emit justification: "Auto-selected {N} directions: all P0 ({list}) + {M} P1 ({list}) to meet min_breadth={min_breadth}"

### Step 5: Write Output JSON

Ensure output directory exists:
```bash
mkdir -p "{{AUTOSKILLIT_TEMP}}/select-directions/"
```

Write `selected_directions_{topic}_{timestamp}.json` with this structure:

```json
{
  "research_question": "<from original manifest>",
  "generated_at": "<from original manifest>",
  "selected_at": "<ISO-8601 timestamp>",
  "selection_mode": "interactive|headless",
  "selected_direction_count": <count of selected>,
  "must_cover_count": <count of selected with must_cover=true>,
  "original_direction_count": <from original manifest>,
  "min_breadth_applied": <threshold used>,
  "directions": [
    {
      "direction_id": "D1",
      "title": "...",
      "priority": "P0",
      "must_cover": true,
      "source_type": "...",
      "feasibility_notes": "...",
      "selection_justification": "..."
    },
    {
      "direction_id": "D2",
      "title": "...",
      "priority": "P1",
      "must_cover": true,
      "source_type": "...",
      "feasibility_notes": "...",
      "selection_justification": "selected to meet min_breadth threshold"
    }
  ],
  "dropped_directions": [
    {
      "direction_id": "D3",
      "title": "...",
      "drop_justification": "..."
    }
  ]
}
```

Note: All selected directions have `must_cover: true` regardless of original priority —
this enforces that `plan-experiment`'s breadth enforcement covers every selected direction.

### Step 6: Emit Output Token

Print as the final lines of output:
```
selected_directions = {absolute_path_to_json}
```

## Output Location

```
{{AUTOSKILLIT_TEMP}}/select-directions/selected_directions_{topic}_{timestamp}.json
({{AUTOSKILLIT_TEMP}} resolves to an absolute path, independent of the current working directory)
```

## Output Fields (for recipe capture)

- `selected_directions` — absolute path to the selected_directions JSON file
