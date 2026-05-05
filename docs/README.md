# AutoSkillit documentation

AutoSkillit is a Claude Code plugin that runs YAML recipes through a
multi-level orchestrator. The bundled recipes implement issue → plan → worktree
→ tests → PR → merge pipelines using 52 MCP tools and 130 bundled skills.

## Start here

- [getting-started.md](getting-started.md) — install, run your first recipe
- [installation.md](installation.md) — environment, doctor checks, secrets
- [configuration.md](configuration.md) — config layers, ingredient defaults

## Quick reference

- [cli.md](cli.md) — every `autoskillit` command
- [update-checks.md](update-checks.md) — update checks, dismissal windows, `autoskillit update`
- [faq.md](faq.md) — common questions
- [glossary.md](glossary.md) — canonical terms
- [orchestration-levels.md](orchestration-levels.md) — L0–L3 orchestration hierarchy
- [version-pipeline.md](version-pipeline.md) — CI versioning pipeline, sync_versions.py, workflow reference

## Topic-based subdirectories

- [recipes/](recipes/README.md) — bundled recipes, authoring, composition
- [skills/](skills/README.md) — Tier model, catalog, subsets, overrides
- [execution/](execution/README.md) — architecture, tool access, orchestration
- [safety/](safety/README.md) — hooks, workspace isolation
- [operations/](operations/README.md) — observability
- [developer/](developer/README.md) — contributing, diagnostics, end-turn hazards
- [examples/](examples/README.md) — end-to-end pipeline runs
- [decisions/](decisions/README.md) — architecture decision records
- [design/](design/README.md) — design specifications for planned features and skills
