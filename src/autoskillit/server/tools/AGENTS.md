# tools/

MCP `@mcp.tool()` handlers registered on import (22 tool modules).

The package initializer is docstring-only; importing tool modules performs registration.
`serve_recipe()` is the only legal caller of `load_and_validate` within `server/tools/`.

## Architecture Notes

Side-effect registration (same pattern as `recipe/rules/`). The `server/__init__.py` owns the `mcp` app object; tool modules import it from the server layer. All tools except the replay-journaled, effectful `open_kitchen` transition require `readOnlyHint: True` (see `server/AGENTS.md`).

`post_pr_review` is the sole headless PR-review publication authority. It validates exact
repository/head/iteration identity, delegates durable pacing and reconciliation to the
GitHub-review execution service, and returns the authoritative receipt identity that recipe
effect gates must verify. Its `readOnlyHint: True` preserves server-side parallel scheduling;
the private ledger provides the required mutation serialization.
