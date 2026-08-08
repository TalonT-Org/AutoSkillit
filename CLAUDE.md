@AGENTS.md

# Claude Code — Project-Specific Rules

Mandatory instructions for AI-assisted development in this repository.

## CLAUDE.md Modifications

  * **Correcting existing content is permitted**: If you discover that CLAUDE.md contains inaccurate information (wrong file paths, stale names, incorrect tool attributions), you may correct it without being asked.
  * **Adding new content requires explicit instruction**: Never add new sections, bullet points, entries, or any new information to CLAUDE.md unless the user has explicitly asked you to update or extend it. Corrections to existing facts ≠ permission to expand scope.

## Pyright LSP Usage

The `LSP` tool provides Pyright-backed navigation — operations `goToDefinition`, `findReferences`, `documentSymbol`, `goToImplementation`, `prepareCallHierarchy`, `incomingCalls`, `outgoingCalls` (all take `filePath`, `line`, `character`, 1-based).

Prefer LSP over grep for tracing symbols through imports/re-exports (e.g., `core/__init__.py` → actual definition), finding Protocol implementations, and mapping call hierarchies. Grep remains better for string literals, comments, and non-Python files (YAML, JSON, markdown).

## Subagent Configuration

When using subagents, invoke with `CLAUDE_CODE_EXIT_AFTER_STOP_DELAY=120000` to ensure subagents exit when finished.
