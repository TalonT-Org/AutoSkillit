# Phoropter Execution Contract

This document is the authoritative reference for phoropter execution contracts. It defines the universal invariants that every phoropter family must satisfy at recipe validation time and at runtime. The accompanying [family-guide.md](family-guide.md) walks through the process of authoring a new family that conforms to this contract.

## §1. The Phoropter Pattern

The phoropter is a recipe-level primitive that organises documentation lenses into a three-phase pipeline: **dial → apply → synthesize**. Each phoropter family groups a set of analytical lenses (e.g., arch-lens, exp-lens, vis-lens) and routes their execution through this pipeline.

Two semantic rules enforce the pattern at recipe validation time (both are `ERROR` severity in `src/autoskillit/recipe/rules/rules_phoropter_adjacency.py`):

- **`phoropter-phase-order`** — steps within a `phoropter_family` must follow the `dial → apply → synthesize` progression. The canonical phase tuple is `_PHOROPTER_PHASES = ("dial", "apply", "synthesize")`. Out-of-order steps produce an `ERROR` finding.
- **`phoropter-step-interleaving`** — non-phoropter steps must not interrupt an in-progress family sequence. If a step with no `phoropter_family` annotation appears between canonical phase steps of an active family, an `ERROR` finding is emitted.

Steps with `action: route` are transparent to both rules — routing does not advance or interrupt a family sequence.

## §2. Universal Contracts

Every phoropter family must satisfy exactly five contracts. These are enforced by the two semantic rules above plus the step-level configuration knobs in §4.

| Contract | Description |
|----------|-------------|
| (i) Dial precedes Apply | Within each family, the `dial` phase must appear before `apply` in recipe declaration order. Enforced by `phoropter-phase-order`. |
| (ii) Apply precedes Synthesize | Within each family, `apply` must appear before `synthesize`. Enforced by `phoropter-phase-order`. |
| (iii) Synthesize is sole verdict-emitting step | Only the `synthesize` step produces the family's final output (verdict, plan, or assessment). Dial selects; Apply evaluates; Synthesize aggregates. |
| (iv) No non-phoropter interruption | Steps without `phoropter_family` must not appear between canonical phase steps of an in-progress family sequence. Enforced by `phoropter-step-interleaving`. Non-canonical steps within the family (e.g., `select_review_dimensions`, `silent_type_gate`) are transparent. |
| (v) Scoped temp directory | Each step writes only to its scoped `AUTOSKILLIT_TEMP` subdirectory. Cross-step data flows through `capture`/`context` tokens, not shared filesystem paths. |

## §3. Phase Responsibilities

The three phases have distinct, non-overlapping responsibilities:

### Dial — Selection and Classification

Determines which lenses to apply, classifies the experiment type, and emits tokens consumed by downstream phases. The dial step is the family's gatekeeper.

- **Examples:**
  - `classify-experiment-type` (review-design family)
  - `select-vis-lenses` (vis-lens family)
  - `prepare-pr` (arch-lens family)
  - `prepare-research-pr` (exp-lens family)
- **Pre-filtering:** Dial skills may compute conditional values (e.g., `is_silent_type`) that downstream steps use for `skip_when_true`/`skip_when_false` gating.

### Apply — Evaluation and Analysis

Each selected lens runs against the target, producing per-lens output files (diagrams, assessments, figure specs).

- This phase may be skipped conditionally via the `optional_phases` knob (§4).
- The apply step may use `capture_list` for multi-lens fan-out (e.g., `vis_apply` captures `vis_lens_output_paths` into a list).
- Retry semantics (`retries: N`, `on_exhausted`) are common on apply steps — lens evaluation can be expensive.

### Synthesize — Aggregation and Verdict

Reads all per-lens outputs from the capture directory and produces a unified result. The synthesis strategy (§6) determines how conflicts between lens outputs are resolved.

