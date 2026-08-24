# types/

Type re-export hub and all typed building blocks for the autoskillit package (IL-0).

## Architecture Notes

Internal dependency DAG: enums -> recipe_sections -> constants_registries -> constants_features; enums -> results -> protocols -> helpers; enums -> phoropter; enums + phoropter -> tradition_manifest; enums -> exceptions; exploration -> exceptions. `_type_intake_policy` is a DAG leaf — stdlib-only, zero sibling imports. `_type_recipe_sections.py` owns recipe-section registry and pagination-policy construction; `_type_constants_registries.py` imports its ten public names as a facade. All modules have zero `autoskillit` imports outside this sub-package (IL-0 hard constraint). Production code imports from `autoskillit.core`, not from this package directly.

## Extension Bundle Pattern

New feature fields go on frozen dataclass bundles (`InfraOutcome`, `ProviderOutcome`), not flat on `SkillResult`. Bundles are embedded as `field(default_factory=...)` on `SkillResult`. The `to_json()` method flattens bundle fields to top-level JSON keys for backward compatibility.
