# hooks/

Claude Code `PreToolUse`/`PostToolUse`/`SessionStart` scripts.
Sub-packages: guards/ (see guards/AGENTS.md), formatters/ (see formatters/AGENTS.md).

The package initializer remains import-free.

## Architecture Notes

`_dispatch.py` must never be renamed — it is referenced by absolute path in `hooks.json`
and the `HOOK_REGISTRY`. All hook scripts are stdlib-only standalone executables; they do
not import from `autoskillit.*` except via `_dispatch.py`'s path-resolution logic.
Renaming any hook script requires updating `HOOK_REGISTRY` in `hook_registry.py` AND
adding the old basename to `RETIRED_SCRIPT_BASENAMES` in the same commit.
