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

- `ClaudeCodeBackend`: `triage_capable=True` (set in `CLAUDE_CODE_CAPABILITIES`). Uses
  a `claude -p` subprocess with the `haiku` model class resolved through
  `backend.translate_model("haiku")` (`claude-haiku-5-5`).
- `CodexBackend`: `triage_capable=False` (set in `CodexBackend.capabilities`). Triage is skipped entirely.

## Contract Gaps

`_triage_batch()` constructs the triage command directly:

```python
triage_cmd: list[str] = [
    backend.binary_name(),
    ClaudeFlags.PRINT,
    prompt,
    ClaudeFlags.MODEL,
    backend.translate_model("haiku"),
    ClaudeFlags.OUTPUT_FORMAT,
    fmt.value,
]
```

The command uses `backend.binary_name()` (not a hardcoded `"claude"` literal) but depends on
`ClaudeFlags` constants (`PRINT`, `MODEL`, `OUTPUT_FORMAT`) which are Claude-CLI-specific.
The backend owns the model ID. Portability to a non-Claude backend still requires either:

- A backend-agnostic `triage_cmd()` protocol method on `CodingAgentBackend`, or
- A dedicated lightweight triage backend decoupled from the coding-agent backend.

## Required Steps for Portable Triage

1. Add an optional `triage_cmd(prompt: str, model: str) -> list[str]` method to
   `CodingAgentBackend`.
2. Update `_triage_batch` to call `backend.triage_cmd()` instead of constructing the
   command inline with `ClaudeFlags`.
3. Gate on `triage_capable` (already done in `_triage_batch`).

## Blast Radius

`_llm_triage.py` is a standalone module at the package root. Changes are isolated and do not
touch `BackendCapabilities` layout or the sub-protocol set. The `triage_capable` field on
`BackendCapabilities` already has a production consumer in `_triage_batch`, so no
`_FORWARD_DECLARED` exemption is needed.

## Key References

- `triage_staleness()` — entry point in `_llm_triage.py`
- `_triage_batch()` — command construction in `_llm_triage.py`
- `_triage_batch()`'s `triage_capable` guard — backend admission in `_llm_triage.py`
- `BackendCapabilities.triage_capable` — field definition in `core/types/execution/_type_backend.py`
