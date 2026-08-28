# fleet/dispatch/

IL-2 fleet dispatch engine — phased shards decomposed from `fleet/_api.py`
(issue #4851, refactor only — no behavior change).

## Architecture

The legacy `fleet/_api.py` (1592 lines) was split into a per-phase shard
package so each phase of the dispatch transaction can be reviewed and
tested in isolation. `fleet/_api.py` is now a 55-line public-API facade
re-exporting the canonical symbol list per REQ-IMP-001.

### Shard layout

| Shard | Phase | Owns |
|---|---|---|
| `_api.py` | Orchestrator | `execute_dispatch`, `_run_dispatch`, `DispatchSpawnFailed` |
| `_validation.py` | A — pre-launch gating | recipe load, validation, kind check, ingredient assembly, `apply_config_authoritative_overrides` |
| `_lineage.py` | B — identity + lineage | `DispatchStateHandle` creation, prior-success short-circuit, captured-ingredient interpolation, launch tuple, `prepare_food_truck_lineage` |
| `_execution.py` | C — execution | spawn/heartbeat/dispatch-fork triple-nested block, `_on_spawn`/`_on_session_id`/`_on_launch_resolved` LOCAL closures, spawn-error gate |
| `_cleanup.py` | D — cleanup | `handle_cancellation`, `handle_generic_exception`, `run_finally_label_cleanup`, `_post_dispatch_cleanup` |
| `_classification.py` | E — outcome | `run_outcome_classification`, `finalize_state_write` |
| `_heartbeat.py` | helper | `_dispatch_heartbeat` asynccontextmanager |
| `_pid.py` | helper | `_write_pid` on_spawn callback (with fail-closed `kill_process_tree`) |
| `_errors.py` | helper | `complete_failure_with_state` (closure released) |

### Threading contract

The closure-scoped state the legacy `_run_dispatch` captured
(`_dispatched_pid`, `_spawn_error`, `_dispatch_completed_normally`, etc.)
is now threaded via three records:

* `SpawnContext` (mutable, owned by orchestrator, populated by Phase C
  callbacks, read by Phase D and Phase E).
* `ExecutionResult` (returned by `run_execution`).
* `LineagePreparationResult` (returned by `run_lineage_preparation`).
* `ClassificationResult` (returned by `run_outcome_classification`).

`complete_failure_with_state` is a free function in `_errors.py`,
callable from Phase A (state_path=None), Phase B (post-identity), and
Phase C (spawn-error gate).

## Public API

The public triple — `execute_dispatch`, `DispatchSpawnFailed`,
`_write_pid` — is re-exported by `fleet/_api.py`. Consumers MUST NOT
import shards directly; per REQ-IMP-001, they import from
`autoskillit.fleet`.

## REQ-CNST-010 budget

Every file in this package is under 750 lines. See the
`tests/arch/test_subpackage_isolation_size.py::test_no_src_module_exceeds_line_limit`
guard. The legacy 1595-line exemption for `fleet/_api.py` (REQ-CNST-010-E6)
was deleted as part of issue #4851.

## IL-009 layer notes

`_pid` and `_cleanup` deferred-import `kill_process_tree` from
`autoskillit.execution` (fail-closed spawn, post-cancel cleanup
respectively). `_validation` deferred-imports
`apply_config_authoritative_overrides` from `autoskillit.config`.
These three transitive imports are exempted from IL-009 via
`pyproject.toml` `ignore_imports`. Do not add new `execution` /
`config` imports from this package without first updating the
`ignore_imports` block.

## Isolation discipline

Tests targeting this package MUST follow the discipline documented in
`tests/AGENTS.md`:

* Use `tool_ctx`, `minimal_ctx`, or `tool_ctx_kitchen_open` fixtures —
  never instantiate `ToolContext` directly.
* Module-level singleton mutations go through `monkeypatch.setattr`,
  never bare assignment.
* Filesystem isolation uses `tmp_path` (RAM-backed on Linux/WSL2).
* `_isolated_home` and `_reset_mcp_visibility` autouse fixtures from
  `tests/conftest.py` apply automatically.
