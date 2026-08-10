# hooks/

Claude Code `PreToolUse`/`PostToolUse`/`SessionStart` scripts.
Sub-packages: guards/ (see guards/AGENTS.md), formatters/ (see formatters/AGENTS.md).

The package initializer remains import-free.

The `_capture` primitives remain stdlib-only and importable in standalone mode when the
hooks directory alone is supplied on `sys.path`.

## Architecture Notes

`_dispatch.py` must never be renamed — every published hooks.json references it via the
`${CLAUDE_PLUGIN_ROOT}/hooks/_dispatch.py` path, a contract published in the plugin
artifact. All hook scripts are stdlib-only standalone executables; they do not import from
`autoskillit.*` except via `_dispatch.py`'s path-resolution logic.
Renaming any hook script requires updating `HOOK_REGISTRY` in `hook_registry.py` AND
adding the old basename to `RETIRED_SCRIPT_BASENAMES` in the same commit.

Two-form path contract, enforced by `hook_registry._build_hook_command`:

- **hooks.json** (plugin manifest — redistributed via the marketplace/plugin cache):
  always uses the relocatable `${CLAUDE_PLUGIN_ROOT}` token form
  (`hook_registry.PLUGIN_ROOT_TOKEN`), expanded by Claude Code at hook-invocation
  time against the plugin version that supplied the file. This makes hook validity
  a property of the artifact, independent of the venv interpreter, install path, or
  continued existence that generated it.
- **settings.json** (machine-local, dev-mode only, never redistributed): always
  bakes an absolute path via `HOOKS_DIR`. The token never expands there — Claude
  Code only substitutes it for plugin-bundled hooks.json — so a settings.json entry
  containing it is always stale/foreign and is swept by `_evict_stale_autoskillit_hooks`.

Codex's `config.toml` hooks are a separate consumer (`execution/backends/_codex_hooks.py`)
with no expansion-token equivalent; its commands always bake a real absolute path via
`execution.backends._codex_hooks._resolve_codex_hooks_dir()` (retained plugin-cache incarnation when installed,
else the dev-source checkout).
