# Design Specifications

Design specifications for planned features and skills.

| Document | Description |
|---|---|
| [env-setup-design.md](env-setup-design.md) | Design spec for the dedicated `setup-environment` skill — Docker vs micromamba-host decision tree, structured output tokens, and recipe integration |
| [recording-replay-accepted-degradations.md](recording-replay-accepted-degradations.md) | Accepted degradations in P8 recording/replay: Claude PTY cassette format incompatibility with Codex replay, and unchanged Claude session recording path |
| [acp-session-contract.md](acp-session-contract.md) | Normative reference for P6-A3-WP1: ACP session method mapping for the `CodingAgentBackend` lifecycle, recovery ladder from `RetryReason` to `session/resume`/`session/load`/`session/new`, capabilities translation of all 41 `BackendCapabilities` fields, and Codex shim deviations |
| [forward-obligations/inspector-contract-impact.md](forward-obligations/inspector-contract-impact.md) | Contract-impact note for Health Inspector (#3534): `build_inspector_cmd` stubs, `inspector_capable` gaps, `InspectorCallback` wiring |
| [forward-obligations/recording-replay-impact.md](forward-obligations/recording-replay-impact.md) | Contract-impact note for recording/replay: backend isolation invariant, format-detection extension points for future backends |
| [forward-obligations/triage-portability-impact.md](forward-obligations/triage-portability-impact.md) | Contract-impact note for triage portability: `triage_capable` gate, hardcoded Claude CLI in `_llm_triage.py`, portable `triage_cmd` path |
| [forward-obligations/heterogeneous-routing-impact.md](forward-obligations/heterogeneous-routing-impact.md) | Contract-impact note for heterogeneous per-item backend routing: `ToolContext.backend` single-backend constraint, `BackendSelector` proposal, `food_truck_capable` gate |
