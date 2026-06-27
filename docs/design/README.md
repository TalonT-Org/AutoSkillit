# Design Specifications

Design specifications for planned features and skills.

| Document | Description |
|---|---|
| [env-setup-design.md](env-setup-design.md) | Design spec for the dedicated `setup-environment` skill — Docker vs micromamba-host decision tree, structured output tokens, and recipe integration |
| [recording-replay-accepted-degradations.md](recording-replay-accepted-degradations.md) | Accepted degradations in P8 recording/replay: Claude PTY cassette format incompatibility with Codex replay, and unchanged Claude session recording path |
| [acp-session-contract.md](acp-session-contract.md) | Normative reference for P6-A3-WP1: ACP session method mapping for the `CodingAgentBackend` lifecycle, recovery ladder from `RetryReason` to `session/resume`/`session/load`/`session/new`, capabilities translation of all 41 `BackendCapabilities` fields, and Codex shim deviations |
