# rules/campaign/

Campaign semantic rule modules (5 rule files).

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | Docstring-only — rules register via `@semantic_rule` decorator on import |
| `rules_campaign_capture.py` | Campaign capture validation: identifier keys, result refs, sentinel cross-checks |
| `rules_campaign_deps.py` | Campaign dependency graph rules: valid refs, acyclic, sequential |
| `rules_campaign_dispatch.py` | Campaign dispatch structure: kind, names, recipe refs, packs, task |
| `rules_campaign_flow.py` | Campaign flow control: gates, paths, campaign refs, version, skip-when |
| `rules_campaign_ingredients.py` | Campaign ingredient validation: keys, dangling, required, string types |

## Architecture Notes

No cross-imports between rule modules. Each rule receives a `ValidationContext` argument.
