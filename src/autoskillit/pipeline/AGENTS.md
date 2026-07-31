# pipeline/

IL-1 pipeline state — per-tool-call state containers, gate logic, audit log, telemetry.

## Architecture Notes

`ToolContext` is the composition root injected into every MCP tool handler via
`server/_factory.py:make_context()`. All implementations satisfy protocols defined in
`core/types/`. `gate.py` is the sole source of structured gate-error results; no tool
handler constructs gate errors directly.
