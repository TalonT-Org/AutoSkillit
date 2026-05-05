# hooks/

Claude Code `PreToolUse`/`PostToolUse`/`SessionStart` scripts.
Sub-packages: guards/ (see guards/CLAUDE.md), formatters/ (see formatters/CLAUDE.md).

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | Package marker (no imports) |
| `_dispatch.py` | Stable hook dispatcher — resolves logical hook names to scripts (stdlib-only, NEVER RENAME) |
| `_hook_settings.py` | Shared stdlib-only settings resolver for quota guard hooks |
| `lint_after_edit_hook.py` | `PostToolUse`: runs ruff format+check on `.py` files after Edit/Write |
| `quota_post_hook.py` | Appends quota warning to `run_skill` output |
| `review_gate_post_hook.py` | `PostToolUse`: writes/clears `review_gate_state.json` |
| `token_summary_hook.py` | Appends Token Usage Summary to PR body |
| `session_start_hook.py` | Injects open-kitchen reminder on resume |
| `skill_load_post_hook.py` | `PostToolUse`: writes skill-loaded flag for non-Anthropic provider guard |

## Architecture Notes

`_dispatch.py` must never be renamed — it is referenced by absolute path in `hooks.json`
and the `HOOK_REGISTRY`. All hook scripts are stdlib-only standalone executables; they do
not import from `autoskillit.*` except via `_dispatch.py`'s path-resolution logic.
Renaming any hook script requires updating `HOOK_REGISTRY` in `hook_registry.py` AND
adding the old basename to `RETIRED_SCRIPT_BASENAMES` in the same commit.