- Synthesize is the only phase that emits the family's final output tokens (e.g., `verdict`, `visualization_plan_path`, `architecture_impact`).
- The synthesis skill is invoked with the capture directory containing all per-lens output files.

## §4. Configuration Knobs

Four knobs control phoropter family behavior. The first three are configured per-family in `src/autoskillit/assets/phoropter-registry.yaml`; the fourth is a step-level field.

| Knob | Values | Description |
|------|--------|-------------|
| `apply_mode` | `eager` (default), `lazy` | Controls whether the apply phase runs all selected lenses (`eager`) or stops at the first decisive result (`lazy`). Currently all families use `eager`. |
| `synthesis.strategy` | `null`, `priority_hierarchy`, `electre_iii`, `dex` | Selects the synthesis strategy for the synthesize phase. See §6 for the full catalog. Configured per-family in `phoropter-registry.yaml`. |
| `optional_phases` | List of phase names | Phases that may be skipped via `PhoropterPhaseSkip` configuration. Currently only `apply` is skippable. Configured per-family in `phoropter-registry.yaml` under `phase_skip`. |
| `PhoropterPhaseSkip` | `skip_when_true` / `skip_when_false` | Conditional phase skipping. The `skip_field` names a context variable; `skip_semantics` determines whether the phase is skipped when the field is truthy or falsy. |

**PhoropterPhaseSkip in detail:**

- `skip_when_true` — The phase is skipped when the named context variable is truthy. Canonical example: vis-lens skips the `apply` phase when `context.is_silent_type` is true (configured in `phoropter-registry.yaml` as `phase_skip.skip_field: context.is_silent_type`, `phase_skip.skip_semantics: skip_when_true`). In recipe YAML, this maps to `skip_when_true: context.is_silent_type` on the apply step.
- `skip_when_false` — The phase runs only when the named context variable is truthy. E.g., `skip_when_false: inputs.review_design` on the `dial` step skips the entire review-design family when the `review_design` input is not provided.

**RecipeStep fields (from `src/autoskillit/recipe/schema.py`):**

```python
phoropter_family: str | None = None   # Associates the step with a phoropter family
skip_when_true: str | None = None     # Context variable; step skipped when truthy
skip_when_false: str | None = None    # Context variable; step skipped when falsy
```

## §5. Step Naming Conventions

The phoropter framework supports two step-naming cases, driven by the `step_naming.prefix` field in `phoropter-registry.yaml`.

| Aspect | Sole-Family (Case A) | Coexisting Families (Case B) |
|--------|---------------------|------------------------------|
| Step keys | `dial`, `apply`, `synthesize` (bare canonical) | Primary family: `dial`, `apply`, `synthesize`; Additional families: `{prefix}_dial`, `{prefix}_apply`, `{prefix}_synthesize` |
| `phoropter_family` annotation | Required on each step — associates the step with the family | Required on primary family steps; **omitted** on prefixed family steps |
| Phase resolution | `_canonical_phase_for_step()` matches step name directly against `_PHOROPTER_PHASES` | `_canonical_phase_for_step()` strips the family prefix (from `phoropter-registry.yaml` `step_naming.prefix`) and matches the suffix |
| When to use | Recipe contains a single phoropter family | Recipe contains two or more families that must coexist |

**Concrete example:**

In `research-design.yaml` and `research.yaml`, the `review-design` family uses canonical step keys (`dial`, `apply`, `synthesize`) with `phoropter_family: review-design` on each. The `vis-lens` family uses prefixed step keys (`vis_dial`, `vis_apply`, `vis_synthesize`) without `phoropter_family` annotation. The `vis` prefix is configured in `phoropter-registry.yaml` under `families.vis-lens.step_naming.prefix: vis`.

**Why `phoropter_family` is omitted on prefixed steps:**

When `phoropter_family` is set, the `phoropter-step-interleaving` rule treats any non-family step between canonical phases as an interleaving violation. In multi-family recipes, routing steps and the other family's steps would trigger false errors. Omitting `phoropter_family` on prefixed steps makes them invisible to the interleaving rule, while the phase-order rule still validates them via prefix-based phase resolution.

