# tools/

MCP `@mcp.tool()` handlers registered on import (21 tool modules).

The package initializer is docstring-only; importing tool modules performs registration.
`serve_recipe()` is the only legal caller of `load_and_validate` within `server/tools/`.

## Architecture Notes

Side-effect registration (same pattern as `recipe/rules/`). The `server/__init__.py` owns the `mcp` app object; tool modules import it from the server layer. All tools require `readOnlyHint: True` (see `server/AGENTS.md`).
