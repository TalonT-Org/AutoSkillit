# Freeze Checklist: `execution/commands.py` Public Import Surface

> **Status:** VERIFIED against live codebase on 2026-05-19
> **Source:** `src/autoskillit/execution/commands.py`
> **Verification queries run:**
> - `grep -rn "from autoskillit.execution.commands import" src/` → 3 production files
> - `grep -rn "from autoskillit.execution.commands import" tests/` → 13+ test files
> - `build_skill_session_cmd|build_food_truck_cmd` in `execution/__init__.__all__` → 0 matches (gap confirmed)
> - `execution/__init__.__all__` from commands.py → 5 names confirmed

---

## 1. Public Surface Freeze Table

### 1a. Builder Functions (5) — MUST PRESERVE

| # | Name | In `__init__.__all__`? | Production Importers | Test Importers |
|---|------|------------------------|---------------------|----------------|
| 1 | `build_interactive_cmd` | YES | `execution/__init__.py`, `backends/claude.py:274` (deferred) | `test_commands.py`, `test_flag_contracts.py` |
| 2 | `build_headless_cmd` | YES | `execution/__init__.py`, `backends/claude.py:224` (deferred), `backends/claude.py:258` (deferred) | `test_commands.py`, `test_flag_contracts.py`, `test_claude_code_backend.py` |
| 3 | `build_headless_resume_cmd` | YES | `execution/__init__.py`, `backends/claude.py:296` (deferred) | `test_commands.py`, `test_output_format_contract.py` |
| 4 | `build_skill_session_cmd` | **NO** | `headless/__init__.py:50` | `test_commands.py`, `test_output_format_contract.py`, `test_resume_prompt.py:52,60,70`, `test_recording.py` |
| 5 | `build_food_truck_cmd` | **NO** | `headless/__init__.py:50` | `test_commands.py`, `test_output_format_contract.py` |

**Monkeypatch consumers for `build_food_truck_cmd`:**
- `tests/execution/test_headless_dispatch.py`
- `tests/fleet/test_fleet_e2e.py`

**AST enforcement note:** `tests/arch/test_ast_rules.py:415-421` references `build_interactive_cmd`, `build_headless_cmd`, `build_headless_resume_cmd` by `(filename, funcname)` string pair in ALLOWED set. ARCH-011 at line 898 hardcodes `SRC_ROOT / "execution" / "commands.py"`.

### 1b. Dataclasses (2) — MUST PRESERVE

| # | Name | Fields | In `__init__.__all__`? | Production Importers | Test Importers |
|---|------|--------|------------------------|---------------------|----------------|
| 1 | `ClaudeInteractiveCmd` | `cmd: list[str]`, `env: Mapping[str, str]` | YES | `execution/__init__.py` | `test_commands.py` |
| 2 | `ClaudeHeadlessCmd` | Type alias for `CmdSpec` (`cmd: tuple[str, ...]`, `env: Mapping[str, str]`, `cwd: str`) | YES | `execution/__init__.py`, `headless/__init__.py` (TYPE_CHECKING) | `test_commands.py`, `test_headless_provider_fallback.py` (4 deferred), `test_headless_provider_forwarding.py` (5 deferred), `test_idle_output_env.py`, `test_flush_provider_integration.py` (4 deferred), `test_process_env_boundary.py` |

### 1c. Constants (3) — MUST PRESERVE

| # | Name | Type | Value | In `__init__.__all__`? | Importers |
|---|------|------|-------|------------------------|-----------|
| 1 | `_MAX_MCP_OUTPUT_TOKENS_VALUE` | `str` | `"50000"` | NO (imported with `# noqa: F401`) | `execution/__init__.py`, `test_commands.py`, `test_cook_env_scrub.py` |
| 2 | `_SESSION_BASELINE_ENV` | `Mapping[str, str]` | `MappingProxyType(...)` | NO | `test_commands.py:905` (deferred) |
| 3 | `_HEADLESS_EXCLUSIVE_VARS` | `frozenset[str]` | env var names | NO | `test_commands.py` (module-level + deferred:867) |

**Dual-copy sync obligation for `_HEADLESS_EXCLUSIVE_VARS`:**
- `IDE_ENV_DENYLIST` in `core/_claude_env.py`
- `AUTOSKILLIT_PRIVATE_ENV_VARS` in `core/types/_type_constants.py`
- See block comment at `commands.py:163-171`: "All lists must be kept in sync when adding new exclusive variables."

### 1d. Re-exported Types (via `# noqa: F401, TC001`)

