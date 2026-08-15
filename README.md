<p align="center">
  <img src="assets/banner.gif" alt="AutoSkillit" width="830">
</p>

AutoSkillit is a Claude Code plugin that runs YAML recipes through a two-tier
orchestrator. Bundled recipes turn GitHub issues into merged PRs by chaining
plan, dry-walkthrough, worktree, test, and PR-review skills against 75 MCP
tools and 142 bundled skills.

https://github.com/user-attachments/assets/bcd910c8-7269-46d6-a496-53b2cb24d212

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) — package manager
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) — `npm install -g @anthropic-ai/claude-code`
- [gh CLI](https://cli.github.com/) — required for PR creation, issue management, CI status

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/TalonT-Org/AutoSkillit/stable/install.sh | sh
```

## Quick try

```bash
cd your-project
autoskillit init
autoskillit order implementation
```

## What it does

Each bundled recipe is a sequenced graph of skill invocations. The orchestrator
holds a kitchen of 52 kitchen-tagged MCP tools within a 75-tool server, launches
headless sessions for the heavy work, and
routes verdicts through retry, merge, and review gates. The 5 bundled recipes
are `implementation`, `implementation-groups`, `merge-prs`, `remediation`, and
`research`.

Writable Codex skill sessions can also delegate one bounded artifact to a behaviorally
read-only evidence reader. That reader runs as a separate sterile process and receives only
authenticated paging tools; see [MCP tool access control](docs/execution/tool-access.md).

## Documentation

Full docs are under [docs/](docs/README.md). Topic entry points:

- [docs/getting-started.md](docs/getting-started.md)
- [docs/installation.md](docs/installation.md)
- [docs/configuration.md](docs/configuration.md)
- [docs/recipes/overview.md](docs/recipes/overview.md)
- [docs/execution/architecture.md](docs/execution/architecture.md)
- [docs/safety/hooks.md](docs/safety/hooks.md)
- [docs/developer/contributing.md](docs/developer/contributing.md)

## Examples

Real end-to-end runs against `TalonT-Org/spectral-init` are in
[docs/examples/research-pipeline.md](docs/examples/research-pipeline.md).

## License

MIT
