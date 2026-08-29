# Phoropter Family Authoring Guide

Step-by-step guide for authoring a new phoropter lens family. This document walks through the six touchpoints required to add a new family to the phoropter framework. For the formal contracts each step must satisfy, see [execution-contract.md](execution-contract.md).

> **If you have not yet decided §4 (Step Naming Convention), do so before
> touching the registry** — the registry's only remaining knob per family is
> `step_naming.prefix`, and that decision is owned by §4.

> **Post-#4894 source-of-truth:** The phoropter-registry at
> `src/autoskillit/assets/phoropter-registry.yaml` contains only
> `step_naming.prefix` per family (one knob per family — the sole production
> consumer is `_load_family_prefixes()` in
> `src/autoskillit/recipe/rules/rules_phoropter_adjacency.py`). Every other
> family knob — synthesis strategy, dial skill, lens metadata, arg
> interface, activate_deps, output_prefix, composite_slugs, lens_count —
> is derived from each lens's `SKILL.md` (frontmatter + body), the recipe
> YAML, or asserted by `tests/skills/test_phoropter_structural.py`. The
> companion contract
> `tests/contracts/test_phoropter_registry_leaf_has_consumer.py` enforces
> that no leaf re-accumulates in the registry without a real consumer or an
> `inert-tracked:#NNNN` annotation.

## §1. Choose a Synthesis Strategy