| Name | Source | Purpose |
|------|--------|---------|
| `CmdSpec` | `autoskillit.core` | Canonical command spec; `ClaudeHeadlessCmd` is a type alias for this |
| `SessionCheckpoint` | `autoskillit.core` | Dual purpose: (1) re-export for downstream, (2) runtime dependency — `_build_resume_context` accesses `checkpoint.completed_items` and `checkpoint.step_name`. TC001 suppresses ruff's `TYPE_CHECKING` suggestion which would break runtime usage. |

---

## 2. Private Helpers Classification

| # | Name | Classification | Rationale |
|---|------|---------------|-----------|
| 1 | `_apply_output_format` | **Safely moveable** | Zero external importers in `src/` or `tests/`. |
| 2 | `_inject_completion_reminder` | **Safely moveable** | Zero external importers. Called within `commands.py` only. |
| 3 | `_ensure_skill_prefix` | **Must coordinate test updates** | Directly tested in `test_headless_core.py:17`. |
| 4 | `_inject_completion_directive` | **Must coordinate test updates** | Directly tested in `test_headless_core.py:29` and `test_tools_execution_command.py:13`. |
| 5 | `_inject_cwd_anchor` | **Must coordinate test updates** | Directly tested in `test_headless_core.py` (5 deferred sites). |
| 6 | `_inject_narration_suppression` | **Must coordinate test updates** | Directly tested in `test_headless_core.py` (8 deferred sites). |
| 7 | `_build_resume_context` | **Must coordinate test updates** | Directly tested in `test_resume_prompt.py:9`. Uses `SessionCheckpoint` at runtime. |

**Summary:** 2 helpers safely moveable (zero external test imports), 5 helpers locked to `commands.py` or require coordinated test updates if moved.

---

## 3. `execution/__init__.py` Re-export Gap Analysis

**Names in `__all__` (5 from commands.py):**
- `ClaudeInteractiveCmd`
- `ClaudeHeadlessCmd`
- `build_interactive_cmd`
- `build_headless_cmd`
- `build_headless_resume_cmd`

**Names NOT in `__all__` but imported:**
- `_MAX_MCP_OUTPUT_TOKENS_VALUE` — `# noqa: F401`, accessible as `autoskillit.execution._MAX_MCP_OUTPUT_TOKENS_VALUE`

**Names NOT re-exported at all (gap — intentional):**
- `build_skill_session_cmd` — imported directly by `headless/__init__.py`
- `build_food_truck_cmd` — imported directly by `headless/__init__.py`

**Assessment:** L2 session builders are intentionally excluded from `__init__.__all__`. They are internal to the execution package's headless subsystem. No TODO or comment suggests this should change.

**IL-004 constraint:** `execution/` has a forbidden-modules list in `pyproject.toml` (lines 211-228): `[config, pipeline, workspace, recipe, migration, server, cli, report]`. Any forwarding shim must not introduce runtime imports from these modules.

---

## 4. Production Import Graph

```
autoskillit.core
  └── commands.py
        Imports: ClaudeFlags, OutputFormat, PluginSource, ResumeSpec,
                 build_agent_env, extract_skill_name, temp_dir_display_str,
                 SessionCheckpoint [re-export + runtime use], + 7 constant/type imports

  ├── execution/__init__.py
  │     Imports: _MAX_MCP_OUTPUT_TOKENS_VALUE, ClaudeHeadlessCmd, ClaudeInteractiveCmd,
  │              build_headless_cmd, build_headless_resume_cmd, build_interactive_cmd
  │     __all__: [5 names from commands.py]
  │
  ├── execution/headless/__init__.py
  │     Line 50: build_food_truck_cmd, build_skill_session_cmd
  │     Line 99: ClaudeHeadlessCmd (TYPE_CHECKING)
  │
  └── execution/backends/claude.py
        Deferred imports:
          Line 224: build_headless_cmd
          Line 258: build_headless_cmd as _build
          Line 274: build_interactive_cmd as _build
          Line 296: build_headless_resume_cmd as _build
```

**Indirect consumers (monkeypatch — not direct import):**
- `tests/execution/test_headless_dispatch.py`
- `tests/fleet/test_fleet_e2e.py`

**String-reference consumers (not imports):**
- `tests/arch/test_ast_rules.py:415-421` — ALLOWED set string pairs
- `tests/arch/test_ast_rules.py:898` — hardcoded path

---

## 5. Forwarding Shim Contract Per Builder

