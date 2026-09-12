# execution/backends/

IL-1 backend abstraction layer — concrete `CodingAgentBackend` implementations.

`_codex_prelaunch.py` owns the sole composed prelaunch transaction: source-config
synchronization, hook update, snapshot, and native validation.

## Adding a new backend

1. **Create the backend module** — add `execution/backends/<name>.py` implementing the `CodingAgentBackend` Protocol. Include a concrete `BackendCapabilities` dataclass instance.

2. **Add the name constant** — add `AGENT_BACKEND_<NAME>: str = "<name>"` to `core/types/_type_constants_env.py` and include it in `KNOWN_BACKEND_NAMES`.
   *Enforced by `test_all_backends_have_name_constant` in `tests/arch/test_backend_coherence.py`.*

3. **Register in `BACKEND_REGISTRY`** — add a `'<name>': <NameBackend>` entry to the `BACKEND_REGISTRY` dict in `execution/backends/__init__.py`.

4. **Declare hook sync and MCP registration via capability fields** — populate the relevant capability fields on `BackendCapabilities` (e.g., `mcp_config_capable`) so that the `init`-time helpers in `cli/_init_helpers.py` and the `ensure_pre_launch()` protocol method discover the backend through data-driven dispatch. Do not add new conditional branches in `cli/_init_helpers.py`.
   *Enforced by `test_all_backends_have_init_hook` in `tests/arch/test_backend_coherence.py` (verifies `cli/_init_helpers.py` imports from the execution layer — the connectivity prerequisite for capability-field dispatch).*

5. **Populate doctor fields** — set `version_check_command`, `process_name`, and `min_version` on `BackendCapabilities`. Do not add a new `_check_<name>_version()` function.
   *Enforced by `test_backend_doctor_coverage` in `tests/arch/test_backend_coherence.py`.*

6. **Add a `FeatureDef`** — add an entry to `FEATURE_REGISTRY` in `core/types/_type_constants_features.py` with `default_enabled=False` and `requires_backend_alignment=True`.

7. **Extend test coverage** — add tests to `tests/execution/backends/test_backend_registry.py`, `tests/contracts/test_backend_compliance.py`, and `tests/contracts/test_backend_protocol.py`.

## Codex's two transports

Codex speaks two distinct wire formats. `codex exec` argv is used only by
`build_interactive_cmd` (the TUI, via `CodexSessionCommandMixin` in
`_codex/session_commands.py`). Every headless/food-truck/resume/skill-session
builder instead emits `codex app-server --listen stdio://` JSON-RPC
(`CodexHeadlessCommandMixin` in `_codex/headless_commands.py`, mixed into
`CodexSessionCommandMixin`), driven at runtime by `CodexAppServerDriver`
(`_codex/app_server.py`), which implements the `LineDriver` protocol. When
adding a Codex builder, mix in whichever of these two boundaries matches its
transport rather than hand-rolling either argv or JSON-RPC construction.
