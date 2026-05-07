# Silent-Type Convention

Shared convention for detecting and handling "silent" experiment types and methodology
traditions — those that lack strong dimensional signals or mandatory figure requirements.

Consumed by Work Items 2.3 (#835) and 4.7 (#846).

## Detection Criteria

### Experiment Types

An experiment type is "silent" when **≥6 of 8 `dimension_weights`** are equal to `S`
(suppressed). Silent experiment types produce valid classifications but lack the
dimensional signal strength to drive opinionated design-review scoring.

### Methodology Traditions

A methodology tradition is "silent" when its `mandatory_figures` list is **empty**.
Silent traditions produce valid Tier-C routing results but do not constrain the
visualization plan to specific figure types.

## Canonical Advisory Schema

Both experiment-type and methodology-tradition subsystems emit advisories using
this schema when a silent type/tradition is detected:

```yaml
verdict: GO
advisory_context:
  subject_kind: experiment_type | methodology_tradition
  subject_name: <snake_case>
  reasoning: <string>
  reference_framework: <string>
  strongly_expected_figures: [<string>, ...]   # optional; only populated by vis-lens side
requires_decision: false
```

### Field Definitions

| Field | Type | Description |
|-------|------|-------------|
| `verdict` | `GO` | Always GO — silent types are valid, not errors |
| `subject_kind` | enum | `experiment_type` or `methodology_tradition` |
| `subject_name` | string | Snake-case name of the detected silent type/tradition |
| `reasoning` | string | Human-readable explanation of why this is silent |
| `reference_framework` | string | Which registry/framework detected the silent signal |
| `strongly_expected_figures` | list | Optional; populated only by vis-lens side for traditions |
| `requires_decision` | `false` | Informational only — no human decision needed |

## Write Targets

| Detection Source | Advisory Written To |
|-----------------|---------------------|
| Experiment-type subsystem | `research/{slug}/audit/design-review-dashboard.md` |
| Methodology-tradition subsystem | `research/{slug}/audit/visualization-plan-trace.md` |

Advisories are **appended** to the corresponding audit-dashboard file. They are NOT
written into the report frontmatter — the frontmatter records classification decisions
only, not advisory metadata.

## Integration Test Expectations

A qualitative-interpretive fixture plan should produce matching advisories from both
subsystems:
- The experiment-type side detects silent dimensions and writes an advisory to
  `design-review-dashboard.md`
- The methodology-tradition side detects empty `mandatory_figures` and writes an
  advisory to `visualization-plan-trace.md`
- Both advisories have `verdict: GO` and `requires_decision: false`
