# execution/backends/

IL-1 backend abstraction layer — concrete `CodingAgentBackend` implementations.

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | `BACKEND_REGISTRY`, `get_backend()` factory, re-exports |
| `claude.py` | `ClaudeCodeBackend`, `ClaudeEnvPolicy`, `ClaudeSessionLocator`, `ClaudeStreamParser`, `ClaudeResultParser` |
| `codex.py` | `CodexFlags`, `CodexBackend`, `CodexStreamParser`, `CodexEnvPolicy`, `CodexSessionLocator`, `CodexResultParser`, `_CodexParseAccumulator`, `_scan_codex_ndjson`, `CODEX_ENV_DENYLIST`, `CODEX_ENV_PREFIX_DENYLIST`, `ensure_codex_mcp_registered`, `_read_codex_config`, `_write_codex_config`, `_serialize_toml`, `_format_toml_value`, `_format_inline_table`, `_emit_toml_table`, `_is_autoskillit_registered` |
