# Recipes

Recipe YAML files are read by an LLM orchestrator, not a code interpreter. Step-level fields like `model:`, `note:`, and `kitchen_rules:` are prompts — if a field is absent from a step, the orchestrator doesn't know to pass it.

## Contract Card Freshness

After editing a bundled recipe YAML file, run `task regen-contracts && task compile-recipes` to regenerate its contract card. Contract cards track `recipe_source_hash` — the pre-commit hook `check-contract-freshness` will fail if a YAML edit is committed without a corresponding card update.
