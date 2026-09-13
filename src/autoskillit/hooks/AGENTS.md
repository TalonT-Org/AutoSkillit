# hooks/

Claude Code `PreToolUse`/`PostToolUse`/`SessionStart` scripts.
Sub-packages: guards/ (see guards/AGENTS.md), formatters/ (see formatters/AGENTS.md),
_runtime/ (see _runtime/AGENTS.md).

The package initializer performs explicit deferred `HOOK_REGISTRY` population to break an
import cycle (see the top-of-file comment in `hooks/__init__.py`); it is not import-free.

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

`_classification/_tokenizer.py` is the sole general parsing authority for command text
(rectify #4941 Parts A-B): it is the only module that reads a raw command string to derive
segments, redirect syntax, and stdin literals (heredoc/herestring bodies bound to the
segment that consumes them, via `StdinLiteral`). Every other scanner in `_classification/`
and `guards/` must consume `_classification/_interpreters.py`'s
`evaluated_payloads`/`all_evaluated_segments`/`live_command_text`/`interpreter_invokes`
projection — "what will actually execute, and by whom" — rather than re-deriving liveness
by scanning the raw command itself; no guard tokenizes the command itself. Every guard
under `guards/` (git_ops_guard, github_mutation_guard, pr_create_guard,
planner_gh_discovery_guard, artifact_download_guard, test_runner_guard, write_guard,
unsafe_install_guard, resource_exhaustion_guard) and `_classification/_flags.py`'s
protected-path read check now consume this authority instead of a private parser.
`compose_pr_body_guard.py` is the one deliberate exception: it tokenizes each evaluated
payload independently (`all_evaluated_segments` flattens across payloads, which would let a
`$VAR` lookup for one payload resolve from a sibling payload's assignment), a reviewed
entry in `tests/arch/test_hook_raw_command_scan_inventory.py` rather than a silent bypass.
That inventory test makes any new raw scan under `hooks/` a conscious, reviewed diff.
`StdinLiteral` is the second instance of the `ArgvToken` "tag once at tokenization, consume
tagged provenance downstream" pattern (`_tokenizer.py`, commit `6624dda71`, issue #4680):
future token-level provenance should extend this path rather than add a parallel parser.