## §6. SynthesisStrategy Catalog

The `SynthesisStrategy` enum in `src/autoskillit/core/types/_type_enums.py` defines the recognized synthesis algorithms:

| Strategy | Enum Value | Families | Status | Description |
|----------|-----------|----------|--------|-------------|
| `null` | `SynthesisStrategy.NULL` | arch-lens | Implemented | Identity pass-through. Concatenates lens output `.md` files in lexicographic order without conflict resolution. Implemented in `phoropter-null-synthesis` skill. |
| `priority_hierarchy` | `SynthesisStrategy.PRIORITY_HIERARCHY` | exp-lens, vis-lens | Implemented | Configurable priority hierarchy for inter-lens conflict resolution. Default hierarchy: `accessibility > anti-pattern > methodology-norms > chart-select`. Implemented in `phoropter-priority-synthesis` (configurable via `--hierarchy`) and `synthesize-vis-plan` (hardcoded hierarchy). |
| `electre_iii` | `SynthesisStrategy.ELECTRE_III` | refactor-lens (designed) | Planned | Outranking-based multi-criteria decision analysis. Targeted at the future refactor-lens family where lens outputs have continuous-valued scores requiring threshold-based concordance/discordance analysis. |
| `dex` | `SynthesisStrategy.DEX` | (candidate) | Research candidate | Categorical multi-attribute decision modeling via symbolic attribute hierarchies. Based on [DEXi (2025)](https://kt.ijs.si/MarkoBohanec/dexi.html). Produces categorical outputs (e.g., "acceptable"/"unacceptable") rather than ranked lists. Feasibility evaluation required before implementing for exp-lens or refactor-lens synthesis. |

Note: The `SynthesisStrategy` enum also includes a `CUSTOM` value for future extension.

## §7. IL-0 Type Cross-Reference

Six phoropter types are defined in the IL-0 core types layer (`src/autoskillit/core/types/_type_phoropter.py` and `_type_enums.py`):

| Type | Module | Status | Fields | Purpose |
|------|--------|--------|--------|---------|
| `PhoropterPrescription` | `_type_phoropter.py` | Implemented | `selected_lenses: str`, `lens_context_paths: str`, `failure_mode: str = "continue"` | Dial phase output — records which lenses were selected and their context paths. |
| `ReadingToken` | `_type_phoropter.py` | Implemented | `output_prefix: str`, `path_value: str` | Structured capture of a single lens reading (path to output file with its prefix). `READING_TOKEN_PATTERN` regex: `r"^(?P<prefix>\w+) = (?P<path>/.+)$"`. |
| `PhoropterPhaseSkip` | `_type_phoropter.py` | Implemented | `skip_field: str`, `skip_semantics: Literal["skip_when_true", "skip_when_false"]`, `applies_to: str = ""` | Configuration for conditional phase skipping (see §4). |
| `CrossDomainPrescription` | `_type_phoropter.py` | Implemented | `family_names: tuple[str, ...]`, `merged_lenses: str = ""`, `merge_strategy: str = "union"` | Multi-family lens selection when families share a recipe context. |
| `CrossDomainAssessment` | `_type_phoropter.py` | Implemented | `family_names: tuple[str, ...]`, `synthesis_strategy: SynthesisStrategy = SynthesisStrategy.NULL`, `combined_output: str = ""` | Multi-family synthesis assessment combining outputs across family boundaries. |
| `SynthesisStrategy` | `_type_enums.py` | Implemented | `NULL`, `PRIORITY_HIERARCHY`, `ELECTRE_III`, `DEX`, `CUSTOM` | Enum of recognized synthesis algorithms (see §6 catalog). |

All types are frozen dataclasses with `slots=True` (except `SynthesisStrategy` which is a `StrEnum`). All are exported from `src/autoskillit/core/types/` via the `__init__.py` re-export hub.
