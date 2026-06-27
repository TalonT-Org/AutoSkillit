# Triage Portability Contract Impact

| Field | Value |
|-------|-------|
| Status | Draft |
| Date | 2026-06-27 |

## Obligation

LLM triage (contract-staleness triage via `_llm_triage.py`) is gated by
`BackendCapabilities.triage_capable`. Backends with `triage_capable=False` cause
`_triage_batch` to return all hash-mismatch items as `meaningful=True` without launching
an LLM subprocess.

## Current State

- `ClaudeCodeBackend`: `triage_capable=True` (set in `CLAUDE_CODE_CAPABILITIES`,
  `_type_backend.py:229`). Uses a `claude -p` subprocess with Haiku model.
- `CodexBackend`: `triage_capable=False` (`codex.py:559`). Triage is skipped entirely.

## Contract Gaps

`_triage_batch()` (`_llm_triage.py:80`) constructs the triage command at lines 123–131:

```python
triage_cmd: list[str] = [
    backend.binary_name(),
    ClaudeFlags.PRINT,
    prompt,
    ClaudeFlags.MODEL,
    "claude-haiku-4-5-20251001",
    ClaudeFlags.OUTPUT_FORMAT,
    fmt.value,
]
```

The command uses `backend.binary_name()` (not a hardcoded `"claude"` literal) but depends on
`ClaudeFlags` constants (`PRINT`, `MODEL`, `OUTPUT_FORMAT`) which are Claude-CLI-specific.
The model `"claude-haiku-4-5-20251001"` is hardcoded. Portability to a non-Claude backend
requires either:

- A backend-agnostic `triage_cmd()` protocol method on `CodingAgentBackend`, or
- A dedicated lightweight triage backend decoupled from the coding-agent backend.

## Required Steps for Portable Triage

1. Add an optional `triage_cmd(prompt: str, model: str) -> list[str]` method to
   `CodingAgentBackend` (or add a `BackendCapabilities.triage_model` field).
2. Update `_triage_batch` to call `backend.triage_cmd()` instead of constructing the
   command inline with `ClaudeFlags`.
3. Gate on `triage_capable` (already done at `_llm_triage.py:107`).

## Blast Radius

`_llm_triage.py` is a standalone module at the package root. Changes are isolated and do not
touch `BackendCapabilities` layout or the sub-protocol set. The `triage_capable` field on
`BackendCapabilities` (`_type_backend.py:86`) already has production consumers (the guard at
`_llm_triage.py:107`), so no `_FORWARD_DECLARED` exemption is needed.

## Key References

- `_llm_triage.py:33` — `triage_staleness()` entry point
- `_llm_triage.py:80` — `_triage_batch()` with command construction
- `_llm_triage.py:107` — `triage_capable` guard
- `core/types/_type_backend.py:86` — `triage_capable` field definition
