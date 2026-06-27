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

The callback is threaded through `execution/process/_process_race.py`: the parameter is
declared at line 189, guarded at 253, invoked at 279, and a timeout warning emitted at 281.
Related `inspector_verdict` fields (lines 75, 102, 116) and inspector-state transitions
(lines 262, 265, 287, 295, 540) complete the protocol surface in the same file. The callback
also appears in `recording.py` at lines 141 and 432.
The wiring point is `execution/headless/_headless_execute.py:270` where
`inspector_callback=None` is hardcoded (no backend currently provides an inspector).

## Required Steps

1. Implement `build_inspector_cmd()` on `ClaudeCodeBackend` — return a `CmdSpec` that
   launches an LLM-backed inspector subprocess.
2. Implement `build_inspector_cmd()` on `CodexBackend` — or leave as `CapabilityNotSupportedError`
   if Codex cannot support inspection.
3. Set `inspector_capable=True` on backends with a working implementation.
4. Wire the callback into `_process_race._run_race()` via the `inspector_callback` parameter
   (currently hardcoded to `None` in `_headless_execute.py:270`).

## Arch Enforcement

`test_capability_consumption.py` (`tests/arch/`) verifies every `BackendCapabilities` field
has a production consumer. `inspector_capable` already satisfies this via the
`self.capabilities.inspector_capable` guard in both backends' `build_inspector_cmd()` methods.
The field is NOT in the `_FORWARD_DECLARED` exemption set.
