@AGENTS.md

# Claude Code — Project-Specific Rules

Mandatory instructions for AI-assisted development in this repository.

## CLAUDE.md Modifications

  * **Correcting existing content is permitted**: If you discover that CLAUDE.md contains inaccurate information (wrong file paths, stale names, incorrect tool attributions), you may correct it without being asked.
  * **Adding new content requires explicit instruction**: Never add new sections, bullet points, entries, or any new information to CLAUDE.md unless the user has explicitly asked you to update or extend it. Corrections to existing facts ≠ permission to expand scope.

## Pyright LSP Usage

The `LSP` tool provides type-aware code intelligence via Pyright. Use it for precise
navigation instead of grep when tracing symbols through imports, re-exports, or protocols.

**Available operations** (all take `filePath`, `line`, `character` — 1-based):

| Operation | Use case |
|-----------|----------|
| `goToDefinition` | Jump to where a symbol is defined (follows imports/re-exports) |
| `findReferences` | Find all usages of a symbol across the codebase |
| `documentSymbol` | List all classes, functions, and variables in a file |
| `goToImplementation` | Find concrete implementations of a Protocol or ABC |
| `prepareCallHierarchy` | Get the call hierarchy item at a position |
| `incomingCalls` | Find all functions/methods that call the function at a position |
| `outgoingCalls` | Find all functions/methods called by a function |

**When to use LSP over grep:**
- Tracing a symbol through re-exports (e.g., `core/__init__.py` -> actual definition)
- Finding all implementations of a Protocol
- Mapping call hierarchies (who calls X, what does X call)
- Understanding a file's structure before editing

**When grep is still better:**
- Searching for string literals, comments, or non-symbol patterns
- Searching across non-Python files (YAML, JSON, markdown)

## Subagent Configuration

When using subagents, invoke with `CLAUDE_CODE_EXIT_AFTER_STOP_DELAY=120000` to ensure subagents exit when finished.
