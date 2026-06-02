# execution/backends/

IL-1 backend abstraction layer — concrete `CodingAgentBackend` implementations.

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | `BACKEND_REGISTRY`, `get_backend()` factory, re-exports |
| `claude.py` | `ClaudeCodeBackend` (incl. `validate_session_layout`), `ClaudeEnvPolicy`, `ClaudeSessionLocator`, `ClaudeStreamParser`, `ClaudeResultParser` (prompt utilities moved to `_claude_prompt.py`) |
| `_claude_prompt.py` | Prompt injection utilities, session constants (`_ensure_skill_prefix`, `_inject_completion_directive`, `_compose_resume_prompt`, etc.), shared by claude + codex + commands |
| `codex.py` | `CodexFlags`, `CodexBackend` (incl. `validate_session_layout`), `CodexEnvPolicy`, `CodexSessionLocator` (parse/config moved to `_codex_parse.py` / `_codex_config.py`) |
| `_codex_config.py` | TOML serialization with `[[key]]` array-of-tables support, MCP registration (`ensure_codex_mcp_registered`, `_serialize_toml`) |
| `_codex_parse.py` | `CodexStreamParser`, `CodexResultParser`, `_scan_codex_ndjson`, `_CodexParseAccumulator` |
| `codex_scenario_player.py` | `CodexScenarioPlayer`, `make_codex_scenario_player`, `CodexStepRecord`, `CodexScenario`, `_load_manifest`, `_FakeCodexCLI`, `_write_shim_script` — scenario replay data layer for Codex backend |
| `_cmd_builder.py` | `CmdBuilder`, `CmdOrderingError` — typed builder that enforces positional-before-variadic ordering by construction |

## Adding a new backend

1. **Create the backend module** — add `execution/backends/<name>.py` implementing the `CodingAgentBackend` Protocol. Include a concrete `BackendCapabilities` dataclass instance.

2. **Add the name constant** — add `AGENT_BACKEND_<NAME>: str = "<name>"` to `core/types/_type_constants_env.py` and include it in `KNOWN_BACKEND_NAMES`.
   *Enforced by `test_all_backends_have_name_constant` in `tests/arch/test_backend_coherence.py`.*

3. **Register in `BACKEND_REGISTRY`** — add a `'<name>': <NameBackend>` entry to the `BACKEND_REGISTRY` dict in `execution/backends/__init__.py`.

4. **Declare hook sync and MCP registration via capability fields** — populate the relevant capability fields on `BackendCapabilities` (e.g., `mcp_config_capable`) so that the `init`-time helpers in `cli/_init_helpers.py` and the `ensure_pre_launch()` protocol method discover the backend through data-driven dispatch. Do not add new conditional branches in `cli/_init_helpers.py`.
   *Enforced by `test_all_backends_have_init_hook` in `tests/arch/test_backend_coherence.py`.*

5. **Populate doctor fields** — set `version_check_command`, `process_name`, and `min_version` on `BackendCapabilities`. Do not add a new `_check_<name>_version()` function.
   *Enforced by `test_backend_doctor_coverage` in `tests/arch/test_backend_coherence.py`.*

6. **Add a `FeatureDef`** — add an entry to `FEATURE_REGISTRY` in `core/types/_type_constants_features.py` with `default_enabled=False` and `requires_backend_alignment=True`.

7. **Extend test coverage** — add tests to `tests/execution/backends/test_backend_registry.py`, `tests/contracts/test_backend_compliance.py`, and `tests/contracts/test_backend_protocol.py`.
