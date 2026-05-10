# report/

IL-1 subpackage — HTML report renderer for bundle-local-report.

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | Re-exports `renderer` submodule so `autoskillit.report.renderer` is always a valid attribute |
| `renderer.py` | Self-contained HTML renderer; uses `pkg_root()` for mermaid asset resolution |
