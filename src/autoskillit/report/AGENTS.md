# report/

IL-1 subpackage — HTML report renderer for bundle-local-report.

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | Re-exports public symbols from `renderer` (`HTML_TEMPLATE`, `VALIDATION_KEYWORDS`, `main`) |
| `renderer.py` | Self-contained HTML renderer; uses `pkg_root()` for mermaid asset resolution |
