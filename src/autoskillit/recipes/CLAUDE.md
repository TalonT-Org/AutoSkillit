# Recipes

Recipe YAML files are read by an LLM orchestrator, not a code interpreter. Step-level fields like `model:`, `note:`, and `kitchen_rules:` are prompts — if a field is absent from a step, the orchestrator doesn't know to pass it.
