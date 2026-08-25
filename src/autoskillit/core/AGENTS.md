# core/

IL-0 foundation layer — zero autoskillit imports; safe for import from hook subprocesses.
Sub-packages: types/ (see types/AGENTS.md) and runtime/ (see runtime/AGENTS.md).

## Architecture Notes

All modules are importable without any `autoskillit` package imports (IL-0 hard constraint).
Production code imports from `autoskillit.core`, not from sub-packages directly.
**Pyright `reportAttributeAccessIssue` on `autoskillit.core` sub-package imports is ALWAYS a real violation.**
The diagnostic means the import bypasses the public gateway — fix the import path to use `autoskillit.core` instead.
Never suppress it. Legitimate `# pyright: ignore[reportAttributeAccessIssue]` suppressions exist only for
dynamic attribute access on lazy-registry objects (see `recipe/__init__.py`, `recipe/_api.py`) and are
governed by an allowlist in `tests/arch/test_pyright_suppression_allowlist.py`.
`_terminal_table.py` is re-exported by `cli/_terminal_table.py` as a shim.

`_release_identity.py` imports `packaging.version.Version` at module load by
requirement. Update transactions cross an irreversible install pivot that can
replace the parent's dependency tree; release-comparison machinery needed after
that pivot must already be imported. Do not make this import lazy.
