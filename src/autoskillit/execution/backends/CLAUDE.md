# execution/backends/

IL-1 backend abstraction layer — concrete `CodingAgentBackend` implementations.

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | `BACKEND_REGISTRY`, `get_backend()` factory, re-exports |
| `claude.py` | `ClaudeCodeBackend`, `ClaudeEnvPolicy`, `ClaudeSessionLocator`, `ClaudeStreamParser`, `ClaudeResultParser` |
| `codex.py` | `CodexFlags`, `CodexBackend`, `CodexStreamParser`, `CodexEnvPolicy`, `CodexSessionLocator`, `CodexResultParser`, `_CodexParseAccumulator`, `_scan_codex_ndjson` |
