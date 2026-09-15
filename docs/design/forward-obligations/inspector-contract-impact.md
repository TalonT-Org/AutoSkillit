# Health Inspector Contract Impact

| Field | Value |
|-------|-------|
| Status | Draft |
| Date | 2026-06-27 |
| Linked Issue | #3534 |

## Obligation

Both `ClaudeCodeBackend` and `CodexBackend` raise `CapabilityNotSupportedError` from
`build_inspector_cmd()` when `inspector_capable` is `False` (the current default), and
raise `AssertionError` if `inspector_capable` were ever set to `True` without a real
implementation:

- `claude.py:872–876` — `ClaudeCodeBackend.build_inspector_cmd()`
- `codex.py:1117–1121` — `CodexBackend.build_inspector_cmd()`

The Health Inspector feature (#3534) requires a real implementation on each backend.

## Contract Gaps

- `BackendCapabilities.inspector_capable` is `False` on both backends:
  - `CLAUDE_CODE_CAPABILITIES` (`_type_backend.py:253`): `inspector_capable=False`
  - `CodexBackend.capabilities` property (`codex.py:584`): `inspector_capable=False`
- Neither backend provides a working `build_inspector_cmd()` — both have guard-then-assert
  stubs.

## Protocol Surface

Already defined in `core/types/_type_inspector.py`:

- **`InspectorEvidence`** (lines 15–24, frozen dataclass): `idle_seconds`, `stdout_path`,
  `jsonl_lines`, `cpu_trend`, `rss_trend`, `connection_summary`,
  `execution_marker_present`, `dispatch_context`
- **`InspectorVerdict`** (lines 27–32, frozen dataclass): `action`, `reasoning`,
  `confidence`, `elapsed_seconds`
- **`InspectorCallback`** (line 35): `Callable[[InspectorEvidence], Awaitable[InspectorVerdict]]`

The public process facade accepts the callback in `run_managed_async()` and injects it into
`execution/process/_race_watchers.py`'s idle watcher. That watcher packages the evidence,
applies the bounded inspection budget, and records non-spare verdicts on the
`RaceAccumulator` defined in `_process_race.py`. The callback also appears in
`recording.py`. The backend wiring point remains
`execution/headless/_headless_execute.py`, where `inspector_callback=None` is hardcoded
because no backend currently provides an inspector.

## Required Steps

1. Implement `build_inspector_cmd()` on `ClaudeCodeBackend` — return a `CmdSpec` that
   launches an LLM-backed inspector subprocess.
2. Implement `build_inspector_cmd()` on `CodexBackend` — or leave as `CapabilityNotSupportedError`
   if Codex cannot support inspection.
3. Set `inspector_capable=True` on backends with a working implementation.
4. Pass the backend callback from `_headless_execute.py` into
   `run_managed_async(inspector_callback=...)` instead of the current `None` value.

## Arch Enforcement

`test_capability_consumption.py` (`tests/arch/`) verifies every `BackendCapabilities` field
has a production consumer. `inspector_capable` already satisfies this via the
`self.capabilities.inspector_capable` guard in both backends' `build_inspector_cmd()` methods.
The field is NOT in the `_FORWARD_DECLARED` exemption set.
