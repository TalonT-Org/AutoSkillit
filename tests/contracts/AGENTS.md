# contracts/

Protocol satisfaction, package gateway, and skill contract compliance tests.

`_anti_fab_helpers.py` mirrors the production anti-fabrication guard.
`_projection_helpers.py` supplies shared session catalogs and stale snapshots for
plugin-projection contract tests.

## Architecture Notes

`conftest.py` provides `REFUSAL_SIGNALS` constants shared across many contract tests. `_anti_confirm_helpers.py` mirrors the production anti-confirmation regex for structural contract verification.
