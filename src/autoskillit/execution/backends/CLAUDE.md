# execution/backends/

IL-1 backend abstraction layer — concrete `CodingAgentBackend` implementations.

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | `BACKEND_REGISTRY`, `get_backend()` factory, re-exports |
| `claude.py` | `ClaudeCodeBackend`, `ClaudeEnvPolicy`, `ClaudeSessionLocator`, `ClaudeStreamParser`, `ClaudeResultParser` (prompt utilities moved to `_claude_prompt.py`) |
| `_claude_prompt.py` | Prompt injection utilities, session constants (`_ensure_skill_prefix`, `_inject_completion_directive`, `_compose_resume_prompt`, etc.), shared by claude + codex + commands |
| `codex.py` | `CodexFlags`, `CodexBackend`, `CodexEnvPolicy`, `CodexSessionLocator` (parse/config moved to `_codex_parse.py` / `_codex_config.py`) |
| `_codex_config.py` | TOML serialization, MCP registration (`ensure_codex_mcp_registered`, `_serialize_toml`) |
| `_codex_parse.py` | `CodexStreamParser`, `CodexResultParser`, `_scan_codex_ndjson`, `_CodexParseAccumulator` |
