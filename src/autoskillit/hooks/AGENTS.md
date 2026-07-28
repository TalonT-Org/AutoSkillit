# hooks/

Claude Code `PreToolUse`/`PostToolUse`/`SessionStart` scripts.
Sub-packages: guards/ (see guards/AGENTS.md), formatters/ (see formatters/AGENTS.md).

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | Package marker (no imports) |
| `_dispatch.py` | Stable hook dispatcher — resolves logical hook names to scripts (stdlib-only, NEVER RENAME) |
| `_hook_settings.py` | Shared stdlib-only settings bridge for quota and output-budget guard hooks |
| `lint_after_edit_hook.py` | `PostToolUse`: runs ruff format+check on `.py` files after Edit/Write |
| `quota_post_hook.py` | Appends quota warning to `run_skill` output |
| `review_gate_post_hook.py` | `PostToolUse`: writes/clears `review_gate_state.json` |
| `token_summary_hook.py` | Appends Token Usage Summary to PR body |
| `session_start_hook.py` | Injects open-kitchen reminder on resume |
| `skill_load_post_hook.py` | `PostToolUse`: writes skill-loaded flag for non-Anthropic provider guard |
| ~~`pipeline_step_post_hook.py`~~ | **RETIRED** — step completion is now server-authoritative (written at `run_skill` adjudication point in `tools_execution.py`) |
| `recipe_confirmed_post_hook.py` | `PostToolUse`: writes recipe-load-confirmed marker after first successful `run_skill` |
| `resume_gate_post_hook.py` | `PostToolUse`: records resume attempts to `resume_gate_state.json` for the reset_dispatch resume gate |
| `quota_guard_state_post_hook.py` | `PostToolUse`: writes / clears the per-session quota-disable marker after `disable_quota_guard` / `close_kitchen` |
| `ingredient_lock_guard.py` | PreToolUse guard script (see guards/AGENTS.md) |
| `_hook_utils.py` | Shared stdlib-only utilities for hook scripts (e.g., `find_project_root`, `STEP_SUFFIX_RE`) |
| `_command_classification.py` | Shared stdlib-only command classification primitives for guard scripts (interpreter/wrapper detection, git command classification) |
| `_capture_contract.py` | Shared stdlib-only shell-capture transport limits and identifier validation |
| `_policy_event.py` | Typed policy-event formatter for hook provenance messages (stdlib-only) |
| `_capture_artifacts.py` | Stdlib-only descriptor-anchored shell-capture authority, runner, and lifecycle context factory |
| `_capture_lifecycle.py` | Stdlib-only root-bound capture lifecycle ledger, writer leases, crash recovery, and quarantine deletion |
| `capture_lifecycle_hook.py` | Cleanup-only `SessionStart`: bounded lifecycle sweep for interactive and headless sessions |
| `shell_capture_hook.py` | `PreToolUse`: input-rewrite hook for Codex shell capture — wraps commands in a lossless capture harness (#4286 / ADR-0006) |

## Architecture Notes

`_dispatch.py` must never be renamed — it is referenced by absolute path in `hooks.json`
and the `HOOK_REGISTRY`. All hook scripts are stdlib-only standalone executables; they do
not import from `autoskillit.*` except via `_dispatch.py`'s path-resolution logic.
Renaming any hook script requires updating `HOOK_REGISTRY` in `hook_registry.py` AND
adding the old basename to `RETIRED_SCRIPT_BASENAMES` in the same commit.
