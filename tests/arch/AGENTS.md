# arch/

AST enforcement, sub-package layer contracts, and architectural invariant tests.

## Architecture Notes

`conftest.py` provides shared fixtures for AST-based tests. `_helpers.py` contains the shared AST visitor infrastructure used across multiple test files. `_rules.py` defines reusable arch rule tuples. `_deselection.py` provides diff-aware parametrized deselection helpers used by tests that use `pytest.mark.parametrize` over large rule sets.
