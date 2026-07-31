# fleet/

Fleet campaign dispatch, state persistence, and sidecar tests.

## Architecture Notes

`conftest.py` and `_helpers.py` provide shared fixtures and helper factories for fleet tests. `test_helpers_exports.py` guards that `_helpers` is importable from other test modules.