Before creating any files, decide which synthesis strategy your family will use. The four recognized strategies (see [execution-contract.md §6](execution-contract.md#6-synthesisstrategy-catalog)):

| Strategy | When to Use | Implementation |
|----------|-------------|----------------|
| `null` | Lens outputs do not conflict and are simply concatenated in lexicographic filename order. | Implemented in `phoropter-null-synthesis` skill. Used by arch-lens. |
| `priority_hierarchy` | Inter-lens conflicts are resolved by a configurable priority ordering. | Implemented in `phoropter-priority-synthesis` (configurable via `--hierarchy`) and `synthesize-vis-plan` (hardcoded). Default: `accessibility > anti-pattern > methodology-norms > chart-select`. Used by exp-lens and vis-lens. |
| `electre_iii` | Lens outputs have continuous-valued scores requiring threshold-based concordance/discordance analysis. | Planned; targeted at the future refactor-lens family for outranking-based multi-criteria decision analysis. |
| `dex` | Categorical outputs ("acceptable"/"unacceptable") are needed instead of ranked lists. | Research candidate; based on [DEXi (2025)](https://kt.ijs.si/MarkoBohanec/dexi.html). Evaluate feasibility before implementing for exp-lens or refactor-lens synthesis. |

The `SynthesisStrategy` enum and `PhoropterPrescription` types are defined in `src/autoskillit/core/types/_type_enums.py` and `src/autoskillit/core/types/_type_phoropter.py`.

## §2. Register in phoropter-registry.yaml

Add a family entry to `src/autoskillit/assets/phoropter-registry.yaml` under the `families` key.

**Required fields (post-#4894):**

- `family_id` (top-level key) — kebab-case identifier (e.g., `vis-lens`, `arch-lens`)
- `step_naming.prefix` — `null` for canonical step names, string for prefixed names. This is the only field with a live production reader (`_load_family_prefixes()` in `src/autoskillit/recipe/rules/rules_phoropter_adjacency.py`).

All other fields that historically lived here (`description`, `output_type`, `mode_label`, `lens_count`, `default_enabled`, `failure_mode`, `arg_interface`, `dial_skill`, `synthesis.strategy`, `activate_deps`, `output_prefix`, `composite_slugs`, `lens_metadata`, `phase_skip`, `status`) are either:
- Derived from each lens's `SKILL.md` content (frontmatter + body) and asserted by `tests/skills/test_phoropter_structural.py`, or
- Tracked for retirement under issue #4894 via the deferral ledger at `tests/arch/test_declarative_asset_consumption.py`.

> **§4 forward-reference.** Decide your step-naming convention in §4
> before writing the registry entry — `step_naming.prefix` value is
> derived from that decision. If you set `prefix: null` you will use
> canonical `dial` / `apply` / `synthesize` keys with
> `phoropter_family: {family-id}` annotations; if you set `prefix: ex`
> you will use `ex_dial` / `ex_apply` / `ex_synthesize` keys.

**Annotated example entry:**

```yaml
families:
  example-lens:
    step_naming:
      prefix: ex   # or null for canonical names (Case A)
```

If you set `prefix: null`, omit `step_naming` entirely — the registry loader treats a missing key as a canonical-naming family.

## §3. Generate Lens SKILL.md Files

Each lens in the family needs a `SKILL.md` file following the template variable conventions below.

**Template variables:**

- `{name}` — Human-readable lens name
- `{categories}` — Comma-separated category tags
- `{dial_skill}` — Dial phase skill name
- `{apply_skill}` — Apply phase skill name (usually the lens itself)
- `{synthesize_skill}` — Synthesize phase skill name
- `{family}` — Family identifier
- `{slug}` — Lens slug suffix
- `{mode_label}` — Display mode label
- `{mode_value}` — Mode value for context
- `{primary_question}` — The analytical question the lens answers
- `{focus_areas}` — Comma-separated focus areas
- `{description}` — One-line description
- `{hook_echo}` — Hook echo message
- `{arg_count}` — Number of positional arguments
- `{step_count}` — Number of steps in the lens workflow
- `{output_type}` — Output format
- `{output_prefix}` — Output file prefix
- `{parent_skill}` — Parent skill for lens grouping

> **Note:** The SKILL.md generator reads `{dial_skill}`, `{apply_skill}`,
> `{synthesize_skill}`, and `{output_prefix}` from each lens's SKILL.md
> body or recipe step (and `{output_prefix}` from the lens body) —
> not from the registry, which no longer carries these fields (see §2).

**Argument interface distinction:**

- **1-arg** (arch-lens): `{context_path}` only. The lens receives a single path to the PR context or codebase root.
- **2-arg** (exp-lens, vis-lens): `{context_path} {experiment_plan_path}`. The lens receives both the context and the experiment plan.

## §4. Select Step Naming Convention

Driven by the `phoropter-phase-order` rule in `src/autoskillit/recipe/rules/rules_phoropter_adjacency.py`:

- **Case A (sole family):** Use canonical step keys (`dial`, `apply`, `synthesize`) and add `phoropter_family: {family-id}` annotation on each step. The rule's `_PHOROPTER_PHASES` tuple `("dial", "apply", "synthesize")` matches these names directly.
- **Case B (coexisting families):** Use prefixed step keys (`{prefix}_dial`, `{prefix}_apply`, `{prefix}_synthesize`) without `phoropter_family` annotation. Omitting the annotation avoids `phoropter-step-interleaving` false errors when recipe-level routing steps separate family steps.

**Concrete example:** vis-lens coexisting with review-design in `research.yaml` uses `vis_dial`, `vis_apply`, `vis_synthesize` with no `phoropter_family`; review-design uses `dial`, `apply`, `synthesize` with `phoropter_family: review-design`. For full details on step naming, see [execution-contract.md §5](execution-contract.md#5-step-naming-conventions).

> **Re-anchor the registry entry (§2):** Set `step_naming.prefix` in §2
> based on this decision — `null` for Case A, the prefix token (e.g.,
> `vis`, `ex`) for Case B.

## §5. Wire Recipe Steps

Provide copy-pasteable YAML snippet patterns from [recipe-blocks/](recipe-blocks/README.md):

- [single-family-canonical.yaml](recipe-blocks/single-family-canonical.yaml) for Case A
- [multi-family-prefixed.yaml](recipe-blocks/multi-family-prefixed.yaml) for Case B
- [null-synthesis-config.yaml](recipe-blocks/null-synthesis-config.yaml) for null synthesis
- [priority-synthesis-config.yaml](recipe-blocks/priority-synthesis-config.yaml) for priority hierarchy synthesis

**Routing chain:** The `on_success` routing follows `dial → apply → synthesize`. The `skip_when_true` bypass pattern means: when `skip_when_true` evaluates to truthy, the step is skipped and control passes to the step named in `on_success` (or `on_failure` if configured as fallthrough).

## §6. Verification Checklist

Before merging a new phoropter family, verify all six touchpoints:

1. **Entry present in `src/autoskillit/assets/phoropter-registry.yaml`** with `step_naming.prefix` set per §4 (or omitted if canonical naming). Run `task test-filtered` with `AUTOSKILLIT_TEST_FILTER=conservative` scoped to `tests/contracts/test_phoropter_registry_leaf_has_consumer.py` to confirm no leaves have accreted.
2. **P5 family-generic structural test auto-discovers** the family via registry — no manual test update required.
3. **`src/autoskillit/recipe/skill_contracts.yaml`** entries for each new lens skill and the family's dial/synthesize skills.
4. **`docs/skills/catalog.md`** updated with new family section listing all lenses.
5. **`docs/skills/subsets.md`** updated with new category row for the family's tool subset tag.
6. **`docs/glossary.md`** updated with new family term (enforced by `tests/docs/test_glossary_spelling.py`).
7. **`PACK_REGISTRY`** entry added to `src/autoskillit/core/types/_type_constants_registries.py` with `PackDef(default_enabled, description)`.
8. **`tests/skills/test_phoropter_structural.py`** module-level maps (`FAMILY_ARG_INTERFACE`, `DIAL_SKILLS`) updated if any lens family's interface changes — these serve as the contract-expected values paired with body-derived tests.