| Builder | Shim Location | Contract |
|---------|---------------|----------|
| `build_interactive_cmd` | `commands.py` | Preserve full signature. 2 production import sites + 2 test importers + ALLOWED set entry. |
| `build_headless_cmd` | `commands.py` | Preserve full signature. 3 production import sites (1 in `__init__`, 2 deferred in `backends/claude.py`) + 3 test importers + ALLOWED set entry. |
| `build_headless_resume_cmd` | `commands.py` | Preserve full signature. 2 production import sites + 2 test importers + ALLOWED set entry. |
| `build_skill_session_cmd` | `commands.py` | Preserve full signature. 1 production importer (`headless/__init__.py`) + 4 test importers. **Must NOT be added to `__init__.__all__`** — exclusion is intentional. |
| `build_food_truck_cmd` | `commands.py` | Preserve full signature. 1 production importer (`headless/__init__.py`) + 2 test importers + 2 monkeypatch consumers. **Must NOT be added to `__init__.__all__`** — exclusion is intentional. |

**Shim invariants:**
- Each shim is a thin `def` that calls through with `*args, **kwargs`
- `ClaudeInteractiveCmd` and `ClaudeHeadlessCmd` are re-exported as names, not shims
- `SessionCheckpoint` re-export must be preserved — runtime usage via `_build_resume_context`
- New shims must not violate IL-004 (no runtime imports from forbidden modules)
- Any new `build_*_cmd` that constructs `["claude", ...]` list literal must be added to ALLOWED set in `test_ast_rules.py`

---

## 6. Complete Test File Inventory (13 files)

| # | Test File | Names Imported | Import Style |
|---|-----------|---------------|--------------|
| 1 | `tests/execution/test_commands.py` | All 5 builders, 2 DCs, 2 private constants (module-level); `_HEADLESS_EXCLUSIVE_VARS` (deferred:867), `_SESSION_BASELINE_ENV` (deferred:905) | Module-level (9 names) + 2 deferred |
| 2 | `tests/execution/test_flag_contracts.py` | `build_headless_cmd`, `build_interactive_cmd` | Module-level |
| 3 | `tests/execution/test_output_format_contract.py` | `build_food_truck_cmd`, `build_headless_resume_cmd`, `build_skill_session_cmd` | Module-level |
| 4 | `tests/execution/test_resume_prompt.py` | `_build_resume_context` (module-level:9), `build_skill_session_cmd` (deferred:52,60,70) | 1 module-level + 3 deferred |
| 5 | `tests/execution/test_recording.py` | `build_skill_session_cmd` | Module-level |
| 6 | `tests/execution/test_headless_core.py` | `_ensure_skill_prefix` (module-level:17); `_inject_completion_directive` (deferred:29); `_inject_cwd_anchor` (deferred x5:2517-2544); `_inject_narration_suppression` (deferred x8:2557-2602) | 1 module-level + 14 deferred |
| 7 | `tests/server/test_tools_execution_command.py` | `_inject_completion_directive` | Module-level |
| 8 | `tests/execution/test_headless_provider_fallback.py` | `ClaudeHeadlessCmd` | 4 deferred (109,137,165,189) |
| 9 | `tests/execution/test_headless_provider_forwarding.py` | `ClaudeHeadlessCmd` | 5 deferred (224,265,519,573,622) |
| 10 | `tests/execution/test_idle_output_env.py` | `ClaudeHeadlessCmd` | Deferred (174) |
| 11 | `tests/execution/test_flush_provider_integration.py` | `ClaudeHeadlessCmd` | 4 deferred (68,96,174,215) |
| 12 | `tests/execution/test_process_env_boundary.py` | `ClaudeHeadlessCmd` | Deferred (101) |
| 13 | `tests/execution/backends/test_claude_code_backend.py` | `build_headless_cmd` | Deferred (47) |

**Additional indirect consumer:**
- `tests/cli/test_cook_env_scrub.py` — imports `_MAX_MCP_OUTPUT_TOKENS_VALUE` from `autoskillit.execution` (package re-export path)

---

## Verification Checklist

- [x] All 5 builder function names documented with importer lists
- [x] Both dataclass names documented with importer lists
- [x] All 3 constants documented with import paths
- [x] Private helpers classified as moveable vs locked
- [x] `build_skill_session_cmd` and `build_food_truck_cmd` documented as NOT in `__init__.__all__` but imported directly
- [x] Checklist matches actual grep results
- [x] Dual-copy sync obligation verified (`IDE_ENV_DENYLIST` in `core/_claude_env.py`, `AUTOSKILLIT_PRIVATE_ENV_VARS` in `core/types/_type_constants.py`)
- [x] IL-004 forbidden modules confirmed in `pyproject.toml`
